from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
import json
import math
import sqlite3

from .ranking import PROFILES, normalize, score


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS domains (
            domain TEXT PRIMARY KEY, score REAL NOT NULL, ranking TEXT NOT NULL,
            created_at TEXT NOT NULL, review TEXT CHECK(review IN ('keep','reject'))
        );
        CREATE INDEX IF NOT EXISTS rank_idx ON domains(score DESC, domain);
        CREATE TABLE IF NOT EXISTS rankings (
            domain TEXT NOT NULL REFERENCES domains(domain), profile TEXT NOT NULL,
            score REAL NOT NULL, ranking TEXT NOT NULL, PRIMARY KEY(domain,profile)
        );
        CREATE INDEX IF NOT EXISTS profile_rank_idx ON rankings(profile,score DESC,domain);
        CREATE TABLE IF NOT EXISTS observations (
            domain TEXT NOT NULL REFERENCES domains(domain), source TEXT NOT NULL,
            kind TEXT NOT NULL, observed_at TEXT NOT NULL, imported_at TEXT NOT NULL,
            price_usd REAL, listing_url TEXT,
            PRIMARY KEY(domain, source)
        );
        CREATE INDEX IF NOT EXISTS price_idx ON observations(price_usd, observed_at, domain);
    """)
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def put(db, domain, *, source, kind="candidate", price_usd=None, observed_at=None, listing_url=None):
    domain = normalize(domain)
    timestamp = now()
    if not source.strip():
        raise ValueError("A source is required")
    if price_usd not in (None, ""):
        price_usd = float(price_usd)
        if not math.isfinite(price_usd) or price_usd <= 0:
            raise ValueError("price_usd must be a finite positive number")
        if not observed_at:
            raise ValueError("Priced observations require observed_at (ISO 8601 with timezone)")
    else:
        price_usd = None
    if observed_at:
        date = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
        if date.tzinfo is None:
            raise ValueError("observed_at must include a timezone")
        if date > datetime.now(timezone.utc) + timedelta(minutes=5):
            raise ValueError("observed_at cannot be in the future")
        observed_at = date.astimezone(timezone.utc).isoformat(timespec="seconds")
    else:
        observed_at = timestamp
    if listing_url and not listing_url.startswith(("https://", "http://")):
        raise ValueError("listing_url must be an http(s) URL")
    ranking = score(domain)
    db.execute("""INSERT INTO domains(domain,score,ranking,created_at) VALUES(?,?,?,?)
                  ON CONFLICT(domain) DO NOTHING""",
               (domain, ranking["score"], json.dumps(ranking), timestamp))
    for profile in PROFILES:
        ranked = ranking if profile == "general" else score(domain, profile)
        db.execute("INSERT INTO rankings VALUES(?,?,?,?) ON CONFLICT(domain,profile) DO NOTHING",
                   (domain, profile, ranked["score"], json.dumps(ranked)))
    db.execute("""INSERT INTO observations VALUES(?,?,?,?,?,?,?)
        ON CONFLICT(domain,source) DO UPDATE SET kind=excluded.kind,
        observed_at=excluded.observed_at, imported_at=excluded.imported_at,
        price_usd=excluded.price_usd, listing_url=excluded.listing_url
        WHERE excluded.observed_at >= observations.observed_at""",
        (domain, source, kind, observed_at, timestamp, price_usd, listing_url or None))


def listing(db, *, query="", budget=None, days=30, review="", limit=100, offset=0, profile="general"):
    if profile not in PROFILES:
        raise ValueError("Unknown ranking profile")
    if not 1 <= limit <= 500 or offset < 0 or not 1 <= days <= 3650:
        raise ValueError("Invalid limit, offset, or freshness window")
    if budget is not None and (not math.isfinite(budget) or budget <= 0):
        raise ValueError("Budget must be a finite positive number")
    if review not in ("", "keep", "reject", "unreviewed"):
        raise ValueError("Unknown review filter")
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    clauses, args = ["1=1"], []
    if query:
        # Literal substring matching: % and _ are not wildcard operators here.
        clauses.append("instr(d.domain, ?) > 0")
        args.append(query.strip().lower())
    if budget is not None:
        clauses.append("EXISTS (SELECT 1 FROM observations o WHERE o.domain=d.domain AND o.price_usd<=? AND o.observed_at>=?)")
        args.extend([budget, cutoff])
    if review == "unreviewed":
        clauses.append("d.review IS NULL")
    elif review:
        clauses.append("d.review=?")
        args.append(review)
    where = " AND ".join(clauses)
    total = db.execute(f"SELECT COUNT(*) FROM domains d WHERE {where}", args).fetchone()[0]
    rows = db.execute(f"""SELECT d.domain,d.created_at,d.review,r.score,r.ranking
                      FROM rankings r JOIN domains d ON d.domain=r.domain
                      WHERE r.profile=? AND {where} ORDER BY r.score DESC,r.domain LIMIT ? OFFSET ?""",
                      [profile, *args, limit, offset]).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["ranking"] = json.loads(item["ranking"])
        item["observations"] = [dict(o) for o in db.execute(
            "SELECT * FROM observations WHERE domain=? ORDER BY observed_at DESC", (row["domain"],))]
        prices = [o["price_usd"] for o in item["observations"] if o["price_usd"] is not None and o["observed_at"] >= cutoff]
        item["recent_price_usd"] = min(prices) if prices else None
        items.append(item)
    return {"items": items, "total": total, "limit": limit, "offset": offset}


def stats(db):
    return dict(db.execute("""SELECT COUNT(*) AS domains,
        COALESCE(SUM(review='keep'),0) AS kept,
        COALESCE(SUM(review='reject'),0) AS rejected FROM domains""").fetchone())


def review_domain(db, domain, verdict):
    if verdict not in ("keep", "reject", None):
        raise ValueError("Review must be keep, reject, or null")
    cursor = db.execute("UPDATE domains SET review=? WHERE domain=?", (verdict, normalize(domain)))
    if not cursor.rowcount:
        raise ValueError("Domain is not in the index")
