# Design

## Goal

Measure which groundedness techniques improve **question→answer correctness**, and at what cost in
**latency, $ and consistency**. The results feed component choices for a later grounded BI / agent platform.
The benchmark is for picking components, not for producing a leaderboard.

## Why stages, not systems

Published approaches sit at different layers. Some are generators (EvidenT, Vera-Life), some are
verifiers (GAVEL, rag-facts-check), and some are metrics (RAGChecker, VeriFastScore). Comparing them as
whole systems mixes those layers up. Instead every approach is split into slot implementations in one fixed
pipeline:

```
retrieve → cull → generate → verify → repair
```

| Slot | Implementations | Notes |
|---|---|---|
| retrieve | `bm25`, `dense`, `hybrid`; params `mpn_filter`, `per_entity`, `lifecycle` | Hold fixed (cached) inside a comparison |
| cull | `none`, `llm_filter`, `evident_align`, `gavel_contract` | Reduce or structure evidence before generation |
| generate | `plain`, `cite`, `gavel_debate` | `cite` = forced claim-level citations to evidence ids |
| verify | `none`, `span_check`, `column_check`, `nli_small`, `claim_decompose`, `gavel_scrutinize`, `llm_judge`, `cascade` | Produces `ClaimVerdict`s |
| repair | `none`, `drop_unsupported`, `regenerate`, `abstain`, `warn_stale` | Acts on verdicts |

Reference mappings:
- **Traditional RAG**: `bm25|none|cite|none|none`
- **EvidenT-like**: `bm25|evident_align|cite|span_check|drop_unsupported`
- **GAVEL-like**: `bm25|gavel_contract|gavel_debate|gavel_scrutinize|abstain`
- **rag-facts-check-like**: `bm25|none|cite|claim_decompose|drop_unsupported`

### Verifiers reused as pre-generation culling

Verifier logic can run **before** generation as evidence selection. Score each candidate passage
for whether it supports the question's likely answer, and keep only the passages that pass. This is
`gavel_contract` and `evident_align` in the cull slot.

### Cascade verification (main hypothesis)

`cascade` runs cheap checks on every claim: deterministic span/ID/number matching, then a small NLI model or
small LLM. Only claims below a confidence threshold go to the expensive verifier (GAVEL debate or a large-model judge).
Sweeping the threshold traces out a cost/accuracy curve. The hypothesis is that this beats any single verifier on the Pareto front.

### Known weakness to probe

Lexical, span and entailment verifiers assume a claim appears in, or is implied by, the source text.
**Derived claims** (tier 3: computed values) do not, so these verifiers will flag correct answers as
unsupported. Tier 3 exists to measure this. A `derived=True` claim may need a calculation check
(re-run the arithmetic over cited values) instead of a span match.

## Metrics

| Dimension | Metric |
|---|---|
| Correctness | numeric: relative error ≤ `tolerance` after unit normalization; enum: normalized exact match; text: fixed LLM judge |
| Groundedness | page-level citation precision and recall against `gold_evidence`; fraction of claims supported |
| Abstention | precision and recall of abstaining on tier 5; false abstention rate on tiers 1–3 and 6 |
| Consistency | answer agreement across k repeats; verdict flip rate |
| Cost | in/out tokens, number of LLM calls, $ per item, per slot (from `StageTrace`) |
| Speed | p50/p95 latency, end-to-end and per slot |

Report: one row per (combo, tier). Pareto plots of accuracy against $/item and against p95 latency.

## Model sweeps

Each slot's model is set separately (`ComboSpec.models`). Run small, medium and large models per slot
(for example Haiku/Sonnet/Opus) to find where a cheap model is good enough. Verification and culling often are; generation
often is not.

## Controls

- Fixed retrieval candidates per item (cache).
- Temperature 0 by default. Consistency runs use k repeats at the configured temperature.
- The judge model is never part of the combo being scored.
- The LLM disk cache is keyed on the full request, so reruns are free and deterministic.
