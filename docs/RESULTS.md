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

---

# Phase 3: external sets (2026-10-27)

Downloaded and adapted by script (`bench fetch-external tatqa|ragtruth|facts`). Nothing under
`data/external/` is committed. Spend for all of Phase 3: about $8.

## TAT-QA: numeric QA over financial table + text (`configs/tatqa.yaml`, 150 dev items)

75 arithmetic (tier 3) + 75 numeric span (tier 1) questions. Each context (table rendered as
pipe rows plus paragraphs) is one single-page doc. Scoring is unit-free: scale words are
normalized on both sides and either sign is accepted, because TAT-QA gold is inconsistent about
"change" and "cash used" signs. Before that fix, 25 of 28 baseline misses were artifacts.

| combo | acc | T1 | T3 | catch | false flag | $/item |
|---|---|---|---|---|---|---|
| cite @ Sonnet | **0.953** | 0.973 | 0.933 | – | – | 0.0061 |
| plain (Haiku) | 0.927 | 0.973 | 0.880 | – | – | **0.00028** |
| cite (Haiku) | 0.907 | 0.946 | 0.866 | – | – | 0.00035 |
| cite + span_check | 0.907 | 0.946 | 0.866 | 0.0 | 0.0 | 0.00035 |
| cite + llm_judge | 0.893 | 0.933 | 0.853 | 0.0 | 0.014 | 0.00060 |
| cite + cascade(gavel loose → Sonnet) | 0.893 | 0.946 | 0.840 | 0.1 | 0.014 | 0.0033 |
| cite + claim_decompose | 0.853 | 0.906 | 0.800 | 0.5 | 0.066 | 0.00067 |
| cite + gavel_scrutinize (loose) | 0.686 | 0.586 | 0.786 | 0.5 | 0.279 | 0.00035 |
| full GAVEL (contract+debate+scrutiny) | 0.673 | 0.613 | 0.733 | 0.6 | 0.352 | 0.00125 |
| evident_align → cite | 0.420 | 0.520 | 0.320 | – | – | 0.00041 |

What carries over from the datasheet set, and what is new:
- **Same ranking at the top.** A bigger generator is the only lever that adds accuracy;
  verifiers subtract.
- **GAVEL quote scrutiny breaks badly on tables** (false flag 0.28–0.35). The model quotes table
  cells without the pipe layout, or merges a row label with a value from a different line.
  It does catch real errors (0.5–0.6), the best catch rate of any verifier, but it throws away
  about 3 correct answers per caught error.
- **claim_decompose has the best trade-off among verifiers** (catches 50% of errors on T3 at a
  9% false-flag rate), but it is still net negative at Haiku's 91% base accuracy.
- **evident_align collapses on tables** (0.42): excerpts lose column headers, as on datasheets.
- Plain (no citations) beats cite here (+2 pts). Writing quotes seems to cost Haiku some
  arithmetic attention.

## RAGTruth: verifier track (`bench vtrack`, 150 test responses)

25 responses per (task × hallucinated?) cell, so the sample is balanced. The table reweights
precision and F1 to RAGTruth's natural test prevalence (QA 17.8%, Summary 22.7%, Data2txt 64.3%;
overall 34.9%) to compare with the paper's Table 5 (response-level, prompt baselines).

| verifier | QA F1 | Data2txt F1 | Summary F1 | overall R | overall F1* | $/pair |
|---|---|---|---|---|---|---|
| paper: prompt gpt-4-turbo | 45.6 | 78.3 | 47.6 | 97.9 | 63.4 | – |
| paper: SelfCheckGPT gpt-3.5 | 43.7 | 74.8 | 40.1 | 71.9 | 58.8 | – |
| paper: fine-tuned Llama-2-13B | 68.2 | 88.1 | 59.1 | 80.7 | **78.7** | – |
| ours: llm_judge @ Haiku 5.5 | 41.1 | 85.7 | 63.2 | 90.7 | 65.9 | 0.00046 |
| ours: llm_judge @ Sonnet 5.5 | 42.2 | 88.3 | 56.0 | 96.0 | 67.3 | 0.00575 |
| ours: claim_decompose @ Haiku 5.5 | 41.9 | 80.4 | 49.6 | 98.7 | 60.6 | 0.00107 |
| ours: claim_decompose @ Sonnet 5.5 | 37.5 | 81.1 | 55.5 | 98.7 | 61.1 | 0.0134 |

\*Reweighted from our balanced sample; n = 25 per cell, so ±~8 points per task.

- **Sanity check passed.** Our LLM-judge adaptation reproduces the paper's prompt-baseline
  profile: very high recall, poor precision on QA, F1 in the low-to-mid 60s overall.
- **claim_decompose over-flags** (TNR 0.32): with long-form responses some atomic claim is
  almost always judged unsupported. Its prompt is tuned for datasheet claims ("name the part,
  parameter, condition and value"), which likely hurts on news and business data.
- A bigger judge model buys almost nothing (+1.4 F1 at 12× the cost). As in the paper, the gap
  to a fine-tuned small detector (78.7) is the real headroom. That argues for an
  `nli_small`-style trained verifier over a bigger prompted one.

## FACTS Grounding: long-form grounding (`bench facts`, 40 public examples)

Context ≤30k chars. Judge: FACTS's own `response_level` prompt on Opus 5.5, which is not a
generator here. One judge, no eligibility filter, so the numbers are not leaderboard-comparable.

| generator | grounded | median words | gen $/example |
|---|---|---|---|
| Haiku 5.5 | 0.850 | 200 | 0.00058 |
| **Haiku 5.5 + claim_decompose → revise** | **0.975** | 196 | 0.00278 |
| Sonnet 5.5 | 0.850 | 269 | 0.0102 |

- **The one clear win for a verifier: verify → revise on long-form output.** It fixed 6
  responses and broke 1. Length stayed the same (median ratio 1.00; worst case 0.62×), so
  it is not gaming the judge by deleting content. It beats Sonnet at about a quarter of the cost.
- This turns around the short-answer finding. With one-number answers there is little to remove,
  and flagging leads to abstaining (lost accuracy). With long-form answers, revising away
  unsupported sentences keeps the useful content. **For the BI platform:** use verifiers to
  *edit* narrative output, not to *gate* single-value answers.
- Caveat: n = 40 and a single judge from the same model family as the generators.

## Not done in Phase 3 (and why)

- **LLM-AggreFact**: gated on Hugging Face (403 until the terms are accepted). The adapter would
  be ~40 lines on top of `vtrack.VPair`. Accept the dataset terms on Hugging Face and set `HF_TOKEN`, then add
  `external/aggrefact.py`.
- **HOVER**: needs a Wikipedia dump plus retrieval to be a grounded task. Deferred.
- **FinQA**: TAT-QA covers the same table+text arithmetic niche.

# Retrieval, near-miss expansion, local NLI (2026-10-27)

## Retrieval at corpus scope (`configs/sweep_retrieval.yaml`, Haiku cite, 108 dev items, CPU)

| Retriever | acc | T1 | T2 | T3 | T4 | $/item |
|---|---|---|---|---|---|---|
| bm25 | 0.833 | 0.90 | 0.94 | 0.67 | 0.33 | 0.00090 |
| dense (bge-small-en-v1.5) | 0.713 | 0.68 | 0.47 | 0.76 | 0.67 | 0.00089 |
| hybrid (RRF k=60) | 0.833 | 0.90 | 0.71 | 0.76 | 0.67 | 0.00091 |
| bm25 + MPN filter | 0.917 | 0.93 | 0.94 | 1.00 | 1.00 | 0.00082 |
| **hybrid + MPN filter** | **0.935** | 0.95 | 1.00 | 1.00 | 1.00 | 0.00084 |
| hybrid + MPN filter, top_k 16 | 0.935 | 0.98 | 1.00 | 1.00 | 1.00 | 0.00132 |

(Item scope, where gold docs are given, scores 0.932 for the same generator.)

- **The entity filter closes the corpus-scope gap.** Restricting retrieval to documents whose part
  number appears in the question recovers essentially all of the 14 points lost at corpus scope.
  For the BI platform this corresponds to resolving entities (customer, product, period) before
  retrieval. It is cheap and deterministic, and it is worth more than a better embedding model.
- Dense alone is the worst option on datasheets. Small embedding models do not separate near-identical
  parameter tables across parts, and they lose exact symbols (`VOH`, `tPD`). Hybrid only helps once the
  scope is right.
- Embedding the full 2431-page corpus with bge-small takes about 6 minutes on CPU, once (cached).
  After that, queries are milliseconds.

## Near-miss unanswerables, expanded (94 cross-check-confirmed: dev 15→18 shown below, test 79)

`bench make-items --near-miss-per-doc 6` added 68 items. The Opus cross-check confirmed 57 of them
(the rest went to `disputed`). The dev near-miss slice (15 items × 3 repeats = 45 answers) at item scope:

| Combo | near-miss acc | $/item |
|---|---|---|
| Opus cite | 0.93 | 0.0256 |
| **Full GAVEL (contract → debate → scrutinize), Haiku** | **0.89** | 0.0016 |
| Sonnet cite | 0.87 | 0.0126 |
| gavel_contract → cite | 0.67 | 0.0010 |
| claim_decompose verify | 0.64 | 0.0011 |
| EvidenT align (Haiku) → Sonnet cite | 0.64 | 0.0052 |
| gavel_scrutinize / cascade | 0.49–0.56 | 0.0007–0.0019 |
| llm_judge verify | 0.47 | 0.0010 |
| Haiku cite (baseline) | 0.47 | 0.0007 |
| EvidenT align → Haiku cite | 0.42 | 0.0008 |

The earlier finding holds at larger n. Full GAVEL makes Haiku about as good as Sonnet on the
"answer with the limit when only a limit is stated" trap, at an eighth of the cost. The contract step
alone gets about half of that gain. Post-hoc verifiers (llm_judge, span checks) do not help. The wrong
answer cites a real quote with a real number, so it *is* supported by the evidence; only the
question's premise is wrong.

## nli_small: off-the-shelf NLI cross-encoder as verifier (RAGTruth, 150 pairs, CPU)

| Verifier | P | R | F1 | BAcc | $/pair | time |
|---|---|---|---|---|---|---|
| nli_small (nli-deberta-v3-base, threshold 0.5) | 0.51 | 0.97 | 0.67 | 0.51 | 0 | ~11 min |
| nli_small, threshold calibrated on half, scored on other half | | | 0.66 | 0.57 | 0 | |
| llm_judge Haiku | 0.67 | 0.91 | 0.77 | 0.73 | 0.0005 | |
| llm_judge Sonnet | 0.67 | 0.96 | 0.79 | 0.74 | 0.0058 | |
| claim_decompose Haiku | 0.59 | 0.99 | 0.74 | 0.65 | 0.0011 | |

(Raw counts at 50% hallucination prevalence; the prevalence-reweighted F1 for the LLM verifiers is above.)

A general NLI model trained on MNLI/SNLI is near chance on RAG responses, even with a tuned
threshold. Sentence-level entailment over 1200-character windows does not handle numbers, tables or
paraphrase well enough. It is not a usable free cascade stage in this form. A grounding-specific
small model (MiniCheck-style, trained on synthetic grounding data) would be the next thing to
try. Haiku llm_judge at $0.0005/pair is the cheap verifier to beat.

# Fairness fixes, lifecycle (Vera-Life), HOVER (2026-10-27)

## Fixes so each method is not penalised for non-critical mismatches

Diagnosed from verifier rationales on correct answers that were flagged anyway (false flags):

| Gap | Fix | Effect |
|---|---|---|
| `gavel_scrutinize` required *every* number in a claim to be in the quote, including dates, periods and conditions ("52 weeks ended 30 Mar 2019") | The answer value must be in the quote, or just past its end if the quote was cut at a thousands comma. Unit prefixes are allowed (1.5 mA = 1500 µA; scaled matches tolerate rounding). Other numbers only need to appear in the cited unit | TAT-QA false flags 0.28→0.09, acc 0.69→0.84; dev false flags 0.10→0.03 |
| Number parsing split thousands separators ("4,351" → 4, 351), so comma-formatted values never matched | Grouped literals are parsed whole as well as in parts | (included above) |
| Quote alignment was strict in debate scrutiny, `gavel_contract`, `evident_align`, `claim_decompose` | All default to loose alignment: exact, then normalised, then an order-free token window. Values must still match. `align: strict` is still available | Full GAVEL on TAT-QA 0.67→0.81 (false flags 0.35→0.11); dev 0.900→0.917 |
| `claim_decompose` turned commentary ("the revisions conflict") into claims that then failed | The decompose prompt keeps only asserted facts | dev false flags 0.16→0.11, catch 0.37→0.48 |
| Evidence-extraction culls (contract, EvidenT) kept bare quotes and lost the document-metadata line (revision header) | Extracted units keep a leading `[...revision/version...]` line. No datasheet or TAT-QA page has one, so those runs are unchanged | GAVEL on stale-only 0.54→1.00 |
| GAVEL judge dropped the caveats debaters raised | Judge prompt: keep caveats about sources (superseded revision, conflicting values) | (included above) |

Trade-off: loose scrutiny catches fewer near-miss answers (scrutinize catch on near-miss
0.17→0.04). Strict alignment had been catching them partly by accident, through "quote not found".
On dev near-miss, full GAVEL now scores 0.78 (was 0.89; n=45 answers from 15 items, ±12 pt). It is
still the best Haiku-priced option on that slice. The scoring itself was audited for artefacts: the remaining wrong
answers on dev and TAT-QA are genuine (wrong row, arithmetic, or a year question answered with a value).

## Lifecycle: Vera-Life-style supersession (`configs/lifecycle.yaml`, 111 dev items, item scope)

The corpus has one revision per part, so `bench fetch-external lifecycle` synthesises old
revisions. Each is a copy of the gold page in which only the answer value differs (37 dev source
items; verified to differ in exactly one token). Every page carries a revision header.

| Combo | superseded | superseded (dates only) | stale only | $/item |
|---|---|---|---|---|
| Haiku plain | 0.95 | 0.95 | 0.92 | 0.0007 |
| Haiku cite | 0.92 | 0.95 | 0.97 | 0.0008 |
| Haiku cite + `lifecycle` retrieval + `warn_stale` | 0.92 | 0.92 | **1.00** | 0.0008 |
| + llm_judge → abstain | 0.92 | 0.92 | 1.00 | 0.0014 |
| + claim_decompose → abstain | 0.81 | 0.73 | 1.00 | 0.0014 |
| gavel_contract → cite | 0.92 | 0.89 | 0.95 | 0.0010 |
| full GAVEL | 0.84 | 0.84 | 1.00 | 0.0018 |
| Sonnet cite | 0.97 | 0.89 | 0.97 | 0.0136 |

- **Stale rate is 0 for every combo.** No model answered with the superseded value when both revisions were in
  context, even when only dates indicated which was current. When revision metadata is visible,
  in-context supersession is solved at Haiku level.
- Errors are over-abstention, not staleness. A second, conflicting revision costs 2–8 points on
  verifier combos. They abstain on conflict instead of resolving it (the `explain_conflict` case).
- **For the BI platform, the risk is provenance loss.** Before the metadata fix, pipelines that
  re-extract evidence (GAVEL contract, EvidenT) stripped the revision header. Their stale-only score
  fell to about 0.5: they answered from a superseded source without warning. Version metadata must
  travel with every evidence unit through every stage. Metadata-driven filtering (`lifecycle`
  retrieval plus `warn_stale`) is deterministic, free, and reaches 1.00.
- Caveat: the synthetic revisions are labelled explicitly. Real staleness, where a newer document
  silently contradicts an older one without metadata, is harder and is not covered.

## HOVER: multi-hop claim verification (`bench vtrack --pairs data/external/hover/pairs.jsonl`)

150 dev claims, 25 per (2/3/4 hops × SUPPORTED/NOT_SUPPORTED) cell. The context is the full 2017
intro paragraphs of every supporting article (oracle documents), so this measures verification
only.

| Verifier | 2-hop BAcc | 3-hop | 4-hop | all F1 | all BAcc | $/pair |
|---|---|---|---|---|---|---|
| llm_judge Haiku | 0.76 | 0.80 | 0.78 | 0.81 | 0.78 | 0.0003 |
| llm_judge Sonnet | 0.78 | 0.78 | 0.82 | 0.81 | 0.79 | 0.0043 |
| claim_decompose Haiku | 0.74 | 0.70 | 0.74 | 0.77 | 0.73 | 0.0005 |
| claim_decompose Sonnet | 0.70 | 0.70 | 0.74 | 0.77 | 0.71 | 0.0064 |
| nli_small (calibrated on half) | | | | 0.71 | 0.67 | 0 |

- One holistic judge call beats decompose-then-verify on multi-hop claims at every size. Decomposition
  breaks the bridge entity across atoms, and each atom is then checked alone. This matches the
  RAGTruth result.
- Haiku is within 1–2 points of Sonnet at 1/14 of the cost.
- The NLI cross-encoder does better here (short, clean Wikipedia prose) than on RAGTruth, but it falls off with hop count
  (BAcc 0.68 → 0.56 uncalibrated).
- Reference point: the HOVER paper's BERT verifier is trained on HOVER and is not directly comparable.
  These are zero-shot.

# Held-out test split, final configuration (443 items, corpus scope, 1 repeat)

`configs/final_datasheet.yaml` with `repeats: 1`: hybrid retrieval + MPN filter, Haiku, all 29 docs in
scope, pypdfium2 text. It cost $5.67 and took 16 minutes on CPU. (n per tier: T1 145, T2 61, T3 73, T4 13,
T5 122 of which near-miss 79, T6 29.)

| Combo | all | T1 | T2 | T3 | T4 | T5 | near-miss | T6 | $/item |
|---|---|---|---|---|---|---|---|---|---|
| **gavel_contract → cite** | **0.865** | 0.87 | 0.93 | 0.86 | 0.69 | 0.83 | 0.76 | 0.93 | 0.00112 |
| full GAVEL (contract → debate → scrutinize) | 0.844 | 0.83 | 0.85 | 0.88 | 0.69 | **0.85** | **0.80** | 0.83 | 0.00177 |
| cite + gavel_scrutinize → abstain | 0.835 | 0.90 | 0.92 | 0.89 | 0.62 | 0.68 | 0.52 | 0.97 | 0.00090 |
| cite + span_check → abstain | 0.835 | 0.90 | 0.92 | 0.89 | 0.69 | 0.67 | 0.52 | 0.97 | 0.00090 |
| cite (baseline) | 0.833 | 0.90 | 0.92 | 0.89 | 0.69 | 0.66 | 0.51 | 0.97 | 0.00090 |
| cite + cascade (scrutinize → Sonnet judge) | 0.831 | 0.90 | 0.92 | 0.88 | 0.69 | 0.66 | 0.51 | 0.97 | 0.00324 |
| cite + claim_decompose → abstain | 0.828 | 0.85 | 0.80 | 0.86 | 0.54 | 0.80 | 0.72 | 0.93 | 0.00161 |
| cite + llm_judge → abstain | 0.828 | 0.88 | 0.92 | 0.86 | 0.69 | 0.70 | 0.56 | 0.90 | 0.00152 |
| plain (no citations) | 0.824 | 0.89 | 0.95 | 0.85 | 0.54 | 0.66 | 0.49 | 0.97 | 0.00083 |

- **The dev findings replicate on held-out data.** GAVEL's evidence contract is the one component that
  moves the overall number (+3.2 points over cite, for +$0.0002/item). Its gain comes from abstention on
  near-miss unanswerables: 0.51 → 0.76 (contract) and 0.80 (full GAVEL), n = 79. The cost is
  answerable-tier accuracy. Full GAVEL drops T1 by 6 points and T6 by 14 points, because it over-abstains
  when evidence is complete.
- Post-hoc verifiers (scrutinize, span_check, llm_judge, cascade) are within ±0.5 points of the baseline.
  The Sonnet cascade triples cost for nothing. claim_decompose trades answerable accuracy for abstention,
  as GAVEL does, but less efficiently.
- Overall test accuracy (0.833 for cite) is lower than the dev retrieval sweep (0.935) mainly because of
  composition. Test has 79 near-miss items (18%), and the dev sweep had 6. Excluding near-miss, test cite
  scores 0.904 (n=364), against 0.93 on dev at item scope. Corpus-scope retrieval costs about 3 points.
- **Recommendation for the BI platform (component choice per stage):**
  - retrieve: hybrid + entity filter
  - cull: GAVEL-style evidence contract when abstention matters; otherwise none
  - generate: cited answers with Haiku-class models
  - verify: a single LLM-judge call only for long-form output, followed by revise; for single values,
    no verifier
  - lifecycle: version metadata on every evidence unit, with deterministic supersession filtering
