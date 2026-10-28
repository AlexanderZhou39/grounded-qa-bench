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
| 1 | Datasheet QA set: 29 public datasheets, 560 cross-checked items (dev 117 / test 443) | done |
| 2 | Pluggable LLM backend, BM25/dense/hybrid retrieval, 16 stage impls, runner, scorer, HTML report | done (see ROADMAP) |
| 3 | External sets: TAT-QA (QA), RAGTruth + HOVER (verifier track), FACTS Grounding (long-form), lifecycle/supersession (Vera-Life-style) | done; LLM-AggreFact gated |

Results: [docs/RESULTS.md](docs/RESULTS.md). Headlines:
- Haiku + hybrid retrieval + part-number filter + cite reaches 0.935 at corpus scope for $0.00084/item.
- On "near-miss" unanswerables, full GAVEL lifts Haiku from 0.47 to 0.89, close to Opus (0.93), at 1/16 of the cost.
- Post-hoc verifiers catch few errors on short answers. Verify→revise lifts long-form grounding (FACTS 0.85→0.975).
- Held-out test (493 items): Opus cite 0.920, Sonnet 0.898, Haiku + GAVEL evidence contract 0.876, Haiku cite
  0.833.
- A single Haiku judge call beats decompose-then-verify (RAGTruth, HOVER) at a fraction of the cost.
- Superseded document revisions are handled in context once revision metadata is visible. Pipelines that re-extract evidence must carry that metadata, or they answer from stale sources without warning.

Final run: [docs/FINAL_RUN.md](docs/FINAL_RUN.md) (~$50 at list prices, under $35 without the Opus row; CPU-only). LLM backends: [docs/ADAPTERS.md](docs/ADAPTERS.md).
Item counts: [data/items/STATS.md](data/items/STATS.md). Item audit: [data/items/AUDIT.md](data/items/AUDIT.md).

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

## Licences

Answer-pipeline components are all permissively licensed. The benchmark uses them as the components
that would ship in production:

| Component | Licence |
|---|---|
| rank-bm25 | Apache-2.0 |
| BAAI/bge-small-en-v1.5 (dense retrieval) | MIT |
| cross-encoder/nli-deberta-v3-base (`nli_small`) | Apache-2.0 |
| sentence-transformers, PyTorch | Apache-2.0, BSD-3 |
| pypdfium2 (PDF text) | Apache-2.0 / BSD-3 |
| rapidfuzz, pydantic, numpy, pillow | MIT, MIT, BSD-3, MIT-CMU |

Evaluation data is downloaded at runtime and never committed. TAT-QA: CC BY 4.0. RAGTruth: MIT.
FACTS Grounding public set: CC BY 4.0. LLM-AggreFact: CC BY-ND 4.0, gated. HOVER: CC BY-SA 4.0.
