# Methods surveyed and how each is adapted

Each entry covers: what the method is, its source, the slot(s) it fills here, and the adaptation details.

## GAVEL: Evidence-Contract Debate with Mechanized Scrutiny
Findings of ACL 2026. https://aclanthology.org/2026.findings-acl.1789/

Multi-agent debate for fact-checking.
- **Evidence Contract**: debaters must state atomic subclaims, each bound to explicit evidence-unit ids.
- **Mechanized Chain of Scrutiny**: a neutral scrutinizer deterministically checks the cited ids and quoted spans.
- **Judge**: selects a sufficient evidence set and issues the verdict.

Evaluated open-book on FEVEROUS and HOVER with provenance-aware metrics.

Slots:
- `gavel_contract` (cull): ask for subclaims that would answer the question, each bound to evidence units. Keep only the bound units.
- `gavel_debate` (generate): N debaters each propose an answer with a contract. The judge picks one.
- `gavel_scrutinize` (verify): the deterministic part only. Check that every cited id exists and every quote appears in the cited unit.

The scrutinizer is cheap, which makes it a strong first tier for `cascade`.

## EvidenT: evidence groundedness and traceability for enterprise assistants
EMNLP 2026 Industry. https://aclanthology.org/2026.emnlp-industry.69/

Structured passage extraction plus deterministic lexical alignment, applied before answer generation.
It removes unsupported content, fixes misattributed citations, and links every claim to a source span. No retraining.
They report about 29% higher gold-source hit rate than prompting baselines on about 500 enterprise queries (data not released).

Slots: `evident_align` (cull: extract passages, align them lexically to the source, drop what doesn't align), `span_check` (verify).

Weakness: lexical alignment can't support derived numbers (tier 3).

## rag-facts-check (EEA)
https://github.com/eea/rag-facts-check (MIT)

Post-hoc verifier. Pipeline:
1. Chunk the answer, repeating table headers in each chunk.
2. An LLM extracts atomic claims.
3. Deduplicate claims.
4. Verify claims in batches against the sources, with documents placed before claims so the KV cache is reused.
5. Map evidence quotes to character offsets: exact match, then whitespace/punctuation-tolerant regex, then fuzzy alignment.
6. Aggregate into a 0–10 score.

Slot: `claim_decompose` (verify). The multi-stage quote matcher is also what `span_check` should use.

## Vera-Life: lifecycle faithfulness
GroundLM 2026. https://aclanthology.org/2026.groundlm-1.18/

Claim-level criterion: semantic support, validity at the target time, non-supersession, and provenance.
- The controller builds an evidence-state graph and picks one action: answer, warn_stale, explain_conflict, retrieve_more, or abstain.
- Benchmark: Vera-Life-Eval, 8.5k questions over 41k versioned pieces of evidence.

Relevant later, for distractor and revision items (tier 6) and for BI data where staleness matters.
Possible `repair` implementations: `warn_stale`, `explain_conflict`.

## RAGChecker (Amazon)
https://github.com/amazon-science/RAGChecker (Apache-2.0)

Claim-level entailment metrics:
- overall precision, recall and F1
- retriever: claim recall, context precision
- generator: context utilization, noise sensitivity, hallucination, self-knowledge, faithfulness

Use: secondary metrics in `score.py` (claim recall against a gold answer, noise sensitivity). It is not a pipeline stage.

## VeriFastScore
Findings of EMNLP 2025. https://aclanthology.org/2025.findings-emnlp.491/

A fine-tuned Llama-3.1-8B that extracts and verifies claims in a single pass against search evidence.
6.6× faster than VeriScore, with r=0.94 system-level correlation.

Use: candidate `verify` implementation if the released model is used against our own documents instead of Google Search. Treat as optional.

## FACTS Grounding (Google DeepMind)
https://www.kaggle.com/benchmarks/google/facts-grounding

1,719 items (860 public), with documents up to 32k tokens. Multi-judge LLM scoring with an eligibility gate, then a grounding check.

Use: external generator-track sanity set (Phase 3).

## Other external sets (Phase 3)
- **RAGTruth**: span-level hallucination labels. Verifier track.
- **LLM-AggreFact**: aggregate of grounding datasets. Verifier track.
- **TAT-QA / FinQA / OfficeQA**: calculations over tables and text. Closest public proxy for numeric BI.
- **HOVER / FEVEROUS**: GAVEL's own sets, used to check our GAVEL implementation against the paper.

## Implementation map (what the code actually does)

These are simplified adaptations, not reproductions. Fidelity checks against the papers are Phase 3.

| Method | Slot / impl | File | What is implemented | Simplifications |
|---|---|---|---|---|
| GAVEL evidence contract | cull `gavel_contract` | `stages/cull.py` | LLM lists the atomic subclaims needed and binds each to a passage id plus a verbatim quote. Bindings whose id or quote fails to align are dropped. The surviving quotes become the evidence units. | One drafting call; no iterative contract negotiation |
| GAVEL debate | generate `gavel_debate` | `stages/generate.py` | `debaters` (default 2) proposals with different stances, each mechanically scrutinized (ids exist, quotes align). A judge sees the proposals plus the scrutiny report and issues the final cited answer or abstains. | Single round; judge = slot model unless `judge_model` is set |
| GAVEL mechanized scrutiny | verify `gavel_scrutinize` | `stages/verify.py` | Deterministic, no LLM. Citation ids exist, the quote aligns in a cited unit, and the claim's numbers (excluding numbers from the question) appear in the quote. Unmatched derived numbers give `unverifiable`. | `align: strict` (exact→normalized→squashed→fuzzy) or `loose` (adds order-free token window, for PDF tables) |
| EvidenT alignment | cull `evident_align` | `stages/cull.py` | LLM extracts minimal verbatim excerpts; deterministic lexical alignment drops any excerpt not found in its passage | No trained aligner; same matcher as above |
| rag-facts-check | verify `claim_decompose` | `stages/verify.py` | The answer text is re-decomposed into atomic claims (LLM). A second call verifies each with an evidence id plus quote, and a claim counts as supported only if the quote aligns. | Two calls; replaces generator claims (originals kept in `scratch.generator_claims`) |
| LLM judge (FACTS-style) | verify `llm_judge` | `stages/verify.py` | One call judges all claims: supported / unsupported / contradicted, with confidence | No multi-judge ensemble |
| Cascade (our hypothesis) | verify `cascade` | `stages/verify.py` | Cheap deterministic verifier first; claims with confidence < `escalate_below` go to `llm_judge` on `expensive_model` | Confidences are fixed per deterministic outcome (0.3–0.95) |
| RAGChecker-style diagnostics | scorer | `score.py` | Citation precision/recall at page level, verifier catch rate, false-flag rate | No claim-level recall against gold claims yet |
| rag-facts-check repair (long-form) | `bench facts` `{model}+repair` | `external/facts.py` | claim_decompose finds unsupported atomic claims; one call rewrites the response without them | One revise round |
| FACTS Grounding judge | `bench facts` | `external/facts.py` | FACTS `response_level` prompt (downloaded at runtime) on a fixed judge model | 1 judge, not 3; no eligibility filter |
| Verifier track | `bench vtrack` | `vtrack.py` | Any verify impl scored on (context, response, label) pairs; P/R/F1/BAcc | RAGTruth and HOVER (oracle docs); LLM-AggreFact pending access |
| Local NLI verifier (SummaC-style) | verify `nli_small` | `stages/verify.py` | Per answer sentence, max entailment over 1200-char windows of cited units (cross-encoder nli-deberta-v3-base, CPU) | Off-the-shelf MNLI model; no grounding fine-tune |
| Retrieval | retrieve `bm25` / `dense` / `hybrid` | `retrieve.py` | BM25; bge-small-en-v1.5 embeddings (cached per doc); RRF fusion (k=60). `mpn_filter` restricts scope to docs whose part number is in the question | No reranker |
| Vera-Life evidence state | retrieve param `lifecycle` + repair `warn_stale` | `stages/retrieve.py`, `stages/repair.py` | Within each document family, keep only the newest revision in scope (from `revisions.json`). If an answer rests on a superseded doc with no successor in scope, warn (or abstain) | No explain_conflict / retrieve_more actions; metadata given, not inferred |
| VeriFastScore | — | — | Not implemented. The released model is a Llama-3.1-8B fine-tune: the Llama community licence is not permissive enough for an answer-pipeline component, and an 8B model is too slow on the CPU-only target. Its role (single-pass claim extraction + verification) is covered by `claim_decompose` / `llm_judge` | — |
