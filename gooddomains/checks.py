"""Import explicitly observed registrar results, never infer availability from absence."""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
import csv
import json
import math
import re

from . import store
from .ranking import normalize

STATUSES = {'available', 'registered', 'premium', 'unknown'}
FIELDS = {'domain', 'status', 'price', 'currency', 'checked_at', 'term_months', 'evidence_url'}


def validate(row):
    domain = normalize(row.get('domain') or '')
    status = (row.get('status') or '').strip().lower()
    if status not in STATUSES:
        raise ValueError('status must be available, registered, premium, or unknown')
    value = (row.get('checked_at') or '').strip()
    if not value:
        raise ValueError('checked_at is required, even for unknown results')
    timestamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if timestamp.tzinfo is None or timestamp > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise ValueError('checked_at must have a timezone and must not be in the future')
    timestamp = timestamp.astimezone(timezone.utc).isoformat(timespec='seconds')
    price = (row.get('price') or '').strip()
    currency = (row.get('currency') or '').strip().upper()
    term = (row.get('term_months') or '').strip()
    if price:
        price = float(price)
        if not math.isfinite(price) or price <= 0:
            raise ValueError('price must be a positive finite number without currency symbols')
        if status not in {'available', 'premium'}:
            raise ValueError('Only available or premium results can carry purchase prices')
        if not re.fullmatch('[A-Z]{3}', currency):
            raise ValueError('An explicit three-letter currency is required with a price')
        if status == 'available' and not term:
            raise ValueError('Registration quotes require term_months')
    else:
        price = None
        if currency:
            raise ValueError('currency requires a price')
    term = int(term) if term else None
    if term is not None and (not 1 <= term <= 120 or status != 'available'):
        raise ValueError('term_months must be 1–120 and applies only to registration quotes')
    url = (row.get('evidence_url') or '').strip()
    if url and not url.startswith(('http://', 'https://')):
        raise ValueError('evidence_url must be HTTP(S)')
    return dict(domain=domain, status=status, price=price, currency=currency or None,
                checked_at=timestamp, term_months=term, evidence_url=url or None)


def import_checks(db, path, *, provider, manifest=None):
    if not provider.strip():
        raise ValueError('A provider is required')
    allowed = None
    if manifest:
        payload = json.loads(Path(manifest).read_text())
        allowed = {normalize(r['domain']) for r in payload['candidates']}
    accepted = unchanged = blank = 0
    # Atomic import even if called by a caller who catches a validation error.
    db.execute('SAVEPOINT import_checks')
    try:
        with open(path, newline='', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames or not {'domain', 'status', 'checked_at'}.issubset(reader.fieldnames):
                raise ValueError('Expected normalized CSV with domain,status,checked_at headers; see docs/bulk-check.md')
            if set(reader.fieldnames) - FIELDS:
                raise ValueError('Unrecognized CSV headers; map the registrar output to the documented template first')
            for line, raw in enumerate(reader, 2):
                try:
                    if None in raw:
                        raise ValueError('Too many CSV columns')
                    # Untouched template rows do not become checks or unknown observations.
                    if not any((raw.get(k) or '').strip() for k in FIELDS - {'domain'}):
                        blank += 1
                        continue
                    row = validate(raw)
                    if allowed is not None and row['domain'] not in allowed:
                        raise ValueError('Domain is not in this batch manifest')
                    previous = db.execute('SELECT * FROM checks WHERE domain=? AND provider=? AND checked_at=?',
                                          (row['domain'], provider, row['checked_at'])).fetchone()
                    if previous:
                        if any(previous[k] != v for k, v in row.items()):
                            raise ValueError('Conflicting observation for the same domain/provider/timestamp')
                        unchanged += 1
                        continue
                    if not db.execute('SELECT 1 FROM domains WHERE domain=?', (row['domain'],)).fetchone():
                        store.put(db, row['domain'], source=f'check:{provider}')
                    db.execute('''INSERT INTO checks(domain,provider,status,price,currency,checked_at,
                        imported_at,evidence_url,term_months) VALUES(?,?,?,?,?,?,?,?,?)''',
                        (row['domain'], provider, row['status'], row['price'], row['currency'], row['checked_at'],
                         store.now(), row['evidence_url'], row['term_months']))
                    accepted += 1
                except (ValueError, TypeError) as exc:
                    raise ValueError(f'CSV line {line}: {exc}') from exc
        db.execute('RELEASE import_checks')
    except Exception:
        db.execute('ROLLBACK TO import_checks')
        db.execute('RELEASE import_checks')
        raise
    return {'imported': accepted, 'unchanged': unchanged, 'blank_rows_skipped': blank,
            'provider': provider, 'missing_domains': 'left unchanged; never inferred as unavailable'}


def report(db, *, run, days=30):
    if not 1 <= days <= 3650:
        raise ValueError('days must be 1–3650')
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec='seconds')
    groups = defaultdict(lambda: dict(candidates=0, checked=0, resolved=0, available=0,
                                      registered=0, premium=0, unknown=0, stale=0, unchecked=0,
                                      kept_available=0))
    rows = db.execute('''SELECT g.brief,g.strategy,d.review,c.status,c.checked_at
        FROM candidates g JOIN domains d ON d.domain=g.domain
        LEFT JOIN latest_checks c ON c.domain=g.domain WHERE g.run=?''', (run,))
    for row in rows:
        metrics = groups[row['brief'] + '/' + row['strategy']]
        metrics['candidates'] += 1
        if row['status'] is None:
            metrics['unchecked'] += 1
        elif row['checked_at'] < cutoff:
            metrics['stale'] += 1
        else:
            metrics['checked'] += 1
            metrics[row['status']] += 1
            metrics['resolved'] += row['status'] != 'unknown'
            metrics['kept_available'] += row['status'] == 'available' and row['review'] == 'keep'
    if not groups:
        raise ValueError('No candidates for this run')
    for values in groups.values():
        values['availability_rate'] = round(values['available'] / values['resolved'], 4) if values['resolved'] else None
        values['kept_available_per_1000_checks'] = round(1000 * values['kept_available'] / values['checked'], 1) if values['checked'] else None
    return {'run': run, 'freshness_days': days, 'groups': dict(groups),
            'note': 'Rates describe imported results only. Partial or available-only exports bias them. Keep/pass is global, not brief-specific.'}
