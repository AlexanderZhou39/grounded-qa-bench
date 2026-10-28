# Final run: configuration, sample sizes, cost

Projected cost at public list prices (USD per 1M tokens in/out: Haiku 0.10/0.50, Sonnet 2/10,
Opus 4/20) is **about $45**. The projection uses per-item costs measured in the dev sweeps,
scaled by 1.27 for corpus scope (the measured ratio for hybrid+MPN against item scope). Everything
runs CPU-only. Local models are bge-small (embeddings, ~130 MB) and nli-deberta-v3-base (~740 MB).
They need the extra: `uv sync --extra local`.

| Step | Command | Items | Projected $ |
|---|---|---|---|
| Datasheet QA, Haiku combos, 2 repeats | `bench run configs/final_datasheet.yaml` | 443 test | 12 |
| Datasheet QA, Sonnet/Opus reference rows, 1 repeat | `bench run configs/final_datasheet_big.yaml` | 443 test | 24 |
| TAT-QA | `bench run configs/tatqa.yaml` | 150 | 2 |
| RAGTruth verifier track (llm_judge, claim_decompose × Haiku/Sonnet; nli_small free) | `bench vtrack ...` | 150 pairs | 1 |
| FACTS Grounding, 3 generators, Opus judge | `bench facts --generator ... --n 60` | 60 | 5 |
| **Total** | | | **~45** |

Ways to cut cost:
- Run the FACTS judge through the file queue with a coding agent (`docs/ADAPTERS.md`). That removes
  about 70% of the FACTS cost.
- Drop the Opus generator row. It is about $14 and, on dev, adds only about 2 points over Sonnet.
- Add `--limit` to the big config. It is tier-balanced.

```bash
uv sync --extra local
export GQB_DEVICE=cpu                       # default; mps/cuda also accepted
uv run bench verify-corpus && uv run bench build-corpus
uv run bench run configs/final_datasheet.yaml && uv run bench run configs/final_datasheet_big.yaml
uv run bench report runs/final_datasheet --items data/items/test.jsonl
uv run bench spend                          # what the cache actually holds, at list prices
```

## Sample sizes, and what they can resolve

At p ≈ 0.9 the 95% interval half-width is about 1.96·√(p(1-p)/n). Combo comparisons are paired on
the same items, so real differences smaller than these unpaired widths are still detectable.

| Slice (test split) | n | ±95% at p=0.9 | Use |
|---|---|---|---|
| all | 443 | 2.8 pt | headline ranking |
| T1 direct | 145 | 4.9 pt | |
| T5 unanswerable | 122 | 5.3 pt | abstention |
| 5:near_miss_typ | 79 | 6.6 pt | the slice that separates verifiers (dev spread 0.42–0.93) |
| T3 derived | 73 | 6.9 pt | |
| T2 conditional | 61 | 7.5 pt | |
| T6 distractor | 29 | 11 pt | directional only |
| T4 cross-doc | 13 | 16 pt | directional only. More needs more same-family docs |
| FACTS | 60 | 9 pt at p=0.85 | repair effect (dev +12.5 pt) is resolvable; generator ranking is not |
| RAGTruth | 150 | | F1/BAcc against the paper's prompt baselines |

The minimum to keep is the full 443 test items for the cheap rows. Do not subsample the near-miss slice.

## Reproducibility notes

- PDF text extraction switched from pymupdf to pypdfium2, so that every component of the answer pipeline is
  permissively licensed. Page text differs in whitespace and table order, so prompts change and
  cached generations do not carry over. The published dev numbers in RESULTS.md were produced on
  pymupdf text. `configs/sweep_dev.yaml` and `sweep_corpus.yaml` pin `corpus: data/local/corpus_pymupdf`
  to reproduce them. The final run uses the default `data/corpus` (pypdfium2). All gold quotes validate
  against both extractions (`bench validate-items`).
- Items are generated deterministically (`bench make-items --seed 13`). Raising
  `--near-miss-per-doc` only appends items and never renumbers existing ones.
