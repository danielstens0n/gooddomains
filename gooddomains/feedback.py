"""A transparent check queue guided by explicit reviews, not a trained model."""
from collections import Counter
from pathlib import Path
import json

from . import store


def export(db, *, output, count=200, max_component=12):
    if not 1 <= count <= 5000 or max_component < 1:
        raise ValueError('Count must be 1–5000 and max_component positive')
    reviews = [dict(r) for r in db.execute(
        'SELECT domain,review,ranking FROM domains WHERE review IS NOT NULL ORDER BY domain')]
    kept = [r for r in reviews if r['review'] == 'keep']
    if not kept:
        raise ValueError('Keep some names before exporting a feedback batch')
    positive, negative = Counter(), Counter()
    for row in reviews:
        words = set(json.loads(row['ranking'])['words'])
        (positive if row['review'] == 'keep' else negative).update(words)
    candidates = []
    for r in db.execute('''SELECT d.domain,d.score,c.components FROM domains d
            JOIN candidates c ON c.domain=d.domain
            WHERE d.review IS NULL AND NOT EXISTS
                (SELECT 1 FROM checks k WHERE k.domain=d.domain)
            ORDER BY d.domain,c.run'''):
        parts = json.loads(r['components'])['parts']
        matches = sorted(set(parts) & positive.keys())
        avoided = sorted(set(parts) & negative.keys())
        # Small bounded adjustment: repeated keeps strengthen a component, but
        # familiar base-name quality still matters. No review is inferred.
        bonus = min(15, sum(min(5, positive[w]) * 3 for w in matches))
        penalty = min(15, sum(min(5, negative[w]) * 3 for w in avoided))
        candidates.append(dict(domain=r['domain'], parts=parts, matched_keeps=matches,
                               matched_rejects=avoided, base_score=r['score'],
                               queue_score=round(r['score'] + bonus - penalty, 1)))
    candidates.sort(key=lambda r: (-r['queue_score'], r['domain']))
    usage, seen, selected = Counter(), set(), []
    for row in candidates:
        if row['domain'] in seen or any(usage[p] >= max_component for p in set(row['parts'])):
            continue
        seen.add(row['domain'])
        usage.update(set(row['parts']))
        selected.append(row)
        if len(selected) == count:
            break
    root = Path(output)
    root.mkdir(parents=True, exist_ok=False)
    payload = dict(created_at=store.now(), method='review-components-v1',
                   caveat='A selection heuristic, not a learned preference model or availability prediction.',
                   requested=count, selected=len(selected), max_component=max_component,
                   reviews=[dict(domain=r['domain'], review=r['review']) for r in reviews],
                   candidates=selected)
    (root / 'manifest.json').write_text(json.dumps(payload, indent=2) + '\n')
    (root / 'domains.txt').write_text(''.join(r['domain'] + '\n' for r in selected))
    return dict(output=str(root), selected=len(selected), keeps=len(kept),
                rejects=len(reviews) - len(kept))
