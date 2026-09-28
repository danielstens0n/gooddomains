import argparse
import json
import sqlite3
import sys

from . import ingest, store
from .ranking import PROFILES, score


def main():
    parser = argparse.ArgumentParser(description="Build and explore an explainable .com index")
    parser.add_argument("--db", default="data/gooddomains.db", help="Local SQLite index")
    commands = parser.add_subparsers(dest="command", required=True)
    imp = commands.add_parser("import", help="Stream a CSV, text list, or .com zone file into the index")
    imp.add_argument("path")
    imp.add_argument("--source", required=True, help="Stable feed identifier; reused to update its observations")
    imp.add_argument("--format", choices=["csv", "txt", "zone"])
    top = commands.add_parser("top", help="Print ranked names as JSON")
    top.add_argument("--profile", choices=PROFILES, default="general")
    top.add_argument("--budget", type=float)
    top.add_argument("--days", type=int, default=30)
    top.add_argument("--query", default="")
    top.add_argument("--review", choices=["keep", "reject", "unreviewed"], default="")
    top.add_argument("--limit", type=int, default=20)
    commands.add_parser("stats")
    commands.add_parser("rescore", help="Recompute all profiles after editing the scoring model")
    serve = commands.add_parser("serve", help="Open the local domain explorer")
    serve.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    try:
        if args.command == "serve":
            from .server import serve
            serve(args.db, args.port)
            return
        with store.connect(args.db) as db:
            if args.command == "import":
                result = ingest.ingest(db, args.path, source=args.source, format=args.format)
            elif args.command == "top":
                result = store.listing(db, query=args.query, budget=args.budget, days=args.days,
                                       review=args.review, limit=args.limit, profile=args.profile)
            elif args.command == "stats":
                result = store.stats(db)
            else:
                count = 0
                for row in db.execute("SELECT domain FROM domains"):
                    for profile in PROFILES:
                        ranked = score(row["domain"], profile)
                        db.execute("INSERT OR REPLACE INTO rankings VALUES(?,?,?,?)",
                                   (row["domain"], profile, ranked["score"], json.dumps(ranked)))
                        if profile == "general":
                            db.execute("UPDATE domains SET score=?,ranking=? WHERE domain=?",
                                       (ranked["score"], json.dumps(ranked), row["domain"]))
                    count += 1
                result = {"rescored": count}
            print(json.dumps(result, indent=2))
        if args.command == "import" and result["skipped"]:
            sys.exit(2)
    except (ValueError, OSError, sqlite3.Error) as exc:
        parser.exit(1, f"Error: {exc}\n")


if __name__ == "__main__":
    main()
