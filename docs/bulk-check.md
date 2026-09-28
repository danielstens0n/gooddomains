# Free candidate-generation experiment

This workflow uses local generation and scoring, plus a **manual registrar browser check** or an available registrar connector. The browser path needs no paid API or scraper. Generating names alone does not establish availability; only recorded registrar results do. Neither path purchases domains.

## 1. Generate a reproducible pool

```sh
python3 -m gooddomains generate --run pilot-002 --count 25000 --seed 42
```

Three starter briefs cover established business software, a playful consumer discovery app, and scientific research tools. They are defined alongside authored vocabulary in `gooddomains/generate.py`. Four strategies contribute:

| Strategy | Method | What we want to learn |
|---|---|---|
| Metaphor | Concrete words associated with the brief | Baseline for recognizable, evocative names |
| Uncommon | Curated less-common English words | Whether less familiar vocabulary yields usable names |
| Compound | Curated modifiers paired with concrete nouns | Which combinations feel natural and memorable |
| Invented | Brief-specific syllable patterns, 6–9 letters | Whether pronounceable inventions outperform compounds |

Every candidate retains its run, brief, strategy, component parts, generator version, and seed. Labels are 4–13 letters with coarse rejection of triple repeats, three consecutive vowels, and long consonant runs. This is not a dictionary-wide search or linguistic guarantee. No brand-conflict checks have been performed. Metaphor/uncommon pools are small; large runs mostly contain compounds and inventions. Many single-word controls will be unrealistic registration prospects.

Generation deduplicates within a run; existing names merge with the index. A run identifier cannot be reused. New runs can overlap older runs, and existing human judgments are preserved. Bump the generator version when changing its rules or vocabulary; seed alone does not identify a generator revision. Generation reports the actual count if the finite pool is exhausted.

## 2. Review 100 names first

```sh
python3 -m gooddomains export-check --run pilot-002 --count 100 \
  --max-component 3 --output data/private/pilot-002/review-100
```

Read `review.md` and mark names you like, dislike, or are unsure about, relative to the brief. Scores and strategies are hidden on that sheet. `manifest.json` contains the full selection audit. The sheet is for discussion; its handwritten judgments are not automatically imported. Use the explorer's Keep/Pass controls to save judgments in the index. Those are currently global judgments, not brief-specific labels.

Selection rotates between brief/strategy groups, taking four ranked names then one shuffled exploration name per group. Ties are shuffled deterministically. Repeated components are capped; invented-word endings are excluded from that cap because they are phonetic glue. Rejected names are excluded. For small batches or exhausted groups the exploration share can differ from 20%; the export reports the actual mix. Scarce groups yield their remaining slots to the other groups. Strict caps can underfill a batch; the exporter reports this rather than silently relaxing them.

## 3. Export and check up to 5,000 names

```sh
python3 -m gooddomains export-check --run pilot-002 --count 5000 \
  --max-component 100 --output data/private/pilot-002/check-5000
```

Each export creates a **new directory** and refuses to overwrite an existing one. It contains:

- `domains.txt`: one complete `.com` per line, no header, for the registrar checker.
- `results-template.csv`: the same names with blank evidence fields. Blank does **not** mean unavailable.
- `manifest.json`: name list, selection method, seed, timestamp, briefs, provenance, and input-file SHA-256.
- `review.md`: a review sheet grouped by company brief.

Go to [Namecheap's free bulk checker](https://www.namecheap.com/domains/bulk-domain-search/), use its import/paste feature, and submit `domains.txt`. Namecheap documents support for line-separated or comma-separated input, including TLDs, with up to 5,000 entries. Use USD display and record the actual registration term. Avoid enabling extra prefixes, suffixes, or other extensions for the experiment. Save both available and unavailable results if the interface permits it, so the availability rate has a meaningful denominator. Checking names does not require buying them.

**The provider's current native result-export format has not been verified.** The implementation accepts our explicit template, not an assumed Namecheap CSV. Preserve whatever raw result/export the browser provides and map its columns to this template. If no export exists, record the observed results for a smaller batch manually. Do not infer the status of unreturned rows. A provider-specific adapter should be added only after inspecting a real output file.

Repeated exports with the same run, seed, reviews, and cap produce the same names. Exporting is not marked as checking, and there is no automatic sent-batch exclusion yet. Use the manifests to avoid accidentally rechecking overlapping batches.

## 4. Import observed results

CSV schema:

```csv
domain,status,price,currency,checked_at,term_months,evidence_url
```

| Field | Meaning |
|---|---|
| `domain` | Exact checked second-level ASCII `.com` |
| `status` | Exactly `available`, `registered`, `premium`, or `unknown` |
| `price` | Optional positive total quoted price for the specified term; no symbols/commas |
| `currency` | Three-letter currency code, required with price; no implicit USD conversion |
| `checked_at` | Actual observation time in ISO 8601 with timezone, required for every result |
| `term_months` | Required with an `available` price; e.g. `12` for a one-year registration |
| `evidence_url` | Optional HTTP(S) source page |

`available` means the registrar explicitly offered an ordinary new registration. `premium` means the registrar labeled it premium; it is not treated as an ordinary registration or automatically as an aftermarket sale. `registered` requires an explicit taken/registered result. Use `unknown` for a failed or ambiguous check, not for “taken.” A broker-service offer or installment amount is not a purchase quote. Keep taxes, optional add-ons, renewal pricing, and eligibility conditions in the original evidence; they are not modeled here. The budget filter compares the quoted total, not an annualized amount.

Leave untouched rows blank. An import containing only blank rows adds no observations. An available result can omit a price, but then cannot satisfy a price filter. Currency without price is rejected, as are priced unknown/registered results.

```sh
python3 -m gooddomains import-check data/private/pilot-002/check-5000/results.csv \
  --provider namecheap-beast-mode \
  --manifest data/private/pilot-002/check-5000/manifest.json
```

Copy the template to `results.csv` before filling it. `--manifest` rejects names outside that batch. Invalid rows fail the **entire** import with the CSV line number; no partial evidence is saved. Exact reimports are idempotent; conflicting observations with the same domain/provider/timestamp are rejected. Observations remain in history.

The latest checked timestamp across providers determines current status. Equal timestamps use insertion order as a tie-breaker. A newer unknown result supersedes an older available quote. Older imported files cannot resurrect a stale result. Missing rows never update existing evidence. Registration observations are stored separately from marketplace asking prices.

## 5. Find available names and measure yield

```sh
python3 -m gooddomains top --availability available --price-type registration \
  --budget 5000 --days 7 --profile catchy
python3 -m gooddomains experiment-report --run pilot-002 --days 7
python3 -m gooddomains serve
```

In the browser, choose **Registrar status → Available to register** and **Price type → Registration quote**. Available, registered, premium, unknown, unchecked, and stale are distinct states. Budget filtering of registration quotes currently supports **USD only**. Results retain other currencies for display. The freshness window is configurable; a recent check is still an observation, not a reservation or checkout guarantee.

The experiment report groups candidates by brief and strategy, showing checked/resolved counts, available counts, availability rate, and kept-available names per 1,000 imported checks. Unknown results are excluded from the availability-rate denominator but included in checked counts. Stale/unchecked names are reported separately. Partial or available-only exports bias these metrics; they are not estimates for the entire candidate pool. Names can appear in multiple runs, so comparisons are not independent trials.

**Initial success target:** 20 names from a completed batch worth considering, followed by fresh registrar confirmation of the finalists. This is an experimental target, not a claim that the first batch will achieve it. Live checks and personal reviews stay in the local database and ignored `data/private/` artifacts.

## Use your reviews for the next batch

After marking names Keep or Pass in the explorer:

```sh
python3 -m gooddomains export-feedback --output data/private/feedback-next --count 200
```

This exports unchecked, unreviewed candidates, using a bounded score adjustment
for components appearing in your keeps and rejects. Each component appears at
most 12 times by default (`--max-component`). The manifest records the exact
review snapshot, matched components, and queue scores. Existing name scores and
reviews stay intact. This is a selection heuristic, not a trained preference
model; positive-only feedback does not establish what you dislike. Previously
checked names are excluded, including stale checks; use the existing freshness
filters to identify names that need a separate recheck.

When the Spaceship connector is available, its `domains_check_availability`
tool checks up to 20 names per call without purchasing anything. Save the raw
responses privately, then normalize explicit `taken` to `registered` and
`available` to `available` (or `premium` when `price.isPremium` is true).
Unrecognized results remain `unknown`. Store the actual observation time,
`price.amount`, `price.currency`, and `12 * price.pricedYears` as the registration
term. The quoted amount already includes any ICANN fee. Preserve missing prices
as missing; never substitute an advertised generic price. These are initial
registration quotes, not renewal prices or aftermarket valuations.
