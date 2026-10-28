"""retrieve slot."""

from __future__ import annotations

import json
import re
from functools import cache
from pathlib import Path

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


@cache
def revisions(corpus_root: Path) -> dict[str, dict]:
    """Version metadata ``{doc_id: {family, revision, date, superseded_by}}``, if the corpus
    has any (``revisions.json`` next to the doc folders)."""
    path = corpus_root / "revisions.json"
    return json.loads(path.read_text()) if path.exists() else {}


def lifecycle_scope(scope: list[str], revs: dict[str, dict]) -> tuple[list[str], list[str]]:
    """Vera-Life-style evidence state: within each document family, keep only the newest
    revision in scope. Returns (scope, stale docs kept because nothing newer is in scope but
    their metadata says they were superseded)."""
    newest: dict[str, str] = {}
    for d in scope:
        r = revs.get(d)
        if r and (r["family"] not in newest or r["date"] > revs[newest[r["family"]]]["date"]):
            newest[r["family"]] = d
    kept = [d for d in scope if d not in revs or newest[revs[d]["family"]] == d]
    return kept, [d for d in kept if d in revs and revs[d].get("superseded_by")]


class BM25Stage(BaseStage):
    """Params: ``top_k``; ``mpn_filter`` (corpus scope only: restrict to docs whose part
    number the question names, falling back to the whole corpus); ``lifecycle`` (drop
    superseded revisions using the corpus's ``revisions.json``; records still-stale docs in
    ``scratch["stale_docs"]`` for the ``warn_stale`` repair)."""

    slot = "retrieve"
    name = "bm25"
    uses_llm = False

    def retriever(self):
        return self.ctx.retriever

    def apply(self, state: PipelineState, llm: StageMeter) -> PipelineState:
        scope = state.doc_ids if self.ctx.scope == "item" else self.ctx.corpus.doc_ids()
        if self.ctx.scope != "item" and self.params.get("mpn_filter"):
            scope = mpn_scope(state.question, self.ctx) or scope
        if self.params.get("lifecycle"):
            scope, stale = lifecycle_scope(scope, revisions(self.ctx.corpus.root))
            state.scratch["stale_docs"] = stale
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
