"""Static report: CSV of aggregates + self-contained HTML (inline SVG, no JS libraries).

The chart plots overall accuracy against mean $/item (log scale) with one dot per combo.
Pareto-optimal combos (no other combo is both cheaper and more accurate) are drawn in the
accent color and labelled; dominated combos are muted. Hovering a dot shows its numbers.
"""

from __future__ import annotations

import csv
import html
import math
from pathlib import Path

from gqb.score import Aggregate

CSS = """
:root{--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--grid:#e6e5e1;--accent:#2a78d6;
--muted:#a9a8a2;--row:#f3f2ef}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--surface:#1a1a19;
--ink:#f0efec;--ink2:#c3c2b7;--grid:#33332f;--accent:#3987e5;--muted:#6b6a65;--row:#232321}}
:root[data-theme=dark]{--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--grid:#33332f;
--accent:#3987e5;--muted:#6b6a65;--row:#232321}
body{background:var(--surface);color:var(--ink);font:14px/1.45 system-ui,sans-serif;
margin:0 auto;max-width:1100px;padding:24px 16px}
h1{font-size:20px;margin:0 0 4px}h2{font-size:16px;margin:28px 0 8px}
p.note{color:var(--ink2);margin:0 0 16px}
.wrap{overflow-x:auto}table{border-collapse:collapse;font-variant-numeric:tabular-nums;
width:100%}th,td{padding:4px 8px;text-align:right;white-space:nowrap}
th:first-child,td:first-child{text-align:left}th{color:var(--ink2);font-weight:600;
border-bottom:1px solid var(--grid)}tr:nth-child(even) td{background:var(--row)}
svg text{fill:var(--ink2);font-size:11px}svg .lbl{fill:var(--ink);font-size:11px}
svg .dot{stroke:var(--surface);stroke-width:2}svg .dot:hover{stroke:var(--ink)}
"""

COLS = [
    ("n", "n", "{:d}"),
    ("accuracy", "acc", "{:.3f}"),
    ("grounded_accuracy", "grounded acc", "{:.3f}"),
    ("selective_accuracy", "sel.acc", "{:.3f}"),
    ("hallucination_rate", "halluc.", "{:.3f}"),
    ("source_hit", "source hit", "{:.3f}"),
    ("attributed", "attributed", "{:.3f}"),
    ("false_abstain_rate", "false abst.", "{:.3f}"),
    ("abstain_recall", "abst. recall (T5)", "{:.3f}"),
    ("cite_precision", "cite P", "{:.3f}"),
    ("cite_recall", "cite R", "{:.3f}"),
    ("distractor_rate", "distractor", "{:.3f}"),
    ("consistency", "consist.", "{:.3f}"),
    ("verifier_catch", "verif. catch", "{:.3f}"),
    ("verifier_false_flag", "verif. false flag", "{:.3f}"),
    ("error_rate", "err", "{:.3f}"),
    ("cost_per_item", "$/item", "{:.5f}"),
    ("latency_p50_ms", "p50 ms", "{:.0f}"),
    ("latency_p95_ms", "p95 ms", "{:.0f}"),
    ("calls_per_item", "calls", "{:.1f}"),
]

TIER_NAMES = {
    "all": "All tiers",
    "1": "Tier 1: direct",
    "2": "Tier 2: conditional",
    "3": "Tier 3: derived",
    "4": "Tier 4: cross-doc comparison",
    "5": "Tier 5: unanswerable (all)",
    "5:near_miss_typ": "Tier 5 subset: near-miss (typ asked, only limits stated)",
    "6": "Tier 6: distractor",
}


def write_csv(aggs: list[Aggregate], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(Aggregate.model_fields)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for a in aggs:
            w.writerow(a.model_dump())


def pareto(points: list[tuple[str, float, float]]) -> set[str]:
    """Names of points not dominated (lower-or-equal cost and higher-or-equal accuracy)."""
    front = set()
    for name, c, a in points:
        if not any(
            (c2 <= c and a2 >= a) and (c2 < c or a2 > a) for n2, c2, a2 in points if n2 != name
        ):
            front.add(name)
    return front


def _clip(label: str, n: int = 40) -> str:
    return label if len(label) <= n else label[: n - 1] + "…"


def _scatter(rows: list[Aggregate], short: dict[str, str]) -> str:
    W, H, L, R, T, B = 820, 400, 56, 260, 16, 44
    pts = [(r.combo_id, max(r.cost_per_item, 1e-6), r.accuracy) for r in rows]
    if not pts:
        return ""
    front = pareto(pts)
    lo = math.floor(math.log10(min(c for _, c, _ in pts)))
    hi = math.ceil(math.log10(max(c for _, c, _ in pts)))
    hi = hi if hi > lo else lo + 1
    a_lo = max(0.0, math.floor(min(a for *_, a in pts) * 10) / 10 - 0.1)
    pw, ph = W - L - R, H - T - B

    def x(c: float) -> float:
        return L + (math.log10(c) - lo) / (hi - lo) * pw

    def y(a: float) -> float:
        return T + (1 - (a - a_lo) / (1 - a_lo)) * ph

    out = [
        f'<svg viewBox="0 0 {W} {H}" width="100%" role="img" '
        f'aria-label="Accuracy versus cost per item, one dot per combo">'
    ]
    for e in range(lo, hi + 1):
        xx = x(10**e)
        out.append(
            f'<line x1="{xx:.1f}" x2="{xx:.1f}" y1="{T}" y2="{T + ph}" stroke="var(--grid)"/>'
        )
        out.append(f'<text x="{xx:.1f}" y="{T + ph + 16}" text-anchor="middle">${10**e:g}</text>')
    steps = int(round((1 - a_lo) / 0.1))
    for i in range(steps + 1):
        a = a_lo + i * 0.1
        yy = y(a)
        out.append(
            f'<line x1="{L}" x2="{L + pw}" y1="{yy:.1f}" y2="{yy:.1f}" stroke="var(--grid)"/>'
        )
        out.append(f'<text x="{L - 6}" y="{yy + 4:.1f}" text-anchor="end">{a:.1f}</text>')
    out.append(
        f'<text x="{L + pw / 2}" y="{H - 6}" text-anchor="middle">'
        f"mean $ per item (log scale)</text>"
    )
    out.append(
        f'<text x="14" y="{T + ph / 2}" text-anchor="middle" '
        f'transform="rotate(-90 14 {T + ph / 2})">accuracy (all tiers)</text>'
    )
    front_pts = sorted((p for p in pts if p[0] in front), key=lambda p: p[1])
    if len(front_pts) > 1:
        d = " ".join(f"{x(c):.1f},{y(a):.1f}" for _, c, a in front_pts)
        out.append(
            f'<polyline points="{d}" fill="none" stroke="var(--accent)" '
            f'stroke-width="2" stroke-dasharray="4 3" opacity="0.6"/>'
        )
    for name, c, a in sorted(pts, key=lambda p: p[0] in front):
        on = name in front
        r = next(r for r in rows if r.combo_id == name)
        tip = html.escape(f"{name}\naccuracy {a:.3f}\n${c:.5f}/item\np95 {r.latency_p95_ms:.0f} ms")
        out.append(
            f'<circle class="dot" cx="{x(c):.1f}" cy="{y(a):.1f}" r="{6 if on else 5}" '
            f'fill="var({"--accent" if on else "--muted"})"><title>{tip}</title></circle>'
        )
        if on:
            out.append(
                f'<text class="lbl" x="{x(c) + 9:.1f}" y="{y(a) + 4:.1f}">'
                f"{html.escape(short[name])}</text>"
            )
    out.append("</svg>")
    return "".join(out)


def _table(rows: list[Aggregate], short: dict[str, str]) -> str:
    head = "<tr><th>combo</th>" + "".join(f"<th>{h}</th>" for _, h, _ in COLS) + "</tr>"
    body = []
    for r in sorted(rows, key=lambda r: -r.accuracy):
        cells = []
        for key, _, fmt in COLS:
            v = getattr(r, key)
            cells.append(f"<td>{'–' if v is None else fmt.format(v)}</td>")
        body.append(
            f'<tr><td title="{html.escape(r.combo_id)}">{html.escape(short[r.combo_id])}'
            f"</td>{''.join(cells)}</tr>"
        )
    return f'<div class="wrap"><table>{head}{"".join(body)}</table></div>'


def short_label(combo_id: str) -> str:
    """'bm25|none|cite|span_check|abstain@generate=x' -> 'cite + span_check + abstain @x'."""
    slots, _, rest = combo_id.partition("@")
    slots, _, params = slots.partition("{") if "@" not in combo_id else (slots, "", "")
    parts = slots.split("|")
    keep = [p for i, p in enumerate(parts) if p != "none" and not (i == 0 and p == "bm25")]
    label = " + ".join(keep)
    if rest:
        models, _, params = rest.partition("{")
        label += " @" + ",".join(m.split("=")[1].replace("claude-", "") for m in models.split(","))
    if params:
        label += " {" + params.rstrip("}").replace("verify.", "") + "}"
    return label


def write_html(aggs: list[Aggregate], path: Path, title: str = "Groundedness benchmark") -> None:
    combos = sorted({a.combo_id for a in aggs})
    short = {c: short_label(c) for c in combos}
    overall = [a for a in aggs if a.tier == "all"]
    parts = [
        f"<!doctype html><html><head><meta charset='utf-8'>"
        f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{html.escape(title)}</title><style>{CSS}</style></head><body>",
        f"<h1>{html.escape(title)}</h1>",
        "<p class='note'>One dot per combo, labelled by its non-default slots (cull + generate + "
        "verify + repair @models {params}); retrieval is BM25 unless named. Blue dots are "
        "Pareto-optimal: no other combo is both cheaper and more accurate. "
        "Hover a dot for details. Cost counts cached calls at list price.</p>",
        _scatter(overall, short),
    ]
    for tier in ["all", "1", "2", "3", "4", "5", "5:near_miss_typ", "6"]:
        rows = [a for a in aggs if a.tier == tier]
        if rows:
            parts.append(f"<h2>{TIER_NAMES[tier]}</h2>{_table(rows, short)}")
    parts.append("</body></html>")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(parts))
