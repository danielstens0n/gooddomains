import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from gooddomains import feedback, store


class FeedbackTests(unittest.TestCase):
    def test_reviews_prioritize_matches_without_changing_reviews(self):
        with TemporaryDirectory() as tmp, store.connect(Path(tmp) / 'index.db') as db:
            for name, parts in [('ionwind.com', ['ion', 'wind']),
                                ('ionfern.com', ['ion', 'fern']),
                                ('redfern.com', ['red', 'fern']),
                                ('ionkite.com', ['ion', 'kite'])]:
                store.put(db, name, source='test')
                db.execute('INSERT INTO candidates VALUES(?,?,?,?,?)',
                           (name, 'test', 'scientific', 'compound', json.dumps({'parts': parts})))
            db.execute("UPDATE domains SET review='keep' WHERE domain='ionwind.com'")
            feedback.export(db, output=Path(tmp) / 'positive', count=10)
            positive = json.loads((Path(tmp) / 'positive' / 'manifest.json').read_text())
            by_name = {r['domain']: r for r in positive['candidates']}
            self.assertEqual(by_name['ionfern.com']['queue_score'] - by_name['ionfern.com']['base_score'], 3)
            self.assertEqual(by_name['redfern.com']['queue_score'], by_name['redfern.com']['base_score'])
            db.execute("UPDATE domains SET review='reject' WHERE domain='ionkite.com'")
            output = Path(tmp) / 'batch'
            feedback.export(db, output=output, count=10, max_component=1)
            data = json.loads((output / 'manifest.json').read_text())
            # The rejected ion compound offsets the positive one; a part is not
            # silently declared bad, and the explicit reject is never queued.
            self.assertTrue(all(r['domain'] not in ('ionwind.com', 'ionkite.com')
                                for r in data['candidates']))
            self.assertEqual(len(data['candidates']), 1)  # shared fern capped
            self.assertEqual(db.execute("SELECT count(*) FROM domains WHERE review IS NOT NULL").fetchone()[0], 2)
            with self.assertRaises(FileExistsError):
                feedback.export(db, output=output)

    def test_checked_names_excluded_and_keeps_required(self):
        with TemporaryDirectory() as tmp, store.connect(Path(tmp) / 'index.db') as db:
            with self.assertRaises(ValueError):
                feedback.export(db, output=Path(tmp) / 'empty')
            store.put(db, 'ionwind.com', source='test')
            store.put(db, 'ionfern.com', source='test')
            db.execute("UPDATE domains SET review='keep' WHERE domain='ionwind.com'")
            db.execute('INSERT INTO candidates VALUES(?,?,?,?,?)',
                       ('ionfern.com', 'test', 'scientific', 'compound', json.dumps({'parts': ['ion', 'fern']})))
            db.execute('''INSERT INTO checks(domain,provider,status,checked_at,imported_at)
                          VALUES(?,?,'available',?,?)''', ('ionfern.com', 'test', store.now(), store.now()))
            result = feedback.export(db, output=Path(tmp) / 'batch')
            self.assertEqual(result['selected'], 0)
