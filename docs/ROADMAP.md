# Roadmap

## Phase 0: contracts and docs
- [x] `schemas.py`, `stage.py`, `llm.py`, CLI, tests
- [x] DESIGN, METHODS, DATASET docs

## Phase 1: datasheet dataset (done on the author's machine; see docs/DATASET.md)
- [x] `tools/export/corpus_allowlist.yaml` → `bench build-manifest` (copy/rename, sha256, zip)
- [x] `bench build-corpus`: per-page text into `data/corpus/{doc_id}/{page:04d}.txt`
- [x] `bench extract-facts`: fresh LLM extraction with verbatim-quote checks. This replaced the
      planned DB export, because the local DBs were nearly empty.
- [x] `bench make-items`: tiers 1, 2, 3, 5, 6 plus generation filters
- [x] `bench qc`: guarded paraphrase, then an independent Opus cross-check
- [x] `bench split` → dev / test / disputed, plus `data/items/STATS.md`
- [x] `bench validate-items`: every gold quote is found on its page
- [x] `bench audit`: interactive human audit, with `--apply`
- [ ] Human audit of `dev.jsonl` (~100 items, ~1 h). Until then items are `audited: false`.
- [x] Tier 4 (cross-doc comparison), 16 items, all cross-check agree. More need more same-family docs.
- [x] Harder tier 5, first kind: `near_miss_typ` asks for typ when only min/max is stated
      (37 cross-check-confirmed items; Haiku cite scores 0.33). Reported as bucket `5:near_miss_typ`.
- [ ] More near-miss kinds: wrong condition, wrong variant or package
- [ ] More text/enum items (package type, interface, pin function). Currently numeric only.

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
- [ ] Retrievers `dense` / `hybrid`, MPN filtering, one sub-query per entity for T4
- [ ] `column_check` verifier (deterministic typ/min/max column check). See RESULTS.md "next"
- [ ] `nli_small` verifier (local NLI cross-encoder, e.g. via sentence-transformers; heavy deps,
      so make it an optional extra)
- [ ] Verifier track: score verifiers directly on (claim, evidence, label) pairs harvested from
      runs. Labels come from item correctness: a claim stating the wrong value should be unsupported.
- [ ] Free-text answers plus a fixed judge (the judge model must not appear in the scored combo)
- [ ] Vera-Life-style lifecycle handling (stale/superseded facts). Needs versioned docs; design first.

## Phase 3: external sets
- [ ] Adapters to `BenchItem`: FACTS Grounding public, TAT-QA/FinQA subset, HOVER subset
- [ ] Verifier-track adapters: RAGTruth, LLM-AggreFact (claim + doc → label)
- [ ] Check our GAVEL and rag-facts-check implementations against the paper and repo numbers
