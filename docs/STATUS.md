# Status: stage options, results, and what to test next

A snapshot for anyone picking the next combos to run. It covers what each stage offers, what has
been measured and where the data lives, and which combos are still untested or open. Details and
the reasoning behind each number are in `RESULTS.md`; how the metrics are defined is in `score.py`.

Last updated 2026-10-27, after the table-header fix (commit `c718365`). LLM spend so far is $97.13 at list prices
(`bench spend`).

## How to read the numbers

- **acc**: correct answers, or correct abstentions on unanswerable items.
- **grounded**: correct **and** citing a gold page, or a correct abstention.
- **halluc.**: wrong answers / answered items (answering an unanswerable item counts as wrong).
- **src hit**: answered items that cite at least one gold page (EvidenT's metric).
- **attrib.**: the answer value appears in the evidence the answer cites (tiers 1/2/5/6 only).
- **$/item**: list-price LLM cost per question.

The default model is Haiku 5.5 in every slot unless a row says otherwise. About 30% of items are
unanswerable, so a well-calibrated system answers about 70%.

## Stage options

| Stage | Option | Paper / origin | Status |
|---|---|---|---|
| retrieve | `bm25` | baseline | measured (dev, corpus scope) |
| retrieve | `dense` (bge-small, CPU) | baseline | measured, weak alone |
| retrieve | **`hybrid`** (RRF of the two) | baseline | **default** |
| retrieve param | **`mpn_filter`** (search only the named parts' docs) | general entity scoping | **default** |
| retrieve param | `per_entity` (passages per named part) | general multi-entity | measured on test |
| retrieve param | `lifecycle` (tag current vs superseded passages) | Vera-Life | lifecycle set only |
| retrieve param | `top_k` | – | dev only (8 vs 16) |
| cull | `none` | – | baseline |
| cull | `llm_filter` | – | dev only |
| cull | `evident_align` (verbatim excerpts + header carry) | EvidenT | measured on test |
| cull | **`gavel_contract`** (subclaims bound to quotes + header carry) | GAVEL | **recommended** |
| generate | `plain` (no citations) | baseline | measured |
| generate | **`cite`** (claims cite evidence ids) | baseline RAG | **default** |
| generate | `gavel_debate` (two debaters + judge) | GAVEL | measured on test |
| verify | `span_check` (deterministic quote/number check) | – | measured, no effect |
| verify | `gavel_scrutinize` | GAVEL | measured, no effect |
| verify | `llm_judge` | prompt baseline | measured, no effect |
| verify | `claim_decompose` | rag-facts-check | measured; useful on long-form |
| verify | `cascade` (cheap check, then escalate) | – | measured, no effect, 3× cost |
| verify | `nli_small` (deberta NLI, CPU) | – | RAGTruth only: near chance |
| repair | **`abstain`** | – | used in every verify combo |
| repair | `drop_unsupported` | – | smoke test only |
| repair | `regenerate` | – | smoke test only |
| repair | `warn_stale` | Vera-Life | lifecycle set only |

## Results and their data

To regenerate any table: `uv run bench report runs/<dir> [--items <items>]`, which writes
`runs/<dir>/scores.csv` and `report.html`. Runs, the LLM cache and the corpus are not in git; they
come from the data zip (see `docs/FINAL_RUN.md`).

### Held-out test: 493 items, corpus scope, hybrid + mpn_filter
Data: `runs/final_datasheet_r1/`, items `data/items/test.jsonl`. Repeats: 1 (Haiku rows),
1 (Sonnet/Opus rows).

| Combo | acc | grounded | halluc. | src hit | attrib. | $/item |
|---|---|---|---|---|---|---|
| cite @Opus | 0.920 | 0.902 | 0.049 | 0.959 | 0.992 | 0.0342 |
| cite @Sonnet | 0.898 | 0.878 | 0.061 | 0.954 | 0.983 | 0.0170 |
| gavel_contract → gavel_debate (Sonnet judge) → scrutinize → abstain | 0.896 | 0.864 | 0.059 | 0.919 | 0.987 | 0.0081 |
| **gavel_contract → cite** | 0.888 | 0.853 | 0.071 | 0.915 | 0.995 | **0.0012** |
| gavel_contract → gavel_debate → scrutinize → abstain | 0.876 | 0.843 | 0.061 | 0.919 | 0.983 | 0.0018 |
| cite + per_entity | 0.837 | 0.815 | 0.162 | 0.940 | 0.930 | 0.0009 |
| cite → gavel_scrutinize → abstain | 0.835 | 0.813 | 0.154 | 0.942 | 0.929 | 0.0009 |
| cite → span_check → abstain | 0.835 | 0.813 | 0.160 | 0.940 | 0.929 | 0.0009 |
| cite (basic RAG) | 0.833 | 0.811 | 0.162 | 0.940 | 0.930 | 0.0009 |
| cite → cascade → abstain | 0.831 | 0.809 | 0.161 | 0.943 | 0.929 | 0.0031 |
| cite → llm_judge → abstain | 0.829 | 0.809 | 0.151 | 0.945 | 0.931 | 0.0015 |
| cite → claim_decompose → abstain | 0.815 | 0.784 | 0.112 | 0.926 | 0.954 | 0.0016 |
| evident_align → cite | 0.789 | 0.754 | 0.130 | 0.909 | 0.942 | 0.0011 |
| plain | 0.825 | 0.212 | 0.163 | – | – | 0.0008 |

Per-tier buckets are in the same `scores.csv` (for example `5:near_miss_typ`, `1:package`, `4`).
Notable buckets:
- **`5:near_miss_typ`** (asks for a typ value when only min/max is stated): cite 0.51, contract
  0.85, full GAVEL 0.89–0.90, Opus 0.91.
- **`1:package`**: cite 0.75, contract 0.90, claim_decompose 0.40.
- **`4`** (comparison across two docs, n=16): cite 0.71, cite + per_entity 0.85.

### Dev sweep: 120 items, item scope (bm25 over the item's own docs), 3 repeats
Data: `runs/sweep_dev/`, items `data/items/dev.jsonl`. Contract and EvidenT rows were rerun after
the header fix; the pre-fix runs are in `runs/_pre_headers/`.

| Combo | acc | grounded | halluc. | $/item |
|---|---|---|---|---|
| cite @Opus | 0.975 | 0.950 | 0.011 | 0.0251 |
| cite @Sonnet | 0.950 | 0.925 | 0.037 | 0.0125 |
| gavel_contract → cite | 0.933 | 0.911 | 0.041 | 0.0009 |
| gavel_contract → debate → scrutinize → abstain | 0.925 | 0.902 | 0.034 | 0.0016 |
| cascade (escalate below 0.9, loose alignment) | 0.908 | 0.877 | 0.090 | 0.0030 |
| cite / cite → span_check | 0.905 | 0.875 | 0.094 | 0.0007 |
| evident_align (Haiku) → cite @Sonnet | 0.905 | 0.886 | 0.061 | 0.0052 |
| plain | 0.888 | 0.191 | 0.093 | 0.0006 |
| cite → gavel_scrutinize | 0.886 | 0.855 | 0.093 | 0.0007 |
| llm_filter → cite | 0.880 | 0.852 | 0.110 | 0.0008 |
| cite → llm_judge | 0.880 | 0.850 | 0.097 | 0.0011 |
| cite → claim_decompose | 0.861 | 0.830 | 0.056 | 0.0012 |
| evident_align → cite | 0.808 | 0.783 | 0.105 | 0.0008 |

### Retrieval: dev, corpus scope (all docs), cite generator
Data: `runs/sweep_retrieval/`. Item set: the dev split as it was when the sweep ran (smaller than
today's 120).

| Retrieval | acc | src hit |
|---|---|---|
| hybrid + mpn_filter | 0.935 | 0.966 |
| hybrid + mpn_filter, top_k 16 | 0.935 | 0.977 |
| bm25 + mpn_filter | 0.916 | 0.977 |
| bm25 | 0.833 | 1.0 |
| hybrid | 0.833 | 0.961 |
| dense | 0.712 | 0.937 |

`runs/sweep_corpus/` (bm25 at corpus scope, no mpn_filter) is superseded by this sweep. Its
contract/EvidenT rows predate the header fix.

### TAT-QA: 150 financial table+text items, item scope
Data: `runs/tatqa/`, items `data/external/tatqa/items.jsonl`.

| Combo | acc | halluc. |
|---|---|---|
| cite @Sonnet | 0.953 | 0.033 |
| gavel_contract → cite | 0.913 | 0.041 |
| cite | 0.906 | 0.068 |
| evident_align → cite | 0.846 (was 0.42 before the header fix) | 0.072 |
| full GAVEL | 0.846 | 0.023 |
| cite → claim_decompose | 0.833 | 0.060 |
| plain | 0.926 | 0.047 |

Source hit is 1.0 for every cited combo, because each item has a single doc.

### Lifecycle (synthetic revision pairs), item scope
Data: `runs/lifecycle/`, items `data/external/lifecycle/items_dev.jsonl`.

| Combo | acc | `5:stale_only` |
|---|---|---|
| cite → warn_stale (+ lifecycle retrieval) | 0.945 | 1.0 |
| cite / cite @Sonnet / cite → llm_judge | 0.945 | – |
| cite → claim_decompose | 0.846 | – |
| gavel_contract → cite | 0.765 | 0.41 |
| full GAVEL | 0.765 | 0.59 |

The contract drops each passage's revision header, so it cannot tell stale from current.

### Verifier-only and long-form tracks
- **RAGTruth** (`bench vtrack`): `llm_judge` matches the paper's prompt baseline; `nli_small` has
  balanced accuracy 0.57 after calibration.
- **HOVER**: with oracle docs, verifier balanced accuracy is about 0.78. With open BM25 retrieval
  over 5.2M Wikipedia intros (recall 0.57), it falls to about 0.60.
- **FACTS Grounding** (`bench facts`, long-form): Haiku 0.85 → **0.95** with verify→revise
  (`claim_decompose` + revise). The Sonnet judge agrees with the Opus judge 92–95% of the time.

## Findings that constrain what to try next

1. **Evidence before generation beats checking after it.** `gavel_contract` is the only cheap
   stage that moves both accuracy (+5.5 points) and hallucination (16% → 7%). Post-hoc
   verifiers on short answers change nothing: wrong answers cite real, supporting-looking evidence
   (the wrong row or value on the right page). Source hit is above 0.9 everywhere, so retrieval is
   rarely the failure.
2. **Verifiers that help also over-abstain.** `claim_decompose` and EvidenT lower hallucination
   but answer only 62–63% of items (the target is about 70%).
3. **A bigger generator is the other lever.** Opus cite is the best single combo at about 30× the
   cost of the Haiku contract.
4. **Retrieval is solved for this corpus** with hybrid + mpn_filter. The open gap is retrieval at
   web scale (HOVER open), not here.

## Untested or open combos, in priority order

Cost estimates are at list price for the 493-item test set, 1 repeat.

| # | Combo / question | Why | Est. cost |
|---|---|---|---|
| 1 | `gavel_contract → cite` with **Sonnet / Opus generate** (contract stays Haiku) | Does the contract's gain stack on a strong generator, or does Opus already do it? Decides whether the contract belongs in the top config | ~$9 / ~$17 |
| 2 | `gavel_contract` @Sonnet → cite @Haiku | Is contract quality the bottleneck? | ~$5 |
| 3 | `gavel_contract` + `per_entity` | Two independent gains, never combined; comparison tier is weak under the contract | ~$0.6 |
| 4 | Contract that keeps passage provenance (revision / doc header) | Fixes the lifecycle regression (stale-only 0.41) in a general way, like the table-header fix | dev + lifecycle, <$1 |
| 5 | `gavel_contract → cite → claim_decompose → regenerate` | `regenerate` has never been scored; it could recover claim_decompose's over-abstention | ~$1.5 |
| 6 | `drop_unsupported` repair after `claim_decompose` | Same over-abstention question, cheaper | ~$1 |
| 7 | `llm_filter` on test | Dev result only (below cite); confirm or drop | ~$0.5 |
| 8 | EvidenT abstention calibration (a general prompt change, not test-fitted) | EvidenT is the most attributable method after the contract but answers too little | dev first, <$0.5 |
| 9 | `nli_small` / a grounding-trained small checker in the datasheet pipeline | Only a free, CPU-only verifier could make cascades cheap; current NLI is near chance | $0 LLM |
| 10 | Judge-based claim faithfulness (RAGChecker-style) on a sample | `attributed` does not cover derived (tier 3) and comparison (tier 4) answers | ~$3 (Opus judge, 150 items) |
| 11 | Repeats ≥2 for the recommended rows | Consistency metric; most test rows have 1 repeat | same as row cost |
| 12 | Rerun or retire `configs/sweep_corpus.yaml` | Pre-header-fix rows, superseded by the retrieval sweep | ~$0.5 |

Blocked items (see `ROADMAP.md`): LLM-AggreFact (needs HF gated access), GAVEL reference numbers
(not yet released), a non-Anthropic judge (needs another provider key).

## Rules for new work

- Method fixes must be general: they must work for any document. Do not tune them to the
  benchmark's items, tiers or phrasing; such changes were tried and removed (`RESULTS.md`, "How
  general are the fixes?").
- Tune on `dev`; report on `test`. Never pick a config by its `test` score alone.
- A rerun after a stage change needs the old run file moved aside (runs resume per combo). The LLM
  cache key includes the prompt and schema, so changed stages make fresh calls.
- `bench qc` cross-checks must use `--corpus-dir data/local/corpus_pymupdf`.
