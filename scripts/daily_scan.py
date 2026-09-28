#!/usr/bin/env python3
"""Scheduler entry point. Run from the checkout with registrar secrets in env."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gooddomains import daily, store


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', default='data/gooddomains.db')
    parser.add_argument('--count', type=int, default=1000)
    parser.add_argument('--output', default='data/private/scans')
    args = parser.parse_args()
    # Finish an interrupted run before reserving any more candidates.
    with store.connect(args.db) as db:
        daily.schema(db)
        old = db.execute('''SELECT r.run,r.requested FROM scan_runs r WHERE EXISTS
            (SELECT 1 FROM scan_items i WHERE i.run=r.run AND i.check_id IS NULL)
            ORDER BY r.created_at,r.run LIMIT 1''').fetchone()
    run = old['run'] if old else 'daily-' + datetime.now(timezone.utc).strftime('%Y%m%d')
    count = old['requested'] if old else args.count
    try:
        print(json.dumps(daily.work(args.db, run=run, count=count, output=args.output), indent=2))
    except (ValueError, OSError) as exc:
        parser.exit(1, f'Error: {exc}\n')


if __name__ == '__main__':
    main()
