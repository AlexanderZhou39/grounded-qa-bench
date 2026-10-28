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
