"""HOVER (Jiang et al., Findings of EMNLP 2020; CC BY-SA 4.0): many-hop claim verification over
Wikipedia. Used for the **verifier track** with oracle documents. The context is the full
2017 intro paragraphs of every supporting article. The "response" is the claim, and the label is
``NOT_SUPPORTED`` (refuted, or not enough information). This isolates multi-hop verification
from retrieval.

Needs ``wiki_wo_links.db`` (2.1 GB sqlite, the HOVER authors' Wikipedia dump) in
``data/external/hover/``. ``build`` downloads it on first use. Balanced per (hops, label) cell."""

from __future__ import annotations

import json
import random
import re
import sqlite3
from pathlib import Path

from gqb.external import EXTERNAL, fetch
from gqb.vtrack import VPair

CLAIMS = (
    "https://raw.githubusercontent.com/hover-nlp/hover/main/data/hover/hover_dev_release_v1.1.json"
)
WIKI = "https://nlp.cs.unc.edu/data/hover/wiki_wo_links.db"
QUESTION = "Is the following claim fully supported by the passages?"


def build(per_cell: int = 25, seed: int = 7, root: Path = EXTERNAL / "hover") -> list[VPair]:
    """``per_cell`` claims per (num_hops in 2/3/4, label) cell -> 6 * per_cell pairs."""
    claims = json.loads(fetch(CLAIMS, root / "hover_dev_release_v1.1.json").read_text())
    db = sqlite3.connect(f"file:{fetch(WIKI, root / 'wiki_wo_links.db')}?mode=ro", uri=True)
    rng = random.Random(seed)
    rng.shuffle(claims)
    cells: dict[tuple[int, str], list[VPair]] = {}
    for c in claims:
        key = (c["num_hops"], c["label"])
        if len(cells.setdefault(key, [])) >= per_cell:
            continue
        titles = list(dict.fromkeys(t for t, _ in c["supporting_facts"]))
        docs = [
            db.execute("select text from documents where id=?", (t,)).fetchone() for t in titles
        ]
        if any(d is None for d in docs):
            continue
        cells[key].append(
            VPair(
                id=f"hover:{c['uid']}",
                set="hover",
                task=f"{c['num_hops']}hop",
                question=QUESTION,
                context="\n\n".join(f"{t}\n{d[0]}" for t, d in zip(titles, docs, strict=True)),
                response=c["claim"],
                hallucinated=c["label"] != "SUPPORTED",
                meta={"titles": titles},
            )
        )
    pairs = [p for key in sorted(cells) for p in cells[key]]
    out = root / "pairs.jsonl"
    out.write_text("".join(p.model_dump_json() + "\n" for p in pairs))
    return pairs


INDEX = "wiki_fts.db"


def build_index(root: Path = EXTERNAL / "hover") -> Path:
    """FTS5 (BM25, porter stemming) over all 5.2M intro paragraphs; ~3 GB, ~2 min on CPU."""
    out = root / INDEX
    if out.exists():
        return out
    src = sqlite3.connect(f"file:{root / 'wiki_wo_links.db'}?mode=ro", uri=True)
    dst = sqlite3.connect(out)
    dst.execute("PRAGMA journal_mode=OFF")
    dst.execute("CREATE VIRTUAL TABLE docs USING fts5(title, text, tokenize='porter unicode61')")
    cur = src.execute("select id, text from documents")
    while rows := cur.fetchmany(50000):
        dst.executemany("insert into docs(title, text) values (?, ?)", rows)
    dst.commit()
    dst.execute("INSERT INTO docs(docs) VALUES('optimize')")
    dst.commit()
    return out


def search(db: sqlite3.Connection, query: str, k: int) -> list[tuple[str, str]]:
    """BM25 over title (weight 3) and text; the claim's words OR-ed together."""
    words = {w for w in re.findall(r"\w+", query.lower()) if len(w) > 2 and w not in _STOP}
    if not words:
        return []
    q = " OR ".join(f'"{w}"' for w in sorted(words))
    return db.execute(
        "select title, text from docs where docs match ? order by bm25(docs, 3.0, 1.0) limit ?",
        (q, k),
    ).fetchall()


_STOP = set(
    "the and was for that with who which this are from has have his her its were also not "
    "than one born film".split()
)


def build_open(k: int = 8, root: Path = EXTERNAL / "hover") -> list[VPair]:
    """Open-retrieval variant of the oracle pairs: the context is the claim's top-``k`` BM25
    articles instead of the gold ones. A SUPPORTED claim whose evidence was not retrieved stays
    labelled supported, so verifiers are scored on the end-to-end task (meta records recall)."""
    oracle = [VPair.model_validate_json(x) for x in (root / "pairs.jsonl").read_text().splitlines()]
    db = sqlite3.connect(build_index(root))
    pairs = []
    for p in oracle:
        hits = search(db, p.response, k)
        got = {t for t, _ in hits}
        gold = p.meta["titles"]
        pairs.append(
            p.model_copy(
                update={
                    "id": p.id + ":open",
                    "set": "hover_open",
                    "context": "\n\n".join(f"{t}\n{x}" for t, x in hits),
                    "meta": {**p.meta, "recall": sum(t in got for t in gold) / len(gold)},
                }
            )
        )
    (root / "pairs_open.jsonl").write_text("".join(p.model_dump_json() + "\n" for p in pairs))
    return pairs
