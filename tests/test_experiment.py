from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import csv
import json
import unittest

from gooddomains import checks, generate, store
from gooddomains.ranking import normalize


class ExperimentTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.context = store.connect(self.root / 'index.db')
        self.db = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)

    def generate(self, count=300):
        return generate.generate(self.db, count=count, run='pilot', seed=42)

    def csv(self, rows):
        path = self.root / 'results.csv'
        with path.open('w', newline='') as f:
            writer = csv.DictWriter(f, sorted(checks.FIELDS))
            writer.writeheader()
            writer.writerows(rows)
        return path

    def observation(self, **kw):
        return dict(domain='anchor.com', status='available', price='14.98', currency='USD',
                    checked_at=store.now(), term_months='12', **kw)

    def test_generation_is_deterministic_valid_and_tracks_provenance(self):
        result = self.generate()
        self.assertEqual(result['generated'], 300)
        first = [dict(r) for r in self.db.execute('SELECT * FROM candidates WHERE run=? ORDER BY domain', ('pilot',))]
        generate.generate(self.db, count=300, run='replica', seed=42)
        second = [dict(r) for r in self.db.execute('SELECT * FROM candidates WHERE run=? ORDER BY domain', ('replica',))]
        self.assertEqual([r['domain'] for r in first], [r['domain'] for r in second])
        self.assertEqual({r['strategy'] for r in first}, {'compound', 'metaphor', 'uncommon', 'invented'})
        self.assertEqual({r['brief'] for r in first}, set(generate.BRIEFS))
        for row in first:
            self.assertEqual(normalize(row['domain']), row['domain'])
            metadata = json.loads(row['components'])
            self.assertEqual(''.join(metadata['parts']) + '.com', row['domain'])
            if row['strategy'] == 'invented':
                self.assertGreaterEqual(len(row['domain'][:-4]), 6)
        self.assertEqual(store.listing(self.db, availability='unchecked')['total'], 300)

    def test_same_run_cannot_silently_change(self):
        self.generate()
        with self.assertRaises(ValueError):
            self.generate()

    def test_export_balance_cap_reproducibility_and_no_claims(self):
        self.generate(1000)
        output = self.root / 'sample'
        report = generate.export_check(self.db, run='pilot', output=output, count=100, max_component=3)
        self.assertEqual(report['exported'], 100)
        manifest = json.loads((output / 'manifest.json').read_text())
        names = (output / 'domains.txt').read_text().splitlines()
        self.assertEqual(len(set(names)), 100)
        self.assertEqual(Counter(r['strategy'] for r in manifest['candidates']),
                         {'compound': 25, 'metaphor': 25, 'uncommon': 25, 'invented': 25})
        usage = Counter(p for r in manifest['candidates'] for p in set(r['diversity_parts']))
        self.assertLessEqual(max(usage.values()), 3)
        rows = generate.select_batch(self.db, run='pilot', count=100, max_component=3)
        self.assertEqual(names, [r['domain'] for r in rows])
        review = (output / 'review.md').read_text()
        self.assertNotIn('heuristic-v1', review)
        self.assertNotIn('invented', review)
        self.assertIn('unchecked', review)
        result = checks.import_checks(self.db, output / 'results-template.csv', provider='test', manifest=output / 'manifest.json')
        self.assertEqual(result['imported'], 0)
        self.assertEqual(result['blank_rows_skipped'], 100)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM checks').fetchone()[0], 0)
        with self.assertRaises(FileExistsError):
            generate.export_check(self.db, run='pilot', output=output, count=100)

    def test_rejected_names_not_exported(self):
        self.generate(100)
        domain = self.db.execute('SELECT domain FROM candidates LIMIT 1').fetchone()[0]
        store.review_domain(self.db, domain, 'reject')
        rows = generate.select_batch(self.db, run='pilot', count=100)
        self.assertEqual(len(rows), 99)
        self.assertNotIn(domain, [r['domain'] for r in rows])

    def test_small_pool_reports_shortfall(self):
        self.generate(12)
        result = generate.export_check(self.db, run='pilot', output=self.root / 'small', count=100)
        self.assertTrue(result['underfilled'])
        self.assertEqual(result['exported'], 12)
        with self.assertRaises(ValueError):
            generate.select_batch(self.db, run='pilot', count=5001)

    def test_results_are_idempotent_and_separate_from_asking_prices(self):
        path = self.csv([self.observation()])
        self.assertEqual(checks.import_checks(self.db, path, provider='test')['imported'], 1)
        self.assertEqual(checks.import_checks(self.db, path, provider='test')['unchanged'], 1)
        self.assertEqual(store.listing(self.db, budget=20)['total'], 0)
        result = store.listing(self.db, budget=20, price_type='registration', availability='available')
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['items'][0]['latest_check']['term_months'], 12)

    def test_newer_failure_supersedes_older_available_result(self):
        old = self.observation()
        old['checked_at'] = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        checks.import_checks(self.db, self.csv([old]), provider='first')
        new = dict(domain='anchor.com', status='unknown', checked_at=store.now())
        checks.import_checks(self.db, self.csv([new]), provider='second')
        self.assertEqual(store.listing(self.db, availability='available')['total'], 0)
        self.assertEqual(store.listing(self.db, availability='unknown')['total'], 1)
        self.assertEqual(store.listing(self.db, budget=5000, price_type='registration')['total'], 0)
        # Re-importing older evidence from another provider cannot resurrect availability.
        checks.import_checks(self.db, self.csv([old]), provider='third')
        self.assertEqual(store.listing(self.db, availability='unknown')['total'], 1)

    def test_stale_and_unchecked_are_distinct(self):
        old = self.observation()
        old['checked_at'] = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
        checks.import_checks(self.db, self.csv([old]), provider='test')
        store.put(self.db, 'prism.com', source='candidate')
        self.assertEqual(store.listing(self.db, availability='stale')['total'], 1)
        self.assertEqual(store.listing(self.db, availability='unchecked')['total'], 1)
        self.assertEqual(store.listing(self.db, availability='available')['total'], 0)

    def test_non_usd_and_premium_not_misclassified(self):
        euro = self.observation()
        euro['currency'] = 'EUR'
        premium = dict(domain='prism.com', status='premium', price='500', currency='USD', checked_at=store.now())
        checks.import_checks(self.db, self.csv([euro, premium]), provider='test')
        self.assertEqual(store.listing(self.db, budget=5000, price_type='registration')['total'], 0)
        self.assertEqual(store.listing(self.db, availability='premium')['total'], 1)

    def test_malformed_import_is_atomic(self):
        bad = self.observation()
        bad.update(domain='prism.com', status='unavailable')
        with self.assertRaisesRegex(ValueError, 'CSV line 3'):
            checks.import_checks(self.db, self.csv([self.observation(), bad]), provider='test')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM checks').fetchone()[0], 0)
        self.assertEqual(store.stats(self.db)['domains'], 0)

    def test_manifest_restricts_import_scope(self):
        self.generate(12)
        output = self.root / 'scope'
        generate.export_check(self.db, run='pilot', output=output, count=12)
        observation = self.observation()
        observation['domain'] = 'notinthisbatch.com'
        with self.assertRaisesRegex(ValueError, 'manifest'):
            checks.import_checks(self.db, self.csv([observation]), provider='test', manifest=output / 'manifest.json')

    def test_conflicting_timestamp_is_rejected(self):
        row = self.observation()
        checks.import_checks(self.db, self.csv([row]), provider='test')
        row['price'] = '99'
        with self.assertRaisesRegex(ValueError, 'Conflicting'):
            checks.import_checks(self.db, self.csv([row]), provider='test')
        self.assertEqual(store.listing(self.db)['items'][0]['latest_check']['price'], 14.98)

    def test_rejects_ambiguous_or_invalid_evidence(self):
        for mutation in [{'status': 'taken'}, {'price': 'nan'}, {'price': '0'}, {'currency': ''},
                         {'checked_at': '2099-01-01T00:00:00Z'}, {'checked_at': '2026-01-01'},
                         {'term_months': ''}, {'term_months': 'monthly'}, {'term_months': '0'},
                         {'status': 'unknown'}, {'evidence_url': 'javascript:alert(1)'}]:
            row = self.observation()
            row.update(mutation)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                checks.validate(row)

    def test_report_uses_only_recent_observed_results(self):
        self.generate(12)
        row = self.db.execute('SELECT * FROM candidates LIMIT 1').fetchone()
        result = self.observation()
        result['domain'] = row['domain']
        checks.import_checks(self.db, self.csv([result]), provider='test')
        store.review_domain(self.db, row['domain'], 'keep')
        report = checks.report(self.db, run='pilot')
        group = report['groups'][row['brief'] + '/' + row['strategy']]
        self.assertEqual(group['available'], 1)
        self.assertEqual(group['availability_rate'], 1)
        self.assertEqual(group['kept_available_per_1000_checks'], 1000)
        self.assertEqual(sum(v['unchecked'] for v in report['groups'].values()), 11)


if __name__ == '__main__':
    unittest.main()
