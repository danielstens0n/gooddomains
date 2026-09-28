"""Durable, review-independent registrar queues and a read-only daily worker."""
from collections import Counter, defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
import csv
import hashlib
import json
import os
import random
import re
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from . import checks, store
from .ranking import normalize

API = 'https://spaceship.dev/api/v1/domains/available'


def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS scan_runs (
        run TEXT PRIMARY KEY, created_at TEXT NOT NULL, requested INTEGER NOT NULL,
        seed INTEGER NOT NULL, selected INTEGER NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS scan_items (
        run TEXT NOT NULL REFERENCES scan_runs(run), domain TEXT NOT NULL REFERENCES domains(domain),
        position INTEGER NOT NULL, brief TEXT NOT NULL, strategy TEXT NOT NULL,
        reason TEXT NOT NULL, check_id INTEGER REFERENCES checks(id),
        PRIMARY KEY(run,domain), UNIQUE(run,position))''')


def plan(db, *, run, count=1000, seed=42):
    """Freeze a queue: up to 20% refreshes; balanced ranked/exploration discovery."""
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', run) or not 1 <= count <= 25000:
        raise ValueError('Run must be a short identifier; count must be 1–25000')
    schema(db)
    previous = db.execute('SELECT * FROM scan_runs WHERE run=?', (run,)).fetchone()
    if previous:
        if previous['requested'] != count or previous['seed'] != seed:
            raise ValueError('Existing run has different settings; resume it or use a new run ID')
        return report(db, run=run)
    now = datetime.now(timezone.utc)
    groups, due, seen = defaultdict(list), [], set()
    rows = db.execute('''SELECT d.domain,c.brief,c.strategy,r.score,k.status,k.checked_at
        FROM domains d JOIN candidates c ON c.domain=d.domain
        JOIN rankings r ON r.domain=d.domain AND r.profile=c.brief
        LEFT JOIN latest_checks k ON k.domain=d.domain
        WHERE NOT EXISTS (SELECT 1 FROM scan_items i WHERE i.domain=d.domain AND i.check_id IS NULL)
        ORDER BY d.domain,c.run''')
    for source in rows:
        row = dict(source)
        if row['domain'] in seen:
            continue
        seen.add(row['domain'])
        if row['status'] is None:
            groups[row['brief'], row['strategy']].append(row)
        else:
            days = 30 if row['status'] == 'registered' else 1
            if datetime.fromisoformat(row['checked_at']) <= now - timedelta(days=days):
                row['reason'] = 'refresh'
                due.append(row)
    due.sort(key=lambda r: (r['status'] not in ('available', 'premium'), r['checked_at'], r['domain']))
    selected = due[:count // 5]
    queues = {}
    for key, rows in sorted(groups.items()):
        rng = random.Random(f'{seed}:{run}:{key}')
        rng.shuffle(rows)
        ranked = sorted(rows, key=lambda r: -r['score'])
        queues[key] = (deque(ranked), deque(rows))
    used = {r['domain'] for r in selected}
    rounds = 0
    while queues and len(selected) < count:
        for key in list(queues):
            queue = queues[key][int(rounds % 5 == 4)]
            while queue and queue[0]['domain'] in used:
                queue.popleft()
            if not queue:
                del queues[key]
                continue
            row = queue.popleft()
            row['reason'] = 'exploration' if rounds % 5 == 4 else 'ranked'
            used.add(row['domain'])
            selected.append(row)
            if len(selected) == count:
                break
        rounds += 1
    # Once discovery is exhausted, spend the remaining budget on due refreshes.
    selected.extend(r for r in due if r['domain'] not in used)
    selected = selected[:count]
    db.execute('INSERT INTO scan_runs VALUES(?,?,?,?,?)', (run, store.now(), count, seed, len(selected)))
    db.executemany('INSERT INTO scan_items VALUES(?,?,?,?,?,?,NULL)',
                   [(run, r['domain'], i, r['brief'], r['strategy'], r['reason']) for i, r in enumerate(selected)])
    return report(db, run=run)


def pending(db, *, run, limit=20):
    schema(db)
    if not db.execute('SELECT 1 FROM scan_runs WHERE run=?', (run,)).fetchone():
        raise ValueError('Unknown scan run')
    if not 1 <= limit <= 25000:
        raise ValueError('limit must be 1–25000')
    return [r[0] for r in db.execute('''SELECT domain FROM scan_items
        WHERE run=? AND check_id IS NULL ORDER BY position LIMIT ?''', (run, limit))]


def normalize_response(payload):
    """Accept saved MCP or REST results; absent/error entries are unknown."""
    requested = payload['requested']
    if not isinstance(requested, list) or not 1 <= len(requested) <= 20:
        raise ValueError('Each response must identify 1–20 requested domains')
    requested = [normalize(d) for d in requested]
    if len(set(requested)) != len(requested):
        raise ValueError('Duplicate requested domain')
    results = payload.get('results', payload.get('domains'))
    if not isinstance(results, list):
        raise ValueError('Expected a results or domains list')
    by_domain = {}
    for item in results:
        domain = normalize(item['domain'])
        if domain not in requested or domain in by_domain:
            raise ValueError('Unexpected or duplicate response domain')
        by_domain[domain] = item
    normalized = []
    for domain in requested:
        item = by_domain.get(domain, {})
        status = {'available': 'available', 'taken': 'registered'}.get(item.get('result'), 'unknown')
        price = item.get('price') or {}
        if status == 'available' and (price.get('isPremium') or item.get('premiumPricing')):
            status = 'premium'
        row = dict(domain=domain, status=status, checked_at=payload['checked_at'], evidence_url=API)
        # REST responses often omit a standard registration price. Do not
        # substitute the MCP quote or a generic advertised .com price.
        if status in ('available', 'premium') and price and (status == 'premium' or price.get('pricedYears')):
            row.update(price=str(price['amount']), currency=price['currency'])
            if status == 'available':
                years = price['pricedYears']
                if isinstance(years, bool) or not isinstance(years, int) or years < 1:
                    raise ValueError('pricedYears must be a positive integer')
                row['term_months'] = str(12 * years)
        # validate all rows before any database change
        checks.validate(row)
        normalized.append(row)
    return normalized


def import_response(db, *, run, payload):
    schema(db)
    rows = normalize_response(payload)
    allowed = {r[0] for r in db.execute('SELECT domain FROM scan_items WHERE run=?', (run,))}
    if not all(r['domain'] in allowed for r in rows):
        raise ValueError('Response does not belong to this run')
    with tempfile.NamedTemporaryFile(mode='w+', suffix='.csv', newline='') as f:
        writer = csv.DictWriter(f, sorted(checks.FIELDS))
        writer.writeheader()
        writer.writerows(rows)
        f.flush()
        result = checks.import_checks(db, f.name, provider='spaceship')
    for row in rows:
        timestamp = checks.validate(row)['checked_at']
        db.execute('''UPDATE scan_items SET check_id=(SELECT id FROM checks
            WHERE domain=? AND provider='spaceship' AND checked_at=?)
            WHERE run=? AND domain=? AND check_id IS NULL''',
            (row['domain'], timestamp, run, row['domain']))
    return result


def report(db, *, run):
    schema(db)
    info = db.execute('SELECT * FROM scan_runs WHERE run=?', (run,)).fetchone()
    if not info:
        raise ValueError('Unknown scan run')
    groups = defaultdict(Counter)
    totals = Counter()
    for r in db.execute('''SELECT i.brief,i.strategy,k.status FROM scan_items i
            LEFT JOIN checks k ON k.id=i.check_id WHERE i.run=?''', (run,)):
        status = r['status'] or 'pending'
        groups[r['brief'] + '/' + r['strategy']][status] += 1
        totals[status] += 1
    return dict(info) | dict(totals=totals, groups=dict(groups),
                              complete=not totals['pending'],
                              note='Availability yield is not human-rated naming quality.')


def export_results(db, *, run, output):
    """Export scan evidence and a small blind review sample."""
    result = report(db, run=run)
    root = Path(output)
    root.mkdir(parents=True, exist_ok=True)
    rows = [dict(r) for r in db.execute('''SELECT i.domain,i.brief,i.strategy,i.reason,
        c.status,c.price,c.currency,c.term_months,c.checked_at,r.score
        FROM scan_items i LEFT JOIN checks c ON c.id=i.check_id
        JOIN rankings r ON r.domain=i.domain AND r.profile=i.brief
        WHERE i.run=? ORDER BY i.position''', (run,))]
    fields = ['domain', 'brief', 'strategy', 'reason', 'status', 'price', 'currency',
              'term_months', 'checked_at', 'score']
    for name, selected in [('results', rows), ('available', [r for r in rows if r['status'] == 'available'])]:
        with (root / (name + '.csv')).open('w', newline='') as f:
            writer = csv.DictWriter(f, fields)
            writer.writeheader()
            writer.writerows(selected)
    groups = defaultdict(list)
    for r in rows:
        if r['status'] == 'available':
            groups[r['brief'], r['strategy']].append(r)
    sample = []
    for key, values in sorted(groups.items()):
        random.Random(f'{run}:{key}:review').shuffle(values)
        sample.extend(values[:10])
    random.Random(run + ':review').shuffle(sample)
    review = ('# Available-name review sample\n\nRandom sample: up to 10 per brief/strategy cohort. '
              'Unreviewed is unknown. These are observed available names, not an endorsed shortlist. '
              'Recheck finalists before purchase. This report reflects this scan, not later observations.\n\n'
              '| Name | Intended impression | Keep / pass / unsure | Why? |\n|---|---|---|---|\n')
    review += ''.join(f"| {r['domain']} | {r['brief']} | | |\n" for r in sample)
    # Preserve any human edits when re-exporting the run.
    if not (root / 'review.md').exists():
        (root / 'review.md').write_text(review)
        (root / 'review-sample.json').write_text(json.dumps(sample, indent=2) + '\n')
    (root / 'report.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


def request_batch(domains, *, opener=urlopen, sleeper=time.sleep):
    key, secret = os.environ.get('SPACESHIP_API_KEY'), os.environ.get('SPACESHIP_API_SECRET')
    if not key or not secret:
        raise ValueError('Set SPACESHIP_API_KEY and SPACESHIP_API_SECRET in the worker environment')
    request = Request(API, data=json.dumps({'domains': domains}).encode(), method='POST',
                      headers={'X-API-Key': key, 'X-API-Secret': secret, 'Content-Type': 'application/json'})
    for attempt in range(4):
        try:
            with opener(request, timeout=30) as response:
                body = json.load(response)
            return dict(requested=domains, checked_at=store.now(), domains=body['domains'])
        except HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 3:
                raise ValueError(f'Registrar HTTP {exc.code}; pending queue preserved') from None
            try:
                delay = float(exc.headers.get('Retry-After', 2 ** (attempt + 1)))
            except (ValueError, TypeError):
                delay = 30
            if delay > 60:
                raise ValueError('Registrar requested a longer pause; rerun later to resume') from None
            sleeper(max(delay, 1.2))
        except (URLError, TimeoutError):
            if attempt == 3:
                raise ValueError('Registrar connection failed; pending queue preserved') from None
            sleeper(2 ** (attempt + 1))


def work(db_path, *, run, count=1000, output='data/private/scans', fetch=request_batch, sleeper=time.sleep):
    """One process per database; commit after each response; raw evidence first."""
    try:
        import fcntl
    except ImportError:
        raise ValueError('The unattended worker currently requires macOS or Linux for process locking') from None
    lock_path = Path(str(db_path) + '.scan.lock')
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Another scan worker is already running') from None
        with store.connect(db_path) as db:
            plan(db, run=run, count=count)
        root = Path(output) / run
        root.mkdir(parents=True, exist_ok=True)
        # Replay evidence saved before an interrupted transaction.
        for path in sorted(root.glob('batch-*.json')):
            with store.connect(db_path) as db:
                import_response(db, run=run, payload=json.loads(path.read_text()))
        while True:
            with store.connect(db_path) as db:
                names = pending(db, run=run)
            if not names:
                break
            payload = fetch(names)
            normalize_response(payload)
            if payload['requested'] != names:
                raise ValueError('Worker response does not match requested batch')
            content = json.dumps(payload, indent=2) + '\n'
            filename = 'batch-' + hashlib.sha256(content.encode()).hexdigest()[:24] + '.json'
            temp = root / (filename + '.tmp')
            temp.write_text(content)
            temp.replace(root / filename)
            with store.connect(db_path) as db:
                import_response(db, run=run, payload=payload)
            sleeper(1.2)
        with store.connect(db_path) as db:
            result = export_results(db, run=run, output=root)
        return result
