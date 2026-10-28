"""Scoring. Per-record metrics, then aggregation per (combo, tier).

Correctness:
  numeric       - relative error <= item.tolerance after SI-prefix conversion
  enum          - the gold option (and no other listed option) is named in the answer
  text          - not scored here (no free-text items yet); see docs/DESIGN.md
  unanswerable  - correct iff the system abstained
Groundedness: page-level citation precision/recall vs gold_evidence, over cited evidence units.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Iterable

from pydantic import BaseModel

from gqb.dataset.qc import enum_match
from gqb.schemas import AnswerType, BenchItem, RunRecord, Tier, Verdict
from gqb.textmatch import normalize
from gqb.units import numeric_match


class RecordScore(BaseModel):
    combo_id: str
    item_id: str
    tier: int
    kind: str | None = None  # item subtype, e.g. near_miss_typ (reported as its own bucket)
    repeat: int
    error: bool
    abstained: bool
    correct: bool
    answer_key: str  # normalized answer for consistency
    cite_precision: float | None
    cite_recall: float | None
    supported_frac: float | None
    distractor_hit: bool
    raw_correct: bool | None  # answer value correct before repair/abstention (None: no value)
    flagged: bool | None  # verifier marked any claim unsupported/contradicted (None: no verify)
    cost_usd: float
    latency_ms: float
    calls: int
    in_tok: int
    out_tok: int


def _answer_key(rec: RunRecord) -> str:
    a = rec.state.answer
    if rec.state.abstained or a is None or a.value is None:
        return "<abstain>"
    if isinstance(a.value, float | int):
        return f"{float(a.value):.6g} {a.unit or ''}".strip()
    return normalize(str(a.value))


def enum_correct(value: object, unit: str | None, item: BenchItem) -> bool:
    """Enum (which-part) answers. A numeric answer counts if it equals the gold option's stated
    value and not any other option's (models often answer with the winning value)."""
    options = item.meta.get("options", [])
    if isinstance(value, float | int):
        vals, units = item.meta.get("values", {}), item.meta.get("units", {})
        tol = item.tolerance or 0.01

        def hit(o: str) -> bool:
            return o in vals and numeric_match(
                float(value), unit, float(vals[o]), units.get(o), tol
            )

        return hit(str(item.gold_answer)) and not any(
            hit(o) for o in options if o != item.gold_answer
        )
    return enum_match(str(value), str(item.gold_answer), options)


def score_record(rec: RunRecord, item: BenchItem) -> RecordScore:
    st = rec.state
    error = any(t.error for t in st.trace)
    abstained = st.abstained or st.answer is None or st.answer.value is None or error
    correct = False
    if item.answer_type == AnswerType.UNANSWERABLE:
        correct = abstained and not error
    elif not abstained:
        v = st.answer.value
        if item.answer_type == AnswerType.NUMERIC and isinstance(v, float | int):
            correct = numeric_match(
                float(v),
                st.answer.unit,
                float(item.gold_answer),
                item.unit,
                item.tolerance or 0.01,
            )
        elif item.answer_type == AnswerType.ENUM:
            correct = enum_correct(v, st.answer.unit, item)

    cite_p = cite_r = None
    if not abstained and st.answer and item.gold_evidence:
        units = {u.id: u for u in st.evidence}
        cited = {
            (units[c].doc_id, units[c].page)
            for cl in st.answer.claims
            for c in cl.citations
            if c in units
        }
        gold = {(e.doc_id, e.page) for e in item.gold_evidence}
        if cited:
            hit = len(cited & gold)
            cite_p, cite_r = hit / len(cited), hit / len(gold)

    supported = None
    if st.verdicts:
        supported = sum(v.verdict == Verdict.SUPPORTED for v in st.verdicts) / len(st.verdicts)

    distractor_hit = False
    dv = item.meta.get("distractor_value")
    if dv is not None and not abstained and isinstance(st.answer.value, float | int):
        distractor_hit = numeric_match(
            float(st.answer.value), st.answer.unit, float(dv), item.unit, item.tolerance or 0.01
        )

    raw_correct = None
    a = st.answer
    if a is not None and a.value is not None and item.answer_type == AnswerType.NUMERIC:
        raw_correct = isinstance(a.value, float | int) and numeric_match(
            float(a.value), a.unit, float(item.gold_answer), item.unit, item.tolerance or 0.01
        )
    elif a is not None and a.value is not None and item.answer_type == AnswerType.ENUM:
        raw_correct = enum_correct(a.value, a.unit, item)
    elif a is not None and a.value is not None and item.answer_type == AnswerType.UNANSWERABLE:
        raw_correct = False  # any value for an unanswerable item is wrong
    has_verify = any(t.stage == "verify" and t.impl != "none" for t in st.trace)
    flagged = (
        any(v.verdict in (Verdict.UNSUPPORTED, Verdict.CONTRADICTED) for v in st.verdicts)
        if has_verify and raw_correct is not None
        else None
    )

    return RecordScore(
        combo_id=rec.combo_id,
        item_id=item.id,
        tier=int(item.tier),
        kind=item.meta.get("kind"),
        repeat=rec.repeat,
        error=error,
        abstained=abstained,
        correct=correct,
        answer_key=_answer_key(rec),
        cite_precision=cite_p,
        cite_recall=cite_r,
        supported_frac=supported,
        distractor_hit=distractor_hit,
        raw_correct=raw_correct,
        flagged=flagged,
        cost_usd=sum(t.cost_usd for t in st.trace),
        latency_ms=sum(t.latency_ms for t in st.trace),
        calls=sum(t.calls for t in st.trace),
        in_tok=sum(t.in_tok for t in st.trace),
        out_tok=sum(t.out_tok for t in st.trace),
    )


class Aggregate(BaseModel):
    combo_id: str
    tier: str  # "1".."6", "all", or "{tier}:{kind}" sub-buckets
    n: int
    accuracy: float
    answered_rate: float
    selective_accuracy: float | None  # correct / answered (answerable tiers)
    false_abstain_rate: float | None  # abstained on answerable items
    abstain_recall: float | None  # tier 5: abstained
    cite_precision: float | None
    cite_recall: float | None
    supported_frac: float | None
    distractor_rate: float | None
    consistency: float | None  # fraction of items whose answer is identical across repeats
    verifier_catch: float | None  # flagged / wrong pre-repair answers (verifier recall)
    verifier_false_flag: float | None  # flagged / correct pre-repair answers
    error_rate: float
    cost_per_item: float
    latency_p50_ms: float
    latency_p95_ms: float
    calls_per_item: float


def _mean(xs: Iterable[float | None]) -> float | None:
    vals = [x for x in xs if x is not None]
    return sum(vals) / len(vals) if vals else None


def _pct(xs: list[float], q: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))]


def aggregate(scores: list[RecordScore]) -> list[Aggregate]:
    groups: dict[tuple[str, str], list[RecordScore]] = defaultdict(list)
    for s in scores:
        groups[(s.combo_id, str(s.tier))].append(s)
        if s.kind:
            groups[(s.combo_id, f"{s.tier}:{s.kind}")].append(s)
        groups[(s.combo_id, "all")].append(s)
    out = []
    for (combo, tier), ss in sorted(groups.items()):
        answerable = [s for s in ss if s.tier != Tier.UNANSWERABLE]
        unans = [s for s in ss if s.tier == Tier.UNANSWERABLE]
        answered = [s for s in answerable if not s.abstained]
        by_item: dict[str, set[str]] = defaultdict(set)
        reps: dict[str, int] = defaultdict(int)
        for s in ss:
            by_item[s.item_id].add(s.answer_key)
            reps[s.item_id] += 1
        multi = [i for i in by_item if reps[i] > 1]
        lat = [s.latency_ms for s in ss]
        out.append(
            Aggregate(
                combo_id=combo,
                tier=tier,
                n=len(ss),
                accuracy=sum(s.correct for s in ss) / len(ss),
                answered_rate=sum(not s.abstained for s in ss) / len(ss),
                selective_accuracy=(sum(s.correct for s in answered) / len(answered))
                if answered
                else None,
                false_abstain_rate=(sum(s.abstained for s in answerable) / len(answerable))
                if answerable
                else None,
                abstain_recall=(sum(s.abstained for s in unans) / len(unans)) if unans else None,
                cite_precision=_mean(s.cite_precision for s in ss),
                cite_recall=_mean(s.cite_recall for s in ss),
                supported_frac=_mean(s.supported_frac for s in ss),
                distractor_rate=_mean(
                    float(s.distractor_hit) for s in ss if s.tier == Tier.DISTRACTOR
                ),
                consistency=(sum(len(by_item[i]) == 1 for i in multi) / len(multi))
                if multi
                else None,
                verifier_catch=_mean(
                    float(s.flagged) for s in ss if s.flagged is not None and not s.raw_correct
                ),
                verifier_false_flag=_mean(
                    float(s.flagged) for s in ss if s.flagged is not None and s.raw_correct
                ),
                error_rate=sum(s.error for s in ss) / len(ss),
                cost_per_item=statistics.fmean(s.cost_usd for s in ss),
                latency_p50_ms=_pct(lat, 0.5),
                latency_p95_ms=_pct(lat, 0.95),
                calls_per_item=statistics.fmean(s.calls for s in ss),
            )
        )
    return out
