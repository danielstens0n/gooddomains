from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from gooddomains import store


class DiscoveryTests(unittest.TestCase):
    def test_filters_sorting_and_latest_evidence(self):
        with TemporaryDirectory() as tmp, store.connect(Path(tmp) / 'index.db') as db:
            fixtures = [('ionfern.com', 'compound', 'available', 9.08),
                        ('hesivi.com', 'invented', 'available', 12),
                        ('cairn.com', 'uncommon', 'available', None),
                        ('taken.com', 'metaphor', 'registered', None),
                        ('oldname.com', 'invented', 'available', 1)]
            for name, strategy, status, price in fixtures:
                store.put(db, name, source='test')
                db.execute('INSERT INTO candidates VALUES(?,?,?,?,?)', (name, 'test', 'solid', strategy, '{}'))
                timestamp = store.now() if name != 'oldname.com' else (datetime.now(timezone.utc)-timedelta(days=8)).isoformat()
                db.execute('''INSERT INTO checks(domain,provider,status,price,currency,term_months,checked_at,imported_at)
                    VALUES(?,?,?,?,?,?,?,?)''', (name, 'test', status, price, 'USD' if price else None, 12 if price else None, timestamp, store.now()))
            def listing(**kwargs):
                return store.listing(db, availability='available', days=7, **kwargs)
            self.assertEqual([r['domain'] for r in listing(sort='shortest')['items']], ['cairn.com', 'hesivi.com', 'ionfern.com'])
            self.assertEqual([r['domain'] for r in listing(sort='price')['items']], ['ionfern.com', 'hesivi.com', 'cairn.com'])
            filtered = listing(min_length=7, max_length=7, name_type='two_words')
            self.assertEqual(filtered['total'], 1)
            self.assertEqual(filtered['items'][0]['length'], 7)
            self.assertEqual(listing(name_type='invented')['total'], 1)
            self.assertEqual(listing(sort='shortest', offset=1, limit=1)['items'][0]['domain'], 'hesivi.com')
            for kwargs in ({'min_length': 9, 'max_length': 4}, {'sort': 'popular'}, {'name_type': 'playful'}):
                with self.assertRaises(ValueError):
                    listing(**kwargs)
            # A newer failed check suppresses the earlier available claim.
            db.execute('''INSERT INTO checks(domain,provider,status,checked_at,imported_at)
                VALUES('ionfern.com','other','unknown',?,?)''', (store.now(), store.now()))
            self.assertEqual(listing(name_type='two_words')['total'], 0)
