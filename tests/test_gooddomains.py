from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import gzip
import json
import unittest

from gooddomains import ingest, store
from gooddomains.ranking import PROFILES, normalize, score
from gooddomains.server import handler_for


class RankingTests(unittest.TestCase):
    def test_normalization(self):
        self.assertEqual(normalize(" CedarBridge.COM. "), "cedarbridge.com")

    def test_rejects_non_domains_and_idns(self):
        for domain in ["https://anchor.com", "x.ai", "www.x.com", "-bad.com", "bad-.com",
                       "xn--bcher-kva.com", "å.com", "a" * 64 + ".com", "a.com<script>"]:
            with self.subTest(domain=domain), self.assertRaises(ValueError):
                normalize(domain)

    def test_friction_penalties(self):
        self.assertGreater(score("riverloom.com")["score"], score("getriverloom.com")["score"])
        self.assertGreater(score("anchor.com")["score"], score("cloud-app123.com")["score"])

    def test_company_impression_changes_order(self):
        self.assertGreater(score("anchor.com", "solid")["score"], score("prism.com", "solid")["score"])
        self.assertGreater(score("prism.com", "scientific")["score"], score("anchor.com", "scientific")["score"])
        self.assertGreater(score("otter.com", "catchy")["score"], score("anchor.com", "catchy")["score"])

    def test_scores_are_bounded_and_explained(self):
        for profile in PROFILES:
            for domain in ["a.com", "anchor.com", "x" * 63 + ".com", "a1-b.com"]:
                result = score(domain, profile)
                self.assertTrue(0 <= result["score"] <= 100)
                self.assertEqual(sum(f["weight"] for f in result["features"].values()), 100)
                self.assertEqual(result["profile"], profile)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db_path = Path(self.temp.name) / "index.db"
        self.context = store.connect(self.db_path)
        self.db = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)

    def put(self, domain="anchor.com", **kw):
        store.put(self.db, domain, source=kw.pop("source", "test"), **kw)

    def test_deduplication_and_source_preservation(self):
        self.put(); self.put(); self.put(source="other")
        result = store.listing(self.db)
        self.assertEqual(result["total"], 1)
        self.assertEqual(len(result["items"][0]["observations"]), 2)

    def test_budget_excludes_unknown_and_stale(self):
        self.put()
        self.put("prism.com", price_usd=4000, observed_at=store.now())
        old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
        self.put("otter.com", price_usd=100, observed_at=old)
        self.put("slate.com", price_usd=6000, observed_at=store.now())
        result = store.listing(self.db, budget=5000)
        self.assertEqual([r["domain"] for r in result["items"]], ["prism.com"])
        self.assertEqual(result["items"][0]["recent_price_usd"], 4000)

    def test_old_import_cannot_replace_newer_price(self):
        self.put(price_usd=4000, observed_at=store.now())
        old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        self.put(price_usd=10, observed_at=old)
        self.assertEqual(store.listing(self.db)["items"][0]["recent_price_usd"], 4000)

    def test_new_snapshot_can_clear_withdrawn_price(self):
        old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        self.put(price_usd=4000, observed_at=old)
        self.put()
        self.assertEqual(store.listing(self.db, budget=5000)["total"], 0)

    def test_price_requires_date_and_rejects_invalid_values(self):
        for values in [{"price_usd": 100}, {"price_usd": -1, "observed_at": store.now()},
                       {"price_usd": "NaN", "observed_at": store.now()},
                       {"price_usd": 100, "observed_at": "2026-01-01"},
                       {"listing_url": "javascript:alert(1)"},
                       {"observed_at": "2099-01-01T00:00:00Z"}]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.put(**values)
        self.assertEqual(store.stats(self.db)["domains"], 0)

    def test_query_and_reviews(self):
        self.put(); self.put("prism.com")
        store.review_domain(self.db, "anchor.com", "keep")
        self.assertEqual(store.listing(self.db, review="keep")["total"], 1)
        self.assertEqual(store.listing(self.db, review="unreviewed")["total"], 1)
        self.assertEqual(store.listing(self.db, query="%")["total"], 0)
        self.assertEqual(store.listing(self.db, query="ANCH")["total"], 1)
        self.put()
        self.assertEqual(store.stats(self.db)["kept"], 1)
        store.review_domain(self.db, "anchor.com", None)
        self.assertEqual(store.stats(self.db)["kept"], 0)

    def test_profile_queries_and_pagination(self):
        self.put(); self.put("prism.com")
        solid = store.listing(self.db, profile="solid", limit=1)
        science = store.listing(self.db, profile="scientific", limit=1)
        self.assertEqual(solid["items"][0]["domain"], "anchor.com")
        self.assertEqual(science["items"][0]["domain"], "prism.com")
        self.assertEqual(store.listing(self.db, profile="solid", limit=1, offset=1)["items"][0]["domain"], "prism.com")

    def test_invalid_filters(self):
        for kwargs in [{"budget": float("nan")}, {"days": -1}, {"limit": 501},
                       {"offset": -1}, {"review": "no"}, {"profile": "no"}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                store.listing(self.db, **kwargs)

    def test_streaming_csv_reports_bad_rows_and_keeps_good_rows(self):
        path = Path(self.temp.name) / "feed.csv"
        path.write_text("domain,price_usd,observed_at\nanchor.com,4000," + store.now() + "\nwrong.ai,,\nprism.com,,\n")
        result = ingest.ingest(self.db, path, source="feed", batch_size=1)
        self.assertEqual(result["accepted_observations"], 2)
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(store.listing(self.db, budget=5000)["total"], 1)

    def test_compressed_list(self):
        path = Path(self.temp.name) / "names.txt.gz"
        with gzip.open(path, "wt") as f:
            f.write("# ignored\nANCHOR.COM.\nanchor.com\nprism.com\n")
        ingest.ingest(self.db, path, source="list")
        self.assertEqual(store.stats(self.db)["domains"], 2)

    def test_zone_delegations_not_glue_or_apex(self):
        zone = """$ORIGIN com.
$TTL 86400
@ IN SOA ns.example.net. host.example.net. (
  1 2 3 4 5 )
@ IN NS ns.example.net.
anchor IN NS ns1.example.net.
       IN NS ns2.example.net.
prism.com. 86400 IN NS ns1.example.net.
ns.anchor IN A 192.0.2.1
www.anchor IN NS ns1.example.net.
"""
        result = list(ingest.zone_domains(zone.splitlines(True)))
        self.assertEqual([x["domain"] for x in result], ["anchor.com.", "anchor.com.", "prism.com."])

    def test_zone_directives_fail_explicitly(self):
        with self.assertRaises(ValueError):
            list(ingest.zone_domains(["$INCLUDE /tmp/secret"]))


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name) / "index.db"
        with store.connect(self.path) as db:
            store.put(db, "anchor.com", source="test")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(self.path))
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        self.temp.cleanup()

    def get(self, path):
        return urlopen(self.base + path, timeout=5)

    def test_assets_and_api(self):
        for path in ["/", "/app.js", "/style.css", "/api/profiles", "/api/stats"]:
            with self.get(path) as response:
                self.assertEqual(response.status, 200)
                self.assertTrue(response.read())
        with self.get("/api/domains?profile=solid") as response:
            self.assertEqual(json.load(response)["items"][0]["domain"], "anchor.com")

    def test_invalid_query(self):
        with self.assertRaises(HTTPError) as error:
            self.get("/api/domains?budget=NaN")
        self.assertEqual(error.exception.code, 400)

    def test_review_persists(self):
        request = Request(self.base + "/api/review", data=json.dumps({"domain": "anchor.com", "review": "keep"}).encode(),
                          headers={"Content-Type": "application/json"})
        with urlopen(request, timeout=5) as response:
            self.assertTrue(json.load(response)["ok"])
        with self.get("/api/domains?review=keep") as response:
            self.assertEqual(json.load(response)["total"], 1)

    def test_cross_origin_write_rejected(self):
        request = Request(self.base + "/api/review", data=b'{}',
                          headers={"Content-Type": "application/json", "Origin": "https://elsewhere.example"})
        with self.assertRaises(HTTPError) as error:
            urlopen(request, timeout=5)
        self.assertEqual(error.exception.code, 403)


if __name__ == "__main__":
    unittest.main()
