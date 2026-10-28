"""Adapters for external benchmarks (Phase 3). Each adapter downloads its source data into
``data/external/{set}/raw/`` and writes ``corpus/`` + ``items.jsonl`` (or ``pairs.jsonl`` for
the verifier track) next to it. Nothing under ``data/external`` is committed: licenses vary,
and the download script is the reproducible artifact."""

from __future__ import annotations

import urllib.request
from pathlib import Path

EXTERNAL = Path("data/external")


def fetch(url: str, dest: Path) -> Path:
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(url, dest)  # noqa: S310 (fixed https URLs)
    return dest


def write_doc(corpus: Path, doc_id: str, pages: list[str]) -> None:
    out = corpus / doc_id
    out.mkdir(parents=True, exist_ok=True)
    for i, text in enumerate(pages, 1):
        (out / f"{i:04d}.txt").write_text(text)
