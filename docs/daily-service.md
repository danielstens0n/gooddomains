# Daily discovery and the public service

## Scope

The shared index should cover multiple company styles. One person's partial
review session must not narrow what every customer sees. Unreviewed means
unknown; it is never a negative training example. The broad scan queue ignores
personal reviews, balances solid/catchy/scientific profiles and the four naming
strategies, and mixes approximately 80% ranked picks with 20% random exploration
within each group. Small groups exhaust and their allocation moves to the others.
This is a stratified discovery sample, not a census or unbiased market estimate.

The current product covers **unregistered .coms**. A registered domain might be
for sale below $5,000, but this checker cannot establish that. Aftermarket feeds
are a separate acquisition channel. Availability does not establish name
quality, company fit, uniqueness, or trademark clearance.

## Run a broad sweep

```sh
python3 -m gooddomains scan-plan --run broad-001 --count 5000
python3 -m gooddomains scan-pending --run broad-001 --limit 20
python3 -m gooddomains scan-report --run broad-001
python3 -m gooddomains scan-export --run broad-001 --output data/private/scans/broad-001
```

Plans are immutable and stored in SQLite. Every name records its brief,
strategy, selection reason, and eventual observation ID. Pending names are
reserved across runs. Reusing a run with different settings fails explicitly.
The queue never alters review labels or base name scores.

The Spaceship connector can check the pending names. Save each response with
`requested`, `checked_at` (the actual observation time), and `results`, then run:

```sh
python3 -m gooddomains scan-import --run broad-001 data/private/response.json
```

Missing/error results become `unknown`, never available or registered. Extra or
duplicate results reject the import. Both successful and unsuccessful checks
are recorded, so the yield denominator isn't biased by available-only exports.

## Unattended worker

Spaceship's connected chat tool works in this session; it is not a credential
the scheduled Python process can reuse. The worker needs a Spaceship API key
and secret with **domains:read** permission, supplied in its environment as
`SPACESHIP_API_KEY` and `SPACESHIP_API_SECRET`. Do not put values in this repo,
command-line arguments, or logs. No billing or purchase permission is needed.

```sh
python3 scripts/daily_scan.py --count 1000
```

Run from the checkout. An interrupted run is resumed before a new UTC-dated run
is created. A completed run is not repeated the same day. The worker makes
read-only availability requests, 20 names at a time, waits 1.2 seconds between
batches, retries transient errors with bounded backoff, and stops on credential
errors. Raw responses are saved before each SQLite transaction. A process lock
prevents two workers using the same database concurrently. Persistent storage
for **both SQLite and the evidence directory** is required.

Standard REST availability responses may not include registration quotes, even
though the chat connector enriches its results with pricing. Missing prices stay
missing and don't pass budget filters. Do not substitute a generic .com price;
per-domain quotes/renewal pricing need an additional verified integration before
the public service promises an exact purchase cost.

The queue reserves up to 20% of a run for due refreshes: available, premium, and
unknown observations after one day; registered names after 30 days. Available
names are prioritized. Remaining capacity checks new inventory, then additional
due refreshes if inventory runs out. **This is a throughput budget, not a promise
that every available name gets checked daily.** Monitor the refresh backlog and
increase capacity before promising a freshness SLA. The current explorer's
default freshness is 30 days; customers should use a 1-day discovery window.
The CLI already supports `top --availability available --days 1`. Exports include
all results, an available-only CSV, and a random review sample of up to 10 names
per cohort. Re-exporting preserves edits to `review.md`. Marking up that file
does not automatically import reviews into the database; the explorer's Keep/Pass
controls do. Scan reports retain the observations made during that run, while
the explorer uses the latest observation for each domain.

## Scheduling without a hosting bill

The first deployment can run on an existing always-on machine, with a local
SQLite database. A scheduler should load secrets from that machine's secret
store, set the checkout as its working directory, and execute the command above
once per day. For example, after configuring the scheduler's environment:

```cron
0 6 * * * cd /absolute/path/gooddomains && /absolute/path/python3 scripts/daily_scan.py --count 1000 >> data/private/daily.log 2>&1
```

Cron uses the machine's timezone. A sleeping/offline laptop will miss runs. No
schedule is installed automatically by these files. API credentials and a
reliably running host are prerequisites to activation. Public CI is not used as
the database: temporary runners and expiring artifacts would lose the queue,
and public logs/artifacts are unsuitable for private review data.

## Product milestones

1. **Supply:** finish coverage of the current pool, compare yields by strategy
   and company style, then add fresh vocabulary/generation sources. The finite
   current generator is not an endless daily supply of new names.
2. **Quality:** collect judgments on randomly sampled available names, not only
   the top ranked results. Record which names were shown, which were skipped,
   and why names were accepted/rejected. Optimize useful shortlists per 1,000
   checks, not raw availability rate. The current reviewer lacks exposure logs.
3. **Discovery:** ask each customer what their company does, desired impression,
   and budget; rank the shared inventory for that brief. Keep user preferences
   separate from the shared quality score. Add pronunciation/spelling feedback
   and semantic relevance; the current phonetic heuristic is only a proxy.
4. **Trust:** show checked-at timestamps and price terms; recheck finalists on
   demand; don't reserve or buy names implicitly. Hide stale claims. Add renewal
   quotes and marketplace provenance before supporting broader price claims.
5. **Delivery:** build a production read API and customer interface, with separate
   user review records, authentication where needed, caching, and metrics. The
   existing loopback Python server is an internal explorer, not a public server.

Operational metrics: pending/failed checks, oldest pending run, stale available
inventory, missing-price fraction, discovery yield by cohort, and human-rated
shortlist yield. Do not call all available generated strings “good domains.”

Provider references: [Spaceship API and limits](https://docs.spaceship.dev/),
[Spaceship MCP result semantics](https://www.spaceship.com/knowledgebase/spaceship-mcp/).
