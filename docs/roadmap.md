# From a useful shortlist to a large domain index

## The product hypothesis

A founder supplies a company description, intended audience, desired impression, language, budget, and constraints. We retrieve plausible names, rerank for that brief, and show a diverse shortlist with acquisition evidence and explanations. The useful outcome is a name they would actually consider buying, not millions of combinations or a high heuristic score.

The current implementation proves the import → rank by impression → inspect → shortlist loop. Its scores have not been validated against human preferences.

A parallel free [candidate-generation experiment](bulk-check.md) now implements generation, diverse batch export, normalized registrar-result import, and yield reporting. Actual registrar checks are manual and the first pilot remains unchecked. Use the 100-name review sheet to calibrate taste before running the larger batch.

## 1. Start with a buyable slice

Find one permitted marketplace, auction, or broker feed with fixed USD asking prices, ideally in the $500–$5,000 range. Import 10k–100k rows and manually audit a sample of URLs, prices, timestamps, and sale terms. The current adapter accepts an exported CSV; connecting a live provider remains to be done. Auction bids and monthly installments must not be represented as buy-now prices.

Track coverage, duplicate rate, malformed rows, price age, source verification rate, import throughput, storage per domain, and browser query latency. No provider coverage or price freshness SLA is promised yet.

## 2. Make “good for this company” measurable

Assemble 20 diverse briefs: an enterprise finance tool, consumer social app, materials laboratory, family service business, developer tool, and so on. Specify audience, language, desired impression, prohibited associations, expansion plans, and budget. Avoid embedding a preferred existing name in the brief, which would bias judgments toward it.

Collect blind pairwise judgments: “Which of these would you choose for this company?” Include ties, neither, and a short reason. Mix dictionary words, compounds, invented words, longer names, and low-scoring candidates so the experiment can expose the starter model's biases. Do not show heuristic scores before the judgment. Include spoken-name spelling and delayed recall exercises for a subset.

Store `brief_id`, both domain IDs, choice, reason, rater, timestamp, and model version. The current global keep/pass field is useful for a personal shortlist but insufficient for this experiment. Add a dedicated comparisons table and UI before treating feedback as training data.

Fit a simple pairwise preference model before trying an expensive model. Keep judgments for entire briefs and name families out of training for evaluation. Compare against random ordering and the v1 rules using pairwise agreement, shortlist acceptance, and diversity; report rater disagreement. Domain sales prices and successful startup names are confounded signals, not clean labels of name quality.

## 3. Add semantic retrieval and reranking

Use word segmentation, linguistic features, and embeddings to retrieve names related to desired associations, including metaphorical ones. Apply a brief-aware model only to the top hundreds of candidates. Ask for structured scores with explanations and explicit uncertainty. Calibrate against held-out human comparisons; do not mistake model-generated explanations for evidence.

Separate usability, tone fit, distinctiveness, expansion room, and acquisition feasibility. Treat budget and explicit exclusions as filters where appropriate. Return varied naming directions rather than fifty nearly identical compounds. Broaden language support through language-specific evaluation, not by assuming English spelling rules generalize.

## 4. Expand coverage

| Source | What it adds | What it cannot establish alone |
|---|---|---|
| Permitted marketplace/broker feeds | Asking prices and purchase paths | Current ownership transferability or final price |
| Registry zone snapshots | Broad inventory of delegated domains | Every registration, availability, or sale intent |
| Licensed expiry/auction feeds | Potential near-term acquisition opportunities | Guaranteed release, auction outcome, or clean history |
| Candidate generators | Unobserved and invented possibilities | Registrability or a seller |
| Registrar checks on shortlists | Timestamped registration/purchase responses | Brand fit or rights clearance |

Archive raw permitted snapshots outside Git, with source license, file hash, fetch time, and parser version. Add append-only commercial observations, feed run tracking, and explicit listing withdrawal handling. Schedule provider adapters with their supported authentication, rate limits, and caching. Scope acquired data to the agreement permitting its use.

The current importer stores the latest observation per source, processes files incrementally, and is rerunnable. It is not a daily synchronization engine. Before a full zone load, benchmark on increasing samples, avoid repeated scoring of unchanged names, reduce per-domain JSON duplication, and measure disk use. At larger scale, use compact feature storage and partitioned snapshot files for batch computation, plus an indexed serving store. Introduce full-text/semantic retrieval and keyset pagination after profiling. No need for a distributed stack before those measurements justify it.

## 5. Verify finalists

Keep registration state, sale state, and price separate, each with provider and checked-at time. Verify the final asking price and purchase path with the seller/registrar. Add human review of brand conflicts, confusing spellings, language connotations, historical use, and audience response. Treat stale, absent, and failed checks as unknown, not available.

Success criterion: a founder can find several names they would seriously consider, with credible acquisition paths inside their budget, and understand why the recommendations fit their company.
