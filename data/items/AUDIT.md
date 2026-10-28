# Item audit (agent audit, 2026-10-27)

**Method.** This audit replaces the planned ≥300-item human audit. A stratified random sample of
the **test** split was drawn: 10 items per tier (seed 2026, `random.Random(2026).sample` per tier
in file order). That gives 60 items, about 14% of test. A coding agent (Claude Opus 5.5) checked
each one. That is not independent of the model family that extracted and cross-checked the items,
as a human audit would be. For every item
it read the question, the gold answer/options, the gold quote, and where anything was unclear, the
page text. Checks:
1. the gold value is what the cited row states, for the asked part, condition and min/typ/max column;
2. T3 arithmetic is correct;
3. T4 picks the right option;
4. T5 items are really unanswerable (near-miss: no typ anywhere for that row);
5. T6 gold is not the distractor.

**Result: 0 / 60 wrong gold answers** (95% upper bound ≈ 5%, rule of three). Three wording
issues do not change the answer:

| Item | Issue | Severity |
|---|---|---|
| `ti_tps51285:t1:00857` | "minimum VIN range under design procedure step 1": a design-example value, not a spec limit | wording |
| `ti_mspm0c1104:t2:00563` | asks for the "typical" CDM ESD rating; ESD ratings are single values (±500 V) | wording |
| `renesas_rx26t:t4:00970` | MSPM0C1104 5.5 V applies only to 5-V-tolerant pins; the comparison result is the same | wording |

Rows double-checked against the page: `ti_tda4vm:t2:00661` (RΘJA 6 °C/W at 3 m/s; the table's
columns are value and airflow), `ti_tps51285:t5:00872` (the named pins do not exist on this part,
so the item is correctly unanswerable), `renesas_rx72t:t6:00939` (VOL 0.5 V at IOL = 1.0 mA).

Together with the independent Opus cross-check of every item (STATS.md; disagreements are moved to
`disputed.jsonl` and excluded), this is the quality evidence for the item set. `audited` stays
`false` on items, because it is reserved for human audit (`bench audit`).
