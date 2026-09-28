from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.error import HTTPError
import json
import unittest

from gooddomains import daily, generate, store


class DailyTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'index.db'
        with store.connect(self.path) as db:
            generate.generate(db, count=300, run='pool')

    def response(self, names, result='available'):
        return dict(requested=names, checked_at=store.now(), results=[
            dict(domain=n, result=result, premiumPricing=[],
                 price=dict(amount=9.08, currency='USD', pricedYears=1, isPremium=False))
            for n in names])

    def test_plan_is_balanced_immutable_and_independent_of_reviews(self):
        with store.connect(self.path) as db:
            first = daily.plan(db, run='first', count=120)
            self.assertEqual(len(first['groups']), 12)
            names = daily.pending(db, run='first', limit=120)
            db.execute("UPDATE domains SET review='reject'")
            self.assertEqual(daily.plan(db, run='first', count=120), first)
            self.assertEqual(daily.pending(db, run='first', limit=120), names)
            with self.assertRaises(ValueError):
                daily.plan(db, run='first', count=10)
            daily.plan(db, run='second', count=120)
            self.assertFalse(set(names) & set(daily.pending(db, run='second', limit=120)))

    def test_response_missing_error_and_premium_semantics(self):
        payload = self.response(['example.com', 'sample.com', 'domain.com'])
        payload['results'] = [dict(domain='example.com', result='available', premiumPricing=[
            dict(operation='register', price=1000, currency='USD')]),
            dict(domain='sample.com', result='unexpectedError')]
        rows = daily.normalize_response(payload)
        self.assertEqual([r['status'] for r in rows], ['premium', 'unknown', 'unknown'])
        self.assertTrue(all('price' not in r for r in rows))
        payload['results'].append(dict(domain='outside.com', result='taken'))
        with self.assertRaises(ValueError):
            daily.normalize_response(payload)

    def test_import_resume_idempotency_and_atomic_validation(self):
        with store.connect(self.path) as db:
            daily.plan(db, run='pilot', count=40)
            names = daily.pending(db, run='pilot')
            payload = self.response(names)
            broken = self.response(names)
            broken['results'][-1]['price']['amount'] = -1
            with self.assertRaises(ValueError):
                daily.import_response(db, run='pilot', payload=broken)
            self.assertEqual(len(daily.pending(db, run='pilot', limit=40)), 40)
            self.assertEqual(db.execute('SELECT count(*) FROM checks').fetchone()[0], 0)
            daily.import_response(db, run='pilot', payload=payload)
            daily.import_response(db, run='pilot', payload=payload)
            self.assertEqual(db.execute('SELECT count(*) FROM checks').fetchone()[0], 20)
            self.assertEqual(daily.report(db, run='pilot')['totals'], {'available': 20, 'pending': 20})
            self.assertFalse(set(names) & set(daily.pending(db, run='pilot')))

    def test_due_refreshes_and_no_price_invention(self):
        with store.connect(self.path) as db:
            daily.plan(db, run='first', count=20)
            names = daily.pending(db, run='first')
            payload = self.response(names)
            payload['checked_at'] = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
            for item in payload['results']:
                del item['price']
            daily.import_response(db, run='first', payload=payload)
            self.assertTrue(all(r[0] is None for r in db.execute('SELECT price FROM checks')))
            daily.plan(db, run='next', count=20)
            self.assertEqual(db.execute("SELECT count(*) FROM scan_items WHERE run='next' AND reason='refresh'").fetchone()[0], 4)

    def test_worker_recovers_after_network_failure(self):
        calls = []
        def fetch(names):
            calls.append(names)
            if len(calls) == 2:
                raise ValueError('network failure')
            return self.response(names)
        output = Path(self.temp.name) / 'scans'
        with self.assertRaises(ValueError):
            daily.work(self.path, run='worker', count=40, output=output, fetch=fetch, sleeper=lambda _: None)
        result = daily.work(self.path, run='worker', count=40, output=output,
                            fetch=self.response, sleeper=lambda _: None)
        self.assertTrue(result['complete'])
        self.assertEqual(result['totals'], {'available': 40})
        with store.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM checks').fetchone()[0], 40)

    def test_rate_limit_backoff_and_no_auth_retry(self):
        calls, waits = [], []
        def opener(*args, **kwargs):
            calls.append(1)
            raise HTTPError(daily.API, 429, 'rate limit', {'Retry-After': '3'}, None)
        with patch.dict('os.environ', {'SPACESHIP_API_KEY': 'test', 'SPACESHIP_API_SECRET': 'test'}):
            with self.assertRaisesRegex(ValueError, 'HTTP 429'):
                daily.request_batch(['example.com'], opener=opener, sleeper=waits.append)
        self.assertEqual(len(calls), 4)
        self.assertEqual(waits, [3, 3, 3])
        def unauthorized(*args, **kwargs):
            raise HTTPError(daily.API, 401, 'unauthorized', {}, None)
        with patch.dict('os.environ', {'SPACESHIP_API_KEY': 'test', 'SPACESHIP_API_SECRET': 'test'}):
            with self.assertRaisesRegex(ValueError, 'HTTP 401'):
                daily.request_batch(['example.com'], opener=unauthorized, sleeper=waits.append)
        self.assertEqual(waits, [3, 3, 3])

    def test_export_preserves_human_edits_and_reports_all_outcomes(self):
        with store.connect(self.path) as db:
            daily.plan(db, run='report', count=20)
            names = daily.pending(db, run='report')
            payload = self.response(names)
            payload['results'][0]['result'] = 'taken'
            daily.import_response(db, run='report', payload=payload)
            output = Path(self.temp.name) / 'report'
            daily.export_results(db, run='report', output=output)
            (output / 'review.md').write_text('My judgments')
            daily.export_results(db, run='report', output=output)
            self.assertEqual((output / 'review.md').read_text(), 'My judgments')
            self.assertEqual(len((output / 'available.csv').read_text().splitlines()), 20)
            self.assertEqual(len((output / 'results.csv').read_text().splitlines()), 21)
