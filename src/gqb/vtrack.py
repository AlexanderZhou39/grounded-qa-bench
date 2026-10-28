"""Verifier track: score verify stages directly on (context, response, label) pairs from
external hallucination-detection sets (RAGTruth; LLM-AggreFact once access is granted).

Each pair becomes a ``PipelineState`` whose evidence is the chunked context and whose answer is
the response with no claims, so verifiers see the implicit claim ``C0`` (the whole response,
citing all evidence) or, for ``claim_decompose``, decompose it themselves. A response counts as
flagged if any verdict is unsupported or contradicted. Deterministic quote verifiers
(span_check, gavel_scrutinize) need generator quotes and are not meaningful here."""

from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from gqb.corpus import Corpus, chunk_page, read_jsonl
from gqb.llm import CachedClient, LLMClient
from gqb.schemas import Answer, EvidenceUnit, PipelineState, Verdict
from gqb.stages import REGISTRY, StageContext


class VPair(BaseModel):
    id: str
    set: str
    task: str
    question: str
    context: str
    response: str
    hallucinated: bool
    meta: dict[str, Any] = Field(default_factory=dict)


class VResult(BaseModel):
    pair_id: str
    task: str
    verifier: str
    hallucinated: bool
    flagged: bool | None  # None = verifier error
    score: float | None = None  # min verdict confidence (support score), for threshold sweeps
    cost_usd: float
    latency_ms: float


def to_state(p: VPair) -> PipelineState:
    units = [
        EvidenceUnit(id=f"E{i}", passage_id=f"{p.id}:{i}", doc_id=p.id, page=1, text=t)
        for i, t in enumerate(chunk_page(p.context), 1)
    ]
    return PipelineState(
        item_id=p.id,
        question=p.question,
        doc_ids=[p.id],
        evidence=units,
        answer=Answer(text=p.response, value=p.response),
    )


def run_verifier(
    verifier: str,
    model: str,
    pairs: list[VPair],
    llm: LLMClient,
    params: dict | None = None,
    workers: int = 8,
    cache_dir: str = ".cache/llm",
) -> list[VResult]:
    params = params or {}
    ctx = StageContext(corpus=Corpus(), retriever=None, default_model=model, effort="low")  # type: ignore[arg-type]
    stage = REGISTRY["verify"][verifier](ctx, model, **params)
    label = f"{verifier}@{model}" + (
        "{" + ",".join(f"{k}={v}" for k, v in sorted(params.items())) + "}" if params else ""
    )
    client = CachedClient(llm, cache_dir)

    def one(p: VPair) -> VResult:
        st = to_state(p)
        try:
            st = stage.run(st, client)
        except Exception:  # noqa: BLE001 (meter recorded the error in the trace)
            pass
        tr = [t for t in st.trace if t.stage == "verify"]
        err = not tr or any(t.error for t in tr)
        flagged = (
            None
            if err
            else any(v.verdict in (Verdict.UNSUPPORTED, Verdict.CONTRADICTED) for v in st.verdicts)
        )
        return VResult(
            pair_id=p.id,
            task=p.task,
            verifier=label,
            hallucinated=p.hallucinated,
            flagged=flagged,
            score=min(
                (v.confidence for v in st.verdicts if v.confidence is not None), default=None
            ),
            cost_usd=sum(t.cost_usd for t in tr),
            latency_ms=sum(t.latency_ms for t in tr),
        )

    with ThreadPoolExecutor(workers) as ex:
        return list(ex.map(one, pairs))


def metrics(results: list[VResult]) -> dict[str, dict[str, float]]:
    """Per task and overall: precision/recall/F1 for the hallucinated class, balanced accuracy
    (the LLM-AggreFact metric), error rate, $/pair."""
    groups: dict[str, list[VResult]] = defaultdict(list)
    for r in results:
        groups[r.task].append(r)
        groups["all"].append(r)
    out = {}
    for g, rs in sorted(groups.items()):
        ok = [r for r in rs if r.flagged is not None]
        tp = sum(r.flagged and r.hallucinated for r in ok)
        fp = sum(r.flagged and not r.hallucinated for r in ok)
        fn = sum(not r.flagged and r.hallucinated for r in ok)
        tn = sum(not r.flagged and not r.hallucinated for r in ok)
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        tnr = tn / (tn + fp) if tn + fp else 0.0
        out[g] = {
            "n": len(rs),
            "precision": prec,
            "recall": rec,
            "f1": 2 * prec * rec / (prec + rec) if prec + rec else 0.0,
            "bacc": (rec + tnr) / 2,
            "error_rate": 1 - len(ok) / len(rs) if rs else 0.0,
            "cost_per_pair": sum(r.cost_usd for r in rs) / len(rs) if rs else 0.0,
            "latency_p50_ms": sorted(r.latency_ms for r in rs)[len(rs) // 2] if rs else 0.0,
        }
    return out


def load_pairs(path: Path) -> list[VPair]:
    return [VPair.model_validate(r) for r in read_jsonl(path)]


def calibrate(results: list[VResult], seed: int = 0) -> dict[str, float]:
    """For score-producing verifiers: pick the support-score threshold that maximizes balanced
    accuracy on a random half, report BAcc/F1 on the other half (flag if score < threshold)."""
    import random

    rs = [r for r in results if r.score is not None]
    random.Random(seed).shuffle(rs)
    cal, test = rs[: len(rs) // 2], rs[len(rs) // 2 :]

    def flagged_at(t: float, xs: list[VResult]) -> list[VResult]:
        return [r.model_copy(update={"flagged": r.score < t}) for r in xs]  # type: ignore[operator]

    grid = sorted({r.score for r in cal})  # type: ignore[misc]
    best = max(grid, key=lambda t: metrics(flagged_at(t, cal))["all"]["bacc"])
    m = metrics(flagged_at(best, test))["all"]
    return {"threshold": best, "test_bacc": m["bacc"], "test_f1": m["f1"], "n_test": len(test)}
