"""retrieve slot."""

from __future__ import annotations

import re

from gqb.llm import StageMeter
from gqb.retrieve import DenseRetriever, HybridRetriever
from gqb.schemas import PipelineState
from gqb.stages.base import BaseStage
from gqb.stages.common import StageContext


def _norm_mpn(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def mpn_scope(question: str, ctx: StageContext) -> list[str]:
    """Docs whose manifest part number appears in the question (entity filter, as a BI
    system would apply from the query's named entities). Empty if none match."""
    q = _norm_mpn(question)
    hits = []
    for doc_id, e in ctx.corpus.manifest.items():
        key = _norm_mpn(e.mpn)
        if len(key) >= 4 and key in q:
            hits.append(doc_id)
    return hits


class BM25Stage(BaseStage):
    """Params: ``top_k``; ``mpn_filter`` (corpus scope only: restrict to docs whose part
    number the question names, falling back to the whole corpus)."""

    slot = "retrieve"
    name = "bm25"
    uses_llm = False

    def retriever(self):
        return self.ctx.retriever

    def apply(self, state: PipelineState, llm: StageMeter) -> PipelineState:
        scope = state.doc_ids if self.ctx.scope == "item" else self.ctx.corpus.doc_ids()
        if self.ctx.scope != "item" and self.params.get("mpn_filter"):
            scope = mpn_scope(state.question, self.ctx) or scope
        k = int(self.params.get("top_k", self.ctx.top_k))
        state.candidates = self.retriever().search(state.question, scope, k=k)
        return state


_SHARED: dict[tuple[int, str], object] = {}


def _dense(ctx: StageContext, model: str) -> DenseRetriever:
    key = (id(ctx.corpus), model)
    if key not in _SHARED:
        _SHARED[key] = DenseRetriever(ctx.corpus, model)
    return _SHARED[key]  # type: ignore[return-value]


class DenseStage(BM25Stage):
    """Local embedding retrieval (extra ``local``). Param ``embed_model``."""

    name = "dense"

    def retriever(self):
        return _dense(self.ctx, self.params.get("embed_model", "BAAI/bge-small-en-v1.5"))


class HybridStage(BM25Stage):
    """BM25 + dense, reciprocal-rank fusion."""

    name = "hybrid"

    def retriever(self):
        dense = _dense(self.ctx, self.params.get("embed_model", "BAAI/bge-small-en-v1.5"))
        return HybridRetriever(self.ctx.retriever, dense)
