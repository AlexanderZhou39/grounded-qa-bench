# Agent instructions

Start here. Read `docs/DESIGN.md` before writing any stage code and `docs/DATASET.md` before touching data.
The current task list is `docs/ROADMAP.md`: pick the first unchecked box in the lowest unfinished phase.

## Layout

```
src/gqb/schemas.py     # data contracts: BenchItem, PipelineState, StageTrace, RunRecord. Keep stable.
src/gqb/stage.py       # SLOTS, ComboSpec (combo_id includes models + params), Pipeline
src/gqb/llm.py         # LLMClient protocol, Anthropic / OpenAI-compat / Routing / Cached / Fake
                       # clients, PRICES, StageMeter (per-stage cost+latency accounting)
src/gqb/stages/        # stage impls per slot; REGISTRY + build_pipeline in __init__.py
src/gqb/runner.py      # RunConfig/SweepConfig (YAML), run_combo (threaded, resumable)
src/gqb/score.py       # per-record scores + per-(combo, tier) aggregates
src/gqb/report.py      # CSV + static HTML (Pareto scatter, per-tier tables)
src/gqb/retrieve.py    # BM25Retriever
src/gqb/textmatch.py   # normalize / find_span (exact→normalized→squashed→fuzzy) / numbers
src/gqb/units.py       # SI-prefix conversion, numeric_match
src/gqb/corpus.py      # manifest, page text, passages
src/gqb/dataset/       # extract (facts), generate (items), qc (paraphrase, cross-check)
src/gqb/cli.py         # `bench` CLI
configs/*.yaml         # sweeps: smoke.yaml, sweep_dev.yaml, example.yaml
data/manifest.jsonl    # corpus manifest (committed)
data/items/*.jsonl     # dev / test / disputed items + STATS.md (committed)
data/pdfs/ data/corpus/ data/local/ runs/ .cache/   # gitignored
```

## Commands

```bash
uv sync && uv run pytest
uv run ruff check . && uv run ruff format .
uv run bench verify-corpus && uv run bench build-corpus
uv run bench validate-items data/items/dev.jsonl
uv run bench run configs/sweep_dev.yaml [--only evident_align] [--limit 20]
uv run bench report runs/sweep_dev
# Phase 3 external sets (downloaded into data/external/, never committed)
uv run bench fetch-external tatqa && uv run bench run configs/tatqa.yaml
uv run bench report runs/tatqa --items data/external/tatqa/items.jsonl
uv run bench fetch-external ragtruth && uv run bench vtrack --verifier llm_judge --model claude-haiku-5-5
uv run bench facts --n 40          # long-form + FACTS judge; generator "{model}+repair" = verify->revise
uv sync --extra local              # CPU models for dense/hybrid retrieval and nli_small
uv run bench run configs/sweep_retrieval.yaml
# final run: docs/FINAL_RUN.md
```

## Adding a stage

1. Subclass `BaseStage` in `src/gqb/stages/<slot>.py`. Set `slot` and `name`; set
   `uses_llm = False` for deterministic stages. Implement `apply(state, llm)`, and make LLM calls
   through `self.complete(llm, ...)` so the call is metered.
2. Register it in `REGISTRY` in `stages/__init__.py`.
3. `tests/test_pipeline_e2e.py` runs every registered impl; script any new prompt in `responder`.
4. Add a combo to a config. `params` reach the stage as `self.params` and are part of `combo_id`.

## Rules

- Every model call goes through `LLMClient`. Stages never import provider SDKs directly; otherwise
  cost and latency attribution breaks.
- Every `Stage.run` appends exactly one `StageTrace` (summed tokens, cost and latency of all calls in the stage).
- `retrieve` is deterministic (BM25 / dense / hybrid, cached on disk), so every combo with the same
  retriever sees identical candidates. Comparisons are only valid when candidates are held fixed.
- Pipeline components must be permissively licensed (Apache/MIT/BSD). Evaluation data may carry
  other licences; never vendor it into the repo.
- LLM backend is pluggable: see docs/ADAPTERS.md. Stages must not assume a provider.
- The judge model used by `score.py` for free-text answers must not appear in the combo being scored.
- Scoring buckets by `Tier`. Don't report only a single overall number.
- Do not commit PDFs, generated page text, `runs/`, API keys, or any non-public data.
- Changing a field in `schemas.py` means updating `tests/test_schemas.py` and existing JSONL in `data/items/`.
