"""Retrievers. Results are cached per (retriever, scope, question) so that every combo in a
comparison sees identical candidates."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

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
        key = hashlib.sha256(json.dumps([self.name, scope, question, k]).encode()).hexdigest()
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
