"""retrieve slot."""

from __future__ import annotations

from gqb.llm import StageMeter
from gqb.schemas import PipelineState
from gqb.stages.base import BaseStage


class BM25Stage(BaseStage):
    slot = "retrieve"
    name = "bm25"
    uses_llm = False

    def apply(self, state: PipelineState, llm: StageMeter) -> PipelineState:
        scope = state.doc_ids if self.ctx.scope == "item" else self.ctx.corpus.doc_ids()
        k = int(self.params.get("top_k", self.ctx.top_k))
        state.candidates = self.ctx.retriever.search(state.question, scope, k=k)
        return state
