"""Stage protocol and pipeline composition.

A pipeline is a fixed sequence of slots: retrieve -> cull -> generate -> verify -> repair.
Each slot holds one Stage implementation; ``none`` is a valid implementation for every slot
except generate. A combo is identified by ``ComboSpec.combo_id``.
"""

from __future__ import annotations

import time
from typing import Protocol

from pydantic import BaseModel, Field

from gqb.llm import LLMClient
from gqb.schemas import PipelineState, StageTrace

SLOTS = ("retrieve", "cull", "generate", "verify", "repair")


class Stage(Protocol):
    slot: str  # one of SLOTS
    name: str  # impl name, e.g. "bm25", "evident_align", "gavel_debate"

    def run(self, state: PipelineState, llm: LLMClient) -> PipelineState:
        """Return the updated state. Append exactly one StageTrace for this stage."""
        ...


class ComboSpec(BaseModel):
    """Which impl fills each slot, which model each slot uses, and impl params."""

    retrieve: str = "bm25"
    cull: str = "none"
    generate: str = "cite"
    verify: str = "none"
    repair: str = "none"
    models: dict[str, str] = Field(default_factory=dict, description="slot -> model id")
    params: dict[str, dict] = Field(default_factory=dict, description="slot -> impl kwargs")

    @property
    def combo_id(self) -> str:
        slots = "|".join(getattr(self, s) for s in SLOTS)
        model_map = ",".join(f"{k}={v}" for k, v in sorted(self.models.items()))
        out = f"{slots}@{model_map}" if model_map else slots
        params = ",".join(
            f"{slot}.{k}={v}"
            for slot, kv in sorted(self.params.items())
            for k, v in sorted(kv.items())
        )
        return f"{out}{{{params}}}" if params else out


class Pipeline:
    def __init__(self, spec: ComboSpec, stages: list[Stage]):
        assert [s.slot for s in stages] == list(SLOTS), "stages must fill every slot in order"
        self.spec = spec
        self.stages = stages

    def run(self, state: PipelineState, llm: LLMClient) -> PipelineState:
        for stage in self.stages:
            t0 = time.perf_counter()
            try:
                state = stage.run(state, llm)
            except Exception as exc:  # record and stop; scorer treats as failure
                if state.trace and state.trace[-1].stage == stage.slot and state.trace[-1].error:
                    break  # stage already recorded its own failure
                state.trace.append(
                    StageTrace(
                        stage=stage.slot,
                        impl=stage.name,
                        latency_ms=(time.perf_counter() - t0) * 1000,
                        error=repr(exc),
                    )
                )
                break
        return state
