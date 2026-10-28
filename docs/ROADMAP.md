# Roadmap

## Phase 0: contracts and docs
- [x] `schemas.py`, `stage.py`, `llm.py`, CLI, tests
- [x] DESIGN, METHODS, DATASET docs

## Phase 1: datasheet dataset (requires the PDFs; see docs/DATASET.md)
- [x] `tools/export/corpus_allowlist.yaml` → `bench build-manifest` (copy/rename, sha256, zip)
- [x] `bench build-corpus`: per-page text into `data/corpus/{doc_id}/{page:04d}.txt`
- [x] `bench extract-facts`: fresh LLM extraction with verbatim-quote checks. This replaced the
      planned DB export, because the local DBs were nearly empty.
- [x] `bench make-items`: tiers 1, 2, 3, 5, 6 plus generation filters
- [x] `bench qc`: guarded paraphrase, then an independent Opus cross-check
- [x] `bench split` → dev / test / disputed, plus `data/items/STATS.md`
- [x] `bench validate-items`: every gold quote is found on its page
- [x] `bench audit`: interactive human audit, with `--apply`
- [x] Human audit replaced by an agent audit of a stratified test sample: 60 items, 0 gold errors
      (data/items/AUDIT.md). Disputed items are discarded, not adjudicated. Near-miss coverage was
      raised instead (`--near-miss-per-doc 6`, Opus cross-check) to 94 confirmed items (test 79).
      A ≥300-item human audit (`bench audit`) remains optional.
- [x] Tier 4 (cross-doc comparison), 16 items, all cross-check agree. More need more same-family docs.
- [x] Harder tier 5, first kind: `near_miss_typ` asks for typ when only min/max is stated
      (94 cross-check-confirmed items; Haiku cite 0.47, full GAVEL 0.89). Reported as bucket `5:near_miss_typ`.
- [x] Second near-miss kind, `near_miss_cond`: parameter stated only at TA = 25 °C, asked at 85 °C
      (`make-items --near-miss-cond-per-doc 10`; 23 generated, 21 cross-check-confirmed: test 19, dev 2)
- [x] Near-miss kind `near_miss_pkg` (θJA asked for a package the part is not offered in): 14 generated,
      12 confirmed. Too easy (all combos 1.0); kept as a regression check. Wrong-variant is still open
- [x] Text/enum items, first kind: `package` (which package is X offered in; 20 items, all cross-check
      agree) from a package extraction pass (`gqb.dataset.packages`, `make-items --packages`)
- [ ] More text items: interface, pin function

## Phase 2: harness and combos
- [x] LLM adapters: `AnthropicClient`, `OpenAICompatClient`, `RoutingClient` (by model prefix),
      retries, disk cache keyed by request hash (+ repeat salt), `PRICES`
- [x] Retriever `bm25` (per-scope index, disk-cached)
- [x] Stages: cull `none|llm_filter|evident_align|gavel_contract`; generate `plain|cite|gavel_debate`;
      verify `none|span_check|gavel_scrutinize|llm_judge|claim_decompose|cascade`;
      repair `none|drop_unsupported|abstain|regenerate` (`gqb/stages/`, registry in `__init__`)
- [x] `runner.py` (threaded, k repeats, resumable JSONL, tier-balanced `limit`) and `bench run`
- [x] `score.py`: numeric/enum correctness, abstention, page-level citation P/R, distractor
      rate, consistency across repeats, cost, latency, calls
- [x] `report.py` / `bench report`: CSV plus static HTML (Pareto scatter, per-tier tables)
- [x] FakeClient end-to-end tests over every stage; Haiku smoke run; cache-only rerun verified
- [x] First dev sweep (`configs/sweep_dev.yaml`, Haiku-centric + Sonnet/Opus generator rows, 3 repeats). Results: `docs/RESULTS.md`
- [x] `scope: corpus` runs (`configs/sweep_corpus.yaml`). Retrieval is the bottleneck (RESULTS.md)
- [x] Retrievers `dense` (bge-small, CPU) / `hybrid` (RRF), `mpn_filter` entity scope. Hybrid+MPN
      recovers the corpus-scope gap (0.833→0.935)
- [x] One sub-query per entity for T4 (`retrieve.per_entity`): T4 0.708→0.854 (n=16)
- [x] `column_check` verifier (deterministic, no LLM): held-out near-miss 0.51→0.77, overall 0.833→0.880
- [x] `nli_small` verifier (nli-deberta-v3-base, extra `local`). Near chance on RAGTruth (BAcc 0.57
      calibrated). Next: a grounding-trained small checker
- [x] Verifier track: done on RAGTruth (`bench vtrack`). Harvesting pairs from datasheet runs is
      still open
- [x] Free-text answers plus a fixed judge: FACTS track (`bench facts`). The judge must differ from the
      generator (enforced). The judge can run through the file queue (docs/ADAPTERS.md)
- [x] Replaceable LLM backend (`GQB_LLM_CLIENT`, `GQB_MODEL_MAP`, file queue): docs/ADAPTERS.md
- [x] Final-run configs and cost (~$50 at list prices; Haiku config measured): docs/FINAL_RUN.md
- [x] Vera-Life-style lifecycle: synthetic revision pairs (`bench fetch-external lifecycle`,
      `configs/lifecycle.yaml`), `lifecycle` retrieval param (evidence state from `revisions.json`),
      `warn_stale` repair. Only 6 real value changes were found in the corpus's revision histories,
      too few to score
- [ ] Lifecycle with real revisions: needs the older public PDF revisions downloaded and added to the
      allowlist; the corpus's revision histories list only 6 value changes
- [x] Fairness pass: loose alignment by default, number grounding in scrutiny limited to the answer
      value, decompose skips commentary, metadata survives evidence extraction (RESULTS.md)

## Phase 3: external sets
- [x] TAT-QA adapter (`external/tatqa.py`, `configs/tatqa.yaml`): 150 numeric items, unit-free scoring
- [x] Verifier track (`vtrack.py`, `bench vtrack`) + RAGTruth adapter; llm_judge matches the
      paper's prompt-baseline profile (RESULTS.md)
- [x] FACTS Grounding track (`external/facts.py`, `bench facts`): long-form answers, FACTS
      response-level judge, verify→revise repair
- [ ] LLM-AggreFact: licence permits eval use; blocked only on HF gated access (accept the terms on
      Hugging Face, then set `HF_TOKEN`)
- [x] HOVER verifier track, oracle docs from the authors' 2017 Wikipedia db (`fetch-external hover`)
- [x] HOVER open retrieval (FTS5 BM25 over 5.2M intros): recall 0.57, verifier BAcc 0.78→0.60
- [ ] FinQA: skipped as redundant with TAT-QA (same skill: numeric QA over financial tables + text)
- [ ] GAVEL fidelity: no public reference numbers on a shared dataset yet; compare once the
      GAVEL repo releases eval data
- [x] FACTS second judge (Sonnet): 92–95% agreement with Opus, more lenient; repair gain holds
- [ ] FACTS third judge from another model family (needs a non-Anthropic key via `OpenAICompatClient`)
      plus the eligibility filter
