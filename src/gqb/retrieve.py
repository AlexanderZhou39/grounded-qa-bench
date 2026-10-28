"""Retrievers. Results are cached per (retriever, scope, question) so that every combo in a
comparison sees identical candidates."""

from __future__ import annotations

import hashlib
import json
import re
import threading
from pathlib import Path
from typing import Any

from rank_bm25 import BM25Okapi

from gqb.corpus import Corpus
from gqb.schemas import Passage
from gqb.textmatch import normalize

_TOKEN = re.compile(r"[a-z0-9]+(?:\.[0-9]+)?")


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall(normalize(text))


class BM25Retriever:
    name = "bm25"

    def __init__(self, corpus: Corpus, cache_dir: str | Path | None = ".cache/retrieval"):
        self.corpus = corpus
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self._index: dict[tuple[str, ...], tuple[BM25Okapi, list[Passage]]] = {}

    def _get_index(self, doc_ids: tuple[str, ...]) -> tuple[BM25Okapi, list[Passage]]:
        if doc_ids not in self._index:
            passages = [p for d in doc_ids for p in self.corpus.passages(d)]
            self._index[doc_ids] = (BM25Okapi([tokenize(p.text) for p in passages]), passages)
        return self._index[doc_ids]

    def search(self, question: str, doc_ids: list[str], k: int = 8) -> list[Passage]:
        scope = tuple(sorted(doc_ids))
        key = hashlib.sha256(
            json.dumps([self.name, self.corpus.fingerprint, scope, question, k]).encode()
        ).hexdigest()
        path = self.cache_dir / f"{key}.json" if self.cache_dir else None
        if path and path.exists():
            return [Passage.model_validate(p) for p in json.loads(path.read_text())]
        bm25, passages = self._get_index(scope)
        scores = bm25.get_scores(tokenize(question))
        top = sorted(range(len(passages)), key=lambda i: -scores[i])[:k]
        out = [passages[i].model_copy(update={"score": float(scores[i])}) for i in top]
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps([p.model_dump() for p in out]))
        return out


class DenseRetriever:
    """Embedding retrieval with a small local model (optional extra ``local``). CPU by default
    (``GQB_DEVICE`` overrides); passage embeddings are cached per doc on disk, so the corpus is
    embedded once (~7k passages: a few minutes on a laptop CPU)."""

    name = "dense"
    QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

    def __init__(
        self,
        corpus: Corpus,
        model: str = "BAAI/bge-small-en-v1.5",
        cache_dir: str | Path = ".cache/embeddings",
    ):
        self.corpus = corpus
        self.model_name = model
        self.cache_dir = Path(cache_dir) / model.replace("/", "__")
        self._model = None
        self._docs: dict[str, tuple[Any, list[Passage]]] = {}
        self._lock = threading.Lock()

    @property
    def model(self):
        with self._lock:
            return self._load()

    def _load(self):
        if self._model is None:
            import os

            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(
                self.model_name, device=os.environ.get("GQB_DEVICE", "cpu")
            )
        return self._model

    def _doc(self, doc_id: str) -> tuple[Any, list[Passage]]:
        with self._lock:
            if doc_id not in self._docs:
                self._docs[doc_id] = self._embed(doc_id)
        return self._docs[doc_id]

    def _embed(self, doc_id: str) -> tuple[Any, list[Passage]]:
        import numpy as np

        passages = self.corpus.passages(doc_id)
        path = self.cache_dir / f"{doc_id}.npy"
        emb = np.load(path) if path.exists() else None
        if emb is None or len(emb) != len(passages):
            emb = (
                self._load()
                .encode([p.text for p in passages], normalize_embeddings=True, batch_size=32)
                .astype("float32")
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            np.save(path, emb)
        return emb, passages

    def scores(self, question: str, doc_ids: list[str]) -> list[tuple[float, Passage]]:
        q = self.model.encode([self.QUERY_PREFIX + question], normalize_embeddings=True)[0]
        out = []
        for d in sorted(doc_ids):
            emb, passages = self._doc(d)
            out += list(zip((emb @ q).tolist(), passages, strict=True)) if len(passages) else []
        return out

    def search(self, question: str, doc_ids: list[str], k: int = 8) -> list[Passage]:
        ranked = sorted(self.scores(question, doc_ids), key=lambda t: -t[0])[:k]
        return [p.model_copy(update={"score": float(s)}) for s, p in ranked]


class HybridRetriever:
    """Reciprocal-rank fusion of BM25 and dense (k=60). Exact part numbers and symbols come
    from BM25; paraphrased parameter names from the dense side."""

    name = "hybrid"

    def __init__(
        self, bm25: BM25Retriever, dense: DenseRetriever, depth: int = 50, k_rrf: int = 60
    ):
        self.bm25, self.dense, self.depth, self.k_rrf = bm25, dense, depth, k_rrf

    def search(self, question: str, doc_ids: list[str], k: int = 8) -> list[Passage]:
        fused: dict[str, float] = {}
        by_id: dict[str, Passage] = {}
        for ranked in (
            self.bm25.search(question, doc_ids, k=self.depth),
            self.dense.search(question, doc_ids, k=self.depth),
        ):
            for rank, p in enumerate(ranked):
                fused[p.id] = fused.get(p.id, 0.0) + 1.0 / (self.k_rrf + rank + 1)
                by_id[p.id] = p
        top = sorted(fused, key=lambda i: -fused[i])[:k]
        return [by_id[i].model_copy(update={"score": fused[i]}) for i in top]
