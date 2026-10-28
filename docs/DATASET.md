# Dataset: datasheet grounded QA

## Why datasheets

Real, messy documents with tables and text. Values depend on test conditions (min/typ/max at given
conditions, footnotes), and many values look alike (several pins, package variants, operating modes).
That is a reasonable stand-in for the tabular and descriptive claims in business data, and everything comes from public sources.

## Corpus

- 29 public vendor datasheets (TI, Renesas, Microchip, Micron, ADI, Infineon/Cypress, Coilcraft,
  Abracon, Panasonic, ...), 2,431 pages. List: `tools/export/corpus_allowlist.yaml`.
- Excluded: customer-prefixed or renamed files, hash-named files, edited variants, NDA/confidential
  docs, scanned drawings, and user manuals over 1000 pages.
- `doc_id = {vendor}_{mpn}`, lowercase, for example `ti_tps51388`.
- `data/manifest.jsonl` has one line per doc:
  `{"doc_id", "filename", "sha256", "vendor", "mpn", "family", "pages", "url"}`.
- PDFs are not in git. Unzip `datasheets.zip` into `data/pdfs/`, then run `bench verify-corpus`
  and `bench build-corpus`. Page text goes to `data/corpus/{doc_id}/{page:04d}.txt`.
- Pages are **1-indexed in the original PDF**. Retrieval passages are chunks of at most 1800 chars,
  with ids `{doc_id}:{page}:{chunk}`.

## Item schema

See `gqb.schemas.BenchItem`. One JSON object per line (real dev item):

```json
{"id": "abracon_abm8:t1:00006", "tier": 1, "question": "What is the typical drive level specified for the ABM8 crystal?", "answer_type": "numeric", "gold_answer": 10.0, "unit": "uW", "tolerance": 0.005, "conditions": [], "doc_ids": ["abracon_abm8"], "gold_evidence": [{"doc_id": "abracon_abm8", "page": 1, "quote": "Drive Level                                         10          100    µW"}], "source": "datasheet", "audited": false, "meta": {"field": "typ", "section": "electrical", "mpn": "ABM8", "crosscheck": "agree", "template_question": "What is the typical Drive Level of the ABM8?"}}
```

## Tiers

| Tier | Name | Construction | Tests |
|---|---|---|---|
| 1 | direct | Parameter with a single row in the doc; one of min/typ/max | basic grounding |
| 2 | conditional | Parameter with several rows; question names the conditions of the row whose value differs from the others | picking the right row/condition |
| 3 | derived | `range_width` (max-min), `max_over_typ_pct`, `absmax_headroom` (abs-max minus recommended max); gold computed in code, tolerance 2% | verifiers that wrongly reject derived claims |
| 4 | cross-doc | "Which part has the higher/lower {min/typ/max} {param}: A or B?" over two same-family parts that each state the parameter exactly once; values differ by >=5% after unit conversion. `answer_type: enum`, gold = MPN, `meta.options` / `meta.values` | multi-doc retrieval and comparison; enum scoring requires the gold option and no other option in the answer |
| 5 | unanswerable | A parameter a same-family sibling part states, which this doc never mentions (no page covers >=60% of its content words; symbol absent) | abstention |
| 6 | distractor | Tier-1 question whose `doc_ids` also contains a same-family sibling stating the same parameter with a different value (`meta.distractor_value`) | confusion between similar parts |

Current counts: `data/items/STATS.md`.

## How gold facts were made

The local extraction DBs turned out to be nearly empty, so facts were **freshly extracted**, with
deterministic checks deciding what survives:

1. **Page selection** (`gqb.dataset.extract.select_pages`): up to 8 pages per doc, scored by
   section keywords (absolute maximum, recommended operating, electrical characteristics,
   thermal). The best page per section is guaranteed.
2. **LLM proposes rows** (Sonnet 5.5, page text plus page image): parameter, symbol, conditions,
   min/typ/max, unit, and a **verbatim `row_quote`**.
3. **Deterministic verification** (`verify_fact`) keeps a row only if:
   - the quote is found on the page (exact, then normalized, then squashed, then fuzzy >= 90);
   - every min/typ/max value appears in the quote;
   - the parameter or symbol appears on the page.

   Result: 806 facts kept from 192 pages.
4. **Generation filters** drop rows where:
   - the question would be unreadable (conditions over 160 chars, parameter over 70 chars);
   - the "condition" is another part number (a column header);
   - the value is an offset from a rail (`AVCC2 – 0.5`).
5. **Paraphrase** (Haiku 5.5, never sees the gold answer). The paraphrase is kept only if:
   - the MPN tokens survive;
   - all numbers survive;
   - the min/typ/max word (or a synonym) survives.

   Otherwise the template question is kept. `meta.template_question` holds the original.
6. **Independent cross-check** (Opus 5.5, a different model from the extractor):
   - Numeric items: it answers from the **gold pages only**. Outcomes are `agree`, `disagree` or
     `not_found`.
   - Unanswerable items: it answers from the top-6 BM25 passages of the doc. A value found means
     `answerable_suspect`.
   - Only `agree` / `absent_confirmed` items enter dev/test. The rest go to `disputed.jsonl`,
     which is kept for inspection and never scored.

   What the disputed items show: a sibling part's table attributed to the wrong part, option
   codes extending a range, and a typ value differing between two tables.

## Audit and splits

- `dev.jsonl` (about 100 items, stratified by tier) is for iterating; `test.jsonl` (the rest) is
  for reporting. Both passed the automatic cross-check but are `audited: false` until a human
  reviews them.
- `bench audit data/items/dev.jsonl` shows the question, gold and page text with the quote
  highlighted. a/r/s/q decisions go to `data/local/audit.jsonl`. `bench audit --apply` sets
  `audited: true` on accepted items and moves rejected ones to `disputed.jsonl`.
- Known residual risks:
  - min/max ambiguity for signed quantities (currents are scored sign-insensitive);
  - rows whose datasheet value differs by package or variant without saying so in the
    conditions;
  - tier-5 absence relies on text search plus one LLM check, so OCR-poor pages can hide a value.

## Rebuilding

```bash
uv run bench build-manifest ~/path/to/input_ds --zip-out data/local/datasheets.zip  # author only
uv run bench verify-corpus && uv run bench build-corpus
uv run bench extract-facts       # ~$2.7, cached
uv run bench make-items && uv run bench qc && uv run bench split   # qc ~$5, cached
```
All LLM calls are cached under `.cache/llm/` by request hash, so a rerun on the same inputs is free.
