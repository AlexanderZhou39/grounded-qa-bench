"""Base class for stage implementations."""

from __future__ import annotations

from typing import Any

from gqb.llm import LLMClient, StageMeter
from gqb.schemas import PipelineState
from gqb.stages.common import StageContext


class BaseStage:
    slot: str = ""
    name: str = ""

    def __init__(self, ctx: StageContext, model: str | None = None, **params: Any):
        self.ctx = ctx
        self.model = model or ctx.default_model
        self.params = params

    def run(self, state: PipelineState, llm: LLMClient) -> PipelineState:
        meter = StageMeter(llm, self.slot, self.name, self.model if self.uses_llm else None)
        try:
            with meter:
                state = self.apply(state, meter)
        finally:
            state.trace.append(meter.trace)  # meter records the error, Pipeline stops
        return state

    uses_llm: bool = True

    def apply(self, state: PipelineState, llm: StageMeter) -> PipelineState:
        raise NotImplementedError

    def complete(self, llm: StageMeter, *args: Any, **kw: Any):
        kw.setdefault("effort", self.params.get("effort", self.ctx.effort))
        return llm.complete(*args, **kw)


class NoneStage(BaseStage):
    """Pass-through. For cull it promotes candidates to evidence 1:1."""

    name = "none"
    uses_llm = False

    def __init__(self, ctx: StageContext, slot: str, model: str | None = None, **params: Any):
        super().__init__(ctx, model, **params)
        self.slot = slot

    def apply(self, state: PipelineState, llm: StageMeter) -> PipelineState:
        if self.slot == "cull":
            from gqb.stages.cull import passages_to_units

            state.evidence = passages_to_units(state.candidates)
        return state
