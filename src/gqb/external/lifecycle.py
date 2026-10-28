"""Lifecycle set (Vera-Life-style supersession), built from committed datasheet items.

The corpus has one revision per part, so revisions are synthesized under control. For each tier-1
numeric item, a copy of the gold page is written as an older revision of the same datasheet.
In that copy, the answer value is replaced by a different value. Every page carries a revision
header, the way real datasheets carry "Rev. D, 08/2023" in their footer. Three kinds:

- ``superseded`` (tier 6): current doc + old revision. The old header says it is superseded.
  Gold = current value. The stale value is ``distractor_value``, so ``distractor_rate`` is the
  stale rate.
- ``superseded_dates`` (tier 6): the same, but the headers carry only revision letters and
  dates. Which revision is current has to be inferred.
- ``stale_only`` (tier 5): only the old, labelled-superseded revision is in scope. Correct is
  to abstain, or to give the value with an explicit staleness warning (Vera-Life ``warn_stale``).

``revisions.json`` next to the corpus records {doc_id: family, revision, date, superseded_by}.
This is the version metadata that a document store would hold. The ``lifecycle`` retrieve
param and the ``warn_stale`` repair use it; LLM-only combos see only the page headers."""

from __future__ import annotations

import hashlib
import json
import random
import re
from pathlib import Path

from gqb.corpus import CHUNK_CHARS, Corpus, chunk_page, read_jsonl
from gqb.external import EXTERNAL, write_doc
from gqb.schemas import AnswerType, BenchItem, Tier

CUR = ("D", "August 2023")
OLD = ("B", "April 2019")
_NUM = re.compile(r"(?<![\w.])[-+]?\d+(?:\.\d+)?(?![\w.])")
KINDS = ("superseded", "superseded_dates", "stale_only")


def _hid(*parts: str) -> str:
    return hashlib.sha1(":".join(parts).encode()).hexdigest()[:6]


def header(rev: tuple[str, str], superseded_by: tuple[str, str] | None = None) -> str:
    h = f"[Datasheet revision {rev[0]}, {rev[1]}]"
    if superseded_by:
        h += (
            f" [SUPERSEDED: this revision is obsolete and replaced by revision "
            f"{superseded_by[0]} ({superseded_by[1]})]"
        )
    return h + "\n"


def _perturb(token: str, rng: random.Random) -> str | None:
    v = float(token)
    dec = len(token.split(".")[1]) if "." in token else 0
    for f in rng.sample([0.5, 0.8, 1.25, 1.5, 2.0], 5):
        s = f"{v * f:.{dec}f}"
        if float(s) != v and s not in (token, "0", "-0"):
            return s
    return None


def _locate(page: str, quote: str, gold: float) -> tuple[int, int] | None:
    """Offsets of the gold number inside the quoted row on the page."""
    words = quote.split()[:3]
    start = page.find(" ".join(words)) if words else -1
    if start < 0 and words:
        start = page.find(words[0])
    if start < 0:
        return None
    for m in _NUM.finditer(page, start, start + max(300, len(quote) * 2)):
        if abs(float(m.group()) - abs(gold)) < 1e-9 or float(m.group()) == gold:
            return m.start(), m.end()
    return None


def _chunks(text: str, width: int) -> list[tuple[int, str]]:
    """Retrieval-sized chunks with their offsets, sized so a header still fits in one chunk.
    Each chunk is written as its own page, so every retrievable unit carries the header."""
    out, pos = [], 0
    for c in chunk_page(text, width):
        start = text.find(c, pos)
        out.append((start, c))
        pos = start + len(c)
    return out


def build(
    split: str,
    corpus_dir: Path = Path("data/corpus"),
    root: Path = EXTERNAL / "lifecycle",
    seed: int = 11,
) -> list[BenchItem]:
    src = Corpus(corpus_dir)
    items = [BenchItem.model_validate(r) for r in read_jsonl(Path(f"data/items/{split}.jsonl"))]
    rng = random.Random(f"{seed}:{split}")
    corpus = root / "corpus"
    revs = (
        json.loads((corpus / "revisions.json").read_text())
        if (corpus / "revisions.json").exists()
        else {}
    )
    out: list[BenchItem] = []
    current: dict[str, tuple[str, dict[tuple[int, int], int]]] = {}
    for it in items:
        if it.tier != Tier.DIRECT or it.answer_type != AnswerType.NUMERIC or not it.gold_evidence:
            continue
        ev = it.gold_evidence[0]
        page = src.pages(ev.doc_id)[ev.page - 1]
        span = _locate(page, ev.quote or "", float(it.gold_answer))
        if span is None:
            continue
        new = _perturb(page[span[0] : span[1]], rng)
        if new is None:
            continue
        width = CHUNK_CHARS - 200
        if ev.doc_id not in current:  # one current copy per source doc, every chunk a page
            cid = f"{ev.doc_id}~{_hid(ev.doc_id, 'cur')}"
            pages, where = [], {}
            for pno, text in enumerate(src.pages(ev.doc_id), 1):
                for start, c in _chunks(text, width):
                    pages.append(header(CUR) + c)
                    where[(pno, start)] = len(pages)
            write_doc(corpus, cid, pages)
            current[ev.doc_id] = (cid, where)
            revs[cid] = {
                "family": ev.doc_id,
                "revision": CUR[0],
                "date": "2023-08",
                "superseded_by": None,
            }
        cid, where = current[ev.doc_id]
        chunks = _chunks(page, width)
        start, chunk = next((st, c) for st, c in chunks if st <= span[0] < st + len(c))
        gold_page = where[(ev.page, start)]
        a, b = span[0] - start, span[1] - start
        old_text = chunk[:a] + new + chunk[b:]
        stale = float(new)
        if float(it.gold_answer) < 0 < stale:  # page states the magnitude, gold is signed
            stale = -stale
        for kind in KINDS:
            labelled = kind != "superseded_dates"
            oid = f"{ev.doc_id}~{_hid(it.id, kind)}"
            write_doc(corpus, oid, [header(OLD, CUR if labelled else None) + old_text])
            revs[oid] = {
                "family": ev.doc_id,
                "revision": OLD[0],
                "date": "2019-04",
                "superseded_by": cid,
            }
            base = it.model_dump()
            base.update(
                id=f"{it.id}:life:{kind}",
                doc_ids=[oid] if kind == "stale_only" else [cid, oid],
                gold_evidence=[{"doc_id": cid, "page": gold_page, "quote": ev.quote}]
                if kind != "stale_only"
                else [{"doc_id": oid, "page": 1, "quote": None}],
            )
            meta = dict(it.meta, kind=kind, source_item=it.id, stale_value=stale)
            if kind == "stale_only":
                base.update(
                    tier=Tier.UNANSWERABLE, answer_type=AnswerType.UNANSWERABLE, gold_answer=None
                )
            else:
                base.update(tier=Tier.DISTRACTOR)
                meta["distractor_value"] = stale
            base["meta"] = meta
            out.append(BenchItem.model_validate(base))
    (corpus / "revisions.json").write_text(json.dumps(revs, indent=0, sort_keys=True))
    path = root / f"items_{split}.jsonl"
    path.write_text("".join(i.model_dump_json() + "\n" for i in out))
    return out
