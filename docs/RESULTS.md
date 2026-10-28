# Results: first sweeps (2026-10-27)

Configs: `configs/sweep_dev.yaml` (item scope: retrieval over the item's own docs) and
`configs/sweep_corpus.yaml` (corpus scope: BM25 over all 29 docs). Split: `dev` (108 items:
T1 41, T2 17, T3 21, T4 3, T5 18 incl. 6 near-miss, T6 8). Item scope uses 3 repeats per item
(324 records per combo); corpus scope uses 1. Default model: `claude-haiku-5-5`, effort low.
Rebuild: `bench run <config>` then `bench report runs/<name>` (all calls are cached, so a rerun
on the same cache costs nothing). Total spend for dataset and sweeps: about $31.

**Caveats.** n is small: T4 has 3 items and near-miss T5 has 6, so differences of one or two
items are noise. Gold has been cross-checked by Opus but not yet audited by a human. Treat the
numbers below as directional.

## Item scope (sweep_dev), overall

| combo | acc | $/item | p50 ms | p95 ms | calls | consistency | verifier catch | false flag |
|---|---|---|---|---|---|---|---|---|
| cite @ Opus 5.5 | **0.981** | 0.0250 | 2513 | 4003 | 1.0 | 1.000 | – | – |
| cite @ Sonnet 5.5 | 0.963 | 0.0124 | 2124 | 2899 | 1.0 | 0.981 | – | – |
| cite (Haiku baseline) | **0.932** | **0.00066** | 1602 | 3284 | 1.0 | 0.925 | – | – |
| cite + span_check + abstain | 0.932 | 0.00066 | 1598 | 3267 | 1.0 | 0.925 | 0.0 | 0.011 |
| cite + cascade(gavel→Sonnet, <0.9) | 0.932 | 0.00355 | 1734 | 5746 | 1.26 | 0.925 | 0.0 | 0.003 |
| gavel_contract → cite | 0.926 | 0.00090 | 3588 | 6613 | 2.0 | 0.879 | – | – |
| plain (no citations) | 0.914 | 0.00059 | 1491 | 4270 | 0.98 | 0.861 | – | – |
| llm_filter → cite | 0.910 | 0.00076 | 2714 | 4675 | 2.0 | 0.916 | – | – |
| cite + llm_judge + abstain | 0.904 | 0.00116 | 3114 | 6125 | 1.84 | 0.879 | 0.0 | 0.042 |
| gavel_contract → gavel_debate → gavel_scrutinize | 0.901 | 0.00153 | 5671 | 9549 | 4.0 | 0.796 | 0.2 | 0.048 |
| cite + gavel_scrutinize (loose align) | 0.895 | 0.00066 | 1603 | 3265 | 1.0 | 0.870 | 0.0 | 0.065 |
| cite + gavel_scrutinize (strict) | 0.870 | 0.00066 | 1598 | 3266 | 1.0 | 0.851 | 0.0 | 0.100 |
| cite + cascade(gavel→Sonnet, <0.5) | 0.870 | 0.00118 | 1605 | 3978 | 1.04 | 0.851 | 0.0 | 0.100 |
| evident_align (Haiku) → cite (Sonnet) | 0.870 | 0.00501 | 3217 | 5082 | 2.0 | 0.925 | – | – |
| cite + claim_decompose + abstain | 0.836 | 0.00126 | 4187 | 8257 | 2.69 | 0.842 | 0.333 | 0.157 |
| evident_align → cite | 0.787 | 0.00076 | 2871 | 4933 | 2.0 | 0.888 | – | – |

`verifier catch` = share of wrong pre-repair answers that the verifier flagged.
`false flag` = share of correct pre-repair answers that it flagged, which then caused an abstain.
Full per-tier tables: `runs/sweep_dev/report.html` (not committed; regenerate with `bench report`).

## Findings

1. **The cheapest baseline is on the Pareto front.** Haiku + BM25 + `cite` reaches 0.932 for
   $0.00066/item. Moving up a model tier buys accuracy directly (Sonnet +3 pts at 19×
   the cost, Opus +5 pts at 38×). No cull or verify stage we tried beats simply using a bigger
   generator.

2. **The verifiers caught almost none of the generator's errors.** Catch rate is 0.0 for
   span_check, gavel_scrutinize, llm_judge and both cascades. It is 0.2 for the GAVEL
   pipeline and 0.33 for claim_decompose. The surviving Haiku errors are *semantic*:
   - answering with a max limit when a typical value was asked;
   - choosing the wrong condition row;
   - a wrong comparison.
   
   In each case the cited quote really does contain the stated number, so the support checks
   pass. Groundedness verification ≠ correctness verification on this data.

3. **Strict quote scrutiny (GAVEL-style) mostly harms.** It false-flags 10% of correct answers
   overall and 14% on T3 (derived). Inspection shows these are mostly real non-verbatim quotes:
   - reordered table columns;
   - symbols dropped (`Storage temperature –55 to +125 °C` against `storage temperature Tstg
     -55 to +125`);
   - rows merged across PDF-extracted lines.
   
   The verifier is "right" that the quote isn't verbatim, but the answer is correct. `align:
   loose` (order-free token window) halves the false flags (0.065). A Sonnet cascade at
   threshold 0.9 removes nearly all of them (0.003), but then simply returns to the baseline's
   accuracy at 5× the cost.

4. **Derived numbers (T3) are where verifiers misfire, as hypothesised.** Deterministic checks
   cannot find a computed value in any quote. gavel_scrutinize returns `unverifiable` and the
   abstain repair with `allow_unverifiable=true` keeps the answer. The losses on T3 come from
   quote alignment, not from the arithmetic.

5. **Pre-generation culling did not help at item scope.** With only one or two docs in scope,
   BM25 top-k already contains the evidence:
   - `llm_filter` costs 2 points;
   - `evident_align` costs 15 points with Haiku as excerpter. Excerpts drop the column headers
     and conditions needed to read table rows (T3 0.73). Sonnet as the generator recovers only
     part of that;
   - `gavel_contract` is neutral.

6. **Hardest slice: near-miss unanswerables.** The question asks for a *typical* value when
   the datasheet states only min and/or max. Original T5 (a parameter borrowed from a sibling
   part) is about 100% for every combo, so it does not discriminate. On near-miss T5:

   | combo | near-miss T5 accuracy |
   |---|---|
   | Haiku cite (and all Haiku + deterministic verify) | 0.33 |
   | Haiku plain / evident_align / claim_decompose | 0.50 |
   | gavel_contract → cite | 0.67 |
   | full GAVEL (contract + debate + scrutiny) | **0.89** |
   | Sonnet / Opus cite | **1.00** |

   This is the one place where the GAVEL debate earns its cost with Haiku. A judge comparing
   proposals notices "the table has no typ column". The open question for the BI platform:
   is a 4-call Haiku GAVEL pipeline ($0.0015) a cheaper way to get near-Sonnet abstention
   behaviour than a one-call Sonnet ($0.012)?

7. **Distractors (T6) are not a problem at item scope.** Distractor rate is 0.0 for every
   combo; the generator does not answer with the sibling part's value.

8. **Repeat consistency follows model size and pipeline length.** Opus is 1.000 and Sonnet
   0.981; Haiku single-call is 0.925; the 4-call GAVEL pipeline is the least stable at 0.796.

## Corpus scope (sweep_corpus, 1 repeat)

| combo | acc | $/item |
|---|---|---|
| cite @ Sonnet | 0.824 | 0.0126 |
| cite (Haiku) | 0.796 | 0.00065 |
| cite + cascade(gavel→Sonnet, <0.9) | 0.787 | 0.00255 |
| cite + gavel_scrutinize | 0.731 | 0.00065 |
| gavel_contract → cite + gavel_scrutinize | 0.704 | 0.00087 |
| evident_align → cite | 0.704 | 0.00076 |

- At corpus scope accuracy falls by about 14 points for every combo. The main cause is
  **retrieval**: T3 drops to about 0.45 because BM25 top-k over 29 docs misses one of the two
  facts, so the model correctly abstains (false-abstain 0.52 on T3). T4 (comparison across two
  docs) is 0–0.33, since one BM25 query rarely retrieves both parts.
- So the next lever is retrieval, not verification: per-entity sub-queries for comparisons,
  MPN filtering, and dense/hybrid search (see ROADMAP).

## Scoring fixes made during this analysis (worth knowing before trusting old CSVs)

- **Enum (T4) answers** were scored by string only. Opus answered with the winning value
  (`150.0`) rather than the part name and scored 0.0. `score.enum_correct` now accepts a
  numeric answer that matches only the gold option's value. Re-run `bench report`; no new
  calls are needed.
- **`has_verify`** previously counted `verify: none`; fixed.
- **Near-miss T5** has its own report bucket, `5:near_miss_typ`.

## What to try next (cheapest information first)

1. Retrieval for corpus scope: MPN-aware filtering and one sub-query per entity in comparisons.
2. A "column check" verifier: a deterministic rule that refuses a `typ` answer whose quote row
   has no typ value. It targets finding 6 directly and costs nothing.
3. Cascade escalation on *semantic* signals (question asks for typ or a condition; answer is
   derived) rather than quote-alignment confidence.
4. GAVEL debate with Haiku debaters and a Sonnet judge (`judge_model`), to see if it matches
   Sonnet cite on near-miss for less.
5. Grow T4 and near-miss T5 on the test split before drawing conclusions from them.
