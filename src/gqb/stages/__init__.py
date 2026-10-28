"""Stage registry: (slot, impl name) -> class. ``build_pipeline`` turns a ComboSpec into a
runnable Pipeline."""

from __future__ import annotations

from gqb.stage import SLOTS, ComboSpec, Pipeline
from gqb.stages.base import BaseStage, NoneStage
from gqb.stages.common import StageContext
from gqb.stages.cull import EvidentAlignStage, GavelContractStage, LLMFilterStage
from gqb.stages.generate import CiteStage, GavelDebateStage, PlainStage
from gqb.stages.repair import (
    AbstainStage,
    DropUnsupportedStage,
    RegenerateStage,
    WarnStaleStage,
)
from gqb.stages.retrieve import BM25Stage, DenseStage, HybridStage
from gqb.stages.verify import (
    CascadeStage,
    ClaimDecomposeStage,
    ColumnCheckStage,
    GavelScrutinizeStage,
    LLMJudgeStage,
    NLISmallStage,
    SpanCheckStage,
)

REGISTRY: dict[str, dict[str, type[BaseStage]]] = {
    "retrieve": {"bm25": BM25Stage, "dense": DenseStage, "hybrid": HybridStage},
    "cull": {
        "llm_filter": LLMFilterStage,
        "evident_align": EvidentAlignStage,
        "gavel_contract": GavelContractStage,
    },
    "generate": {"plain": PlainStage, "cite": CiteStage, "gavel_debate": GavelDebateStage},
    "verify": {
        "span_check": SpanCheckStage,
        "column_check": ColumnCheckStage,
        "gavel_scrutinize": GavelScrutinizeStage,
        "llm_judge": LLMJudgeStage,
        "claim_decompose": ClaimDecomposeStage,
        "nli_small": NLISmallStage,
        "cascade": CascadeStage,
    },
    "repair": {
        "drop_unsupported": DropUnsupportedStage,
        "abstain": AbstainStage,
        "warn_stale": WarnStaleStage,
        "regenerate": RegenerateStage,
    },
}


def build_pipeline(spec: ComboSpec, ctx: StageContext) -> Pipeline:
    stages = []
    for slot in SLOTS:
        impl = getattr(spec, slot)
        model = spec.models.get(slot)
        params = spec.params.get(slot, {})
        if impl == "none":
            if slot in ("retrieve", "generate"):
                raise ValueError(f"slot {slot} cannot be none")
            stages.append(NoneStage(ctx, slot, model, **params))
            continue
        try:
            cls = REGISTRY[slot][impl]
        except KeyError as exc:
            known = ", ".join(["none", *REGISTRY[slot]])
            raise ValueError(f"unknown {slot} impl {impl!r}; known: {known}") from exc
        stages.append(cls(ctx, model, **params))
    return Pipeline(spec, stages)


__all__ = ["REGISTRY", "StageContext", "build_pipeline"]
