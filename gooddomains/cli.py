import argparse
import json
import sqlite3
import sys

from . import checks, feedback, generate, ingest, store
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
    top.add_argument("--availability", choices=["available", "registered", "premium", "unknown", "unchecked", "stale"], default="")
    top.add_argument("--price-type", choices=["asking", "registration"], default="asking")
    gen = commands.add_parser("generate", help="Generate local candidates with brief and strategy provenance")
    gen.add_argument("--count", type=int, default=25000)
    gen.add_argument("--seed", type=int, default=42)
    gen.add_argument("--run", default="experiment-001")
    export = commands.add_parser("export-check", help="Export a diverse batch for a free registrar browser check")
    export.add_argument("--run", required=True)
    export.add_argument("--output", required=True, help="New directory for names, manifest, review, and results template")
    export.add_argument("--count", type=int, default=5000)
    export.add_argument("--seed", type=int, default=42)
    export.add_argument("--max-component", type=int, default=100)
    check = commands.add_parser("import-check", help="Import normalized registrar CSV observations")
    check.add_argument("path")
    check.add_argument("--provider", required=True)
    check.add_argument("--manifest", help="Limit imported domains to a batch manifest")
    report = commands.add_parser("experiment-report", help="Compare availability and shortlist yield by strategy")
    report.add_argument("--run", required=True)
    report.add_argument("--days", type=int, default=30)
    personal = commands.add_parser("export-feedback", help="Choose unchecked candidates using your keeps and rejects")
    personal.add_argument("--output", required=True)
    personal.add_argument("--count", type=int, default=200)
    personal.add_argument("--max-component", type=int, default=12)
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
                                       review=args.review, limit=args.limit, profile=args.profile,
                                       availability=args.availability, price_type=args.price_type)
            elif args.command == "generate":
                result = generate.generate(db, count=args.count, seed=args.seed, run=args.run)
            elif args.command == "export-check":
                result = generate.export_check(db, run=args.run, output=args.output, count=args.count,
                                               seed=args.seed, max_component=args.max_component)
            elif args.command == "import-check":
                result = checks.import_checks(db, args.path, provider=args.provider, manifest=args.manifest)
            elif args.command == "experiment-report":
                result = checks.report(db, run=args.run, days=args.days)
            elif args.command == "export-feedback":
                result = feedback.export(db, output=args.output, count=args.count,
                                         max_component=args.max_component)
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
