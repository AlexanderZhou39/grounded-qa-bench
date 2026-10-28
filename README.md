# grounded-qa-bench

Benchmark harness for comparing LLM **groundedness / fact-checking approaches** on question→answer tasks.
The approaches are traditional RAG, EvidenT-style evidence alignment, GAVEL-style contract/debate/scrutiny,
rag-facts-check-style claim verification, Vera-Life-style lifecycle handling, and cascades of these.
Each one is measured on **accuracy, groundedness, abstention, consistency, latency and cost**.

Approaches aren't compared as monoliths. Each one is broken into **pipeline stages**:

```
retrieve → cull → generate → verify → repair
```

A "combo" fills each slot with one implementation and one model. The output is a Pareto frontier
of accuracy against cost and latency for each question tier. It tells you which component (and
which model size) is worth using at each stage of a production grounded-QA / BI system.

See [docs/DESIGN.md](docs/DESIGN.md), [docs/METHODS.md](docs/METHODS.md),
[docs/DATASET.md](docs/DATASET.md), [docs/ROADMAP.md](docs/ROADMAP.md).

## Status

| Phase | Scope | State |
|---|---|---|
| 0 | Contracts (`schemas.py`, `stage.py`, `llm.py`), docs | done |
| 1 | Datasheet QA set: 29 public datasheets, 503 cross-checked items (dev 108 / test 395) | done, human audit pending |
| 2 | LLM adapters, BM25, 15 stage impls, runner, scorer, HTML report; item- and corpus-scope sweeps | core done (see ROADMAP) |
| 3 | External sets: TAT-QA (QA), RAGTruth (verifier track), FACTS Grounding (long-form) | done; LLM-AggreFact gated, HOVER deferred |

First results: [docs/RESULTS.md](docs/RESULTS.md). Headline: Haiku+BM25+cite ($0.00066/item, 0.93) is on the Pareto front; verifiers catch few errors on short answers, but verify→revise lifts long-form grounding (FACTS 0.85→0.975). Item counts: [data/items/STATS.md](data/items/STATS.md).

## Quickstart

```bash
uv sync && uv run pytest            # offline; FakeClient e2e over every stage
# data: unzip datasheets.zip into data/pdfs/ (see Data policy)
uv run bench verify-corpus && uv run bench build-corpus
uv run bench validate-items data/items/dev.jsonl
export ANTHROPIC_API_KEY=...        # non-Claude models: OPENAI_BASE_URL / OPENAI_API_KEY
uv run bench run configs/smoke.yaml # 7 combos x 12 items, Haiku, ~$0.05
uv run bench report runs/smoke      # -> runs/smoke/report.html, scores.csv
```

## Data policy

- **PDFs are not in git.** These are public vendor datasheets (TI, Renesas, Microchip, …). Get the zip
  out-of-band and unzip it into `data/pdfs/` as `{doc_id}.pdf`. `data/manifest.jsonl` stores the sha256 of each file
  and its public source URL where known. `bench verify-corpus` checks the files against the manifest.
- **Benchmark items** (`data/items/*.jsonl`) contain only facts stated in those public datasheets, plus page
  references.
- **Page text** (`data/corpus/`) is generated locally from the PDFs and gitignored.
