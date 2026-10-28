"""RAGTruth (Niu et al., ACL 2024; MIT): LLM responses to QA / summary / data-to-text prompts
with human span-level hallucination labels. Used for the **verifier track**: given the source
context and a response, does a verifier flag the responses that contain a hallucination?

Mapping: one test response -> one ``VPair`` (context, response, label = any labeled span).
Sampled balanced per task type, so precision/recall are not dominated by Data2txt (where most
responses are hallucinated)."""

from __future__ import annotations

import json
import random
from pathlib import Path

from gqb.external import EXTERNAL, fetch
from gqb.vtrack import VPair

BASE = "https://raw.githubusercontent.com/ParticleMedia/RAGTruth/main/dataset/"
TASK_PROMPT = {
    "Summary": "Summarize the following news article.",
    "Data2txt": "Write an objective overview of this business based on the structured data.",
}


def _context(src: dict) -> tuple[str, str]:
    info = src["source_info"]
    if src["task_type"] == "QA":
        return info["question"], info["passages"]
    if isinstance(info, dict | list):
        info = json.dumps(info, ensure_ascii=False, indent=1)
    return TASK_PROMPT[src["task_type"]], info


def build(per_cell: int = 25, seed: int = 7, root: Path = EXTERNAL / "ragtruth") -> list[VPair]:
    """``per_cell`` responses for each (task, hallucinated?) cell -> 6 * per_cell pairs."""
    resp = [
        json.loads(x)
        for x in fetch(BASE + "response.jsonl", root / "raw" / "response.jsonl")
        .read_text()
        .splitlines()
        if x
    ]
    src = {
        (s := json.loads(x))["source_id"]: s
        for x in fetch(BASE + "source_info.jsonl", root / "raw" / "source_info.jsonl")
        .read_text()
        .splitlines()
        if x
    }
    cells: dict[tuple[str, bool], list[VPair]] = {}
    for r in resp:
        if r["split"] != "test":
            continue
        s = src[r["source_id"]]
        question, context = _context(s)
        label = bool(r["labels"])
        cells.setdefault((s["task_type"], label), []).append(
            VPair(
                id=f"ragtruth:{r['id']}",
                set="ragtruth",
                task=s["task_type"],
                question=question,
                context=context,
                response=r["response"],
                hallucinated=label,
                meta={"model": r["model"], "spans": [x["text"] for x in r["labels"]]},
            )
        )
    rng = random.Random(seed)
    pairs = []
    for key in sorted(cells):
        rng.shuffle(cells[key])
        pairs += cells[key][:per_cell]
    out = root / "pairs.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(p.model_dump_json() + "\n" for p in pairs))
    return pairs
