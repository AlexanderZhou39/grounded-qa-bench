"""TAT-QA (Zhu et al., ACL 2021; CC BY 4.0): numeric QA over a financial-report table plus
its surrounding paragraphs. Close to the BI use case: hybrid table + text, derived values.

Mapping:
- one context (table rendered as pipe rows + paragraphs) -> one single-page doc
- ``arithmetic`` questions -> tier 3 (derived); ``span`` questions with a numeric answer -> tier 1
- multi-span and count questions are skipped (not single numeric answers)
- gold stays in the dataset's ``scale`` (e.g. millions); the scorer also accepts the value
  multiplied out (``scale_factors``), since the question rarely names the scale.
"""

from __future__ import annotations

import json
import random
import re
from pathlib import Path

from gqb.external import EXTERNAL, fetch, write_doc
from gqb.schemas import AnswerType, BenchItem, GoldEvidence, ItemSource, Tier

URL = "https://raw.githubusercontent.com/NExTplusplus/TAT-QA/master/dataset_raw/tatqa_dataset_dev.json"
SCALE = {"": [], "thousand": [1e3], "million": [1e6], "billion": [1e9], "percent": [0.01]}


def _num(x: object) -> float | None:
    if isinstance(x, int | float):
        return float(x)
    if isinstance(x, list) and len(x) == 1:
        x = x[0]
    if not isinstance(x, str):
        return None
    s = x.strip().replace(",", "").replace("$", "").replace("%", "").strip()
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    try:
        v = float(s)
    except ValueError:
        return None
    return -v if neg else v


def render(ctx: dict) -> str:
    rows = [" | ".join(c.strip() for c in row) for row in ctx["table"]["table"]]
    paras = [p["text"] for p in sorted(ctx["paragraphs"], key=lambda p: p["order"])]
    return "TABLE\n" + "\n".join(rows) + "\n\nTEXT\n" + "\n\n".join(paras) + "\n"


def build(n: int = 150, seed: int = 7, root: Path = EXTERNAL / "tatqa") -> list[BenchItem]:
    raw = json.loads(fetch(URL, root / "raw" / "tatqa_dataset_dev.json").read_text())
    pool: dict[str, list[BenchItem]] = {"arithmetic": [], "span": []}
    for ctx in raw:
        doc_id = "tatqa_" + ctx["table"]["uid"][:8]
        text = render(ctx)
        for q in ctx["questions"]:
            kind = q["answer_type"]
            gold = _num(q["answer"])
            if kind not in pool or gold is None or q["scale"] not in SCALE:
                continue
            if kind == "span" and not re.search(r"\d", str(q["answer"])):
                continue
            pool[kind].append(
                BenchItem(
                    id=f"{doc_id}:{q['uid'][:8]}",
                    tier=Tier.DERIVED if kind == "arithmetic" else Tier.DIRECT,
                    question=q["question"],
                    answer_type=AnswerType.NUMERIC,
                    gold_answer=round(gold, 4),
                    unit=None,
                    tolerance=0.01,
                    doc_ids=[doc_id],
                    gold_evidence=[GoldEvidence(doc_id=doc_id, page=1)],
                    source=ItemSource.EXTERNAL,
                    meta={
                        "set": "tatqa",
                        "unit_free": True,
                        "scale": q["scale"],
                        "scale_factors": SCALE[q["scale"]],
                        "answer_from": q["answer_from"],
                        "derivation": q["derivation"],
                        "_text": text,
                    },
                )
            )
    rng = random.Random(seed)
    items = []
    for kind in ("arithmetic", "span"):
        rng.shuffle(pool[kind])
        items += pool[kind][: n // 2]
    for it in items:
        write_doc(root / "corpus", it.doc_ids[0], [it.meta.pop("_text")])
    for kind in pool:
        for it in pool[kind]:
            it.meta.pop("_text", None)
    out = root / "items.jsonl"
    out.write_text("".join(i.model_dump_json() + "\n" for i in items))
    return items
