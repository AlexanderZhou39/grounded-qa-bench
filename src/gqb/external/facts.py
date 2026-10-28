"""FACTS Grounding public split (Jacovi et al., 2025; CC BY 4.0): long-form answers to a user
request over a context document; the metric is the share of responses a judge rates fully
grounded ("Accurate") in the document.

Compact adaptation (a separate track; long-form answers do not fit ``BenchItem`` scoring):
- generators: a single long-form call per model, optionally followed by a verify -> revise
  repair (``claim_decompose`` finds unsupported atomic claims, then one call rewrites the
  response without them). This is the rag-facts-check idea applied to long-form output.
- judge: FACTS's own ``response_level`` prompt (downloaded, not copied into the repo) on a fixed
  judge model that is never a generator in the same comparison.
Differences from the official leaderboard: one judge instead of three, no eligibility
(helpfulness) filter, a small sample. Numbers are comparable across our combos, not to the
leaderboard."""

from __future__ import annotations

import csv
import random
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pydantic import BaseModel

from gqb.corpus import Corpus, chunk_page
from gqb.external import EXTERNAL, fetch
from gqb.llm import CachedClient, LLMClient, Message, StageMeter
from gqb.schemas import Answer, EvidenceUnit, PipelineState, Verdict
from gqb.stages import REGISTRY, StageContext

BASE = "https://huggingface.co/datasets/google/FACTS-grounding-public/resolve/main/"
ROOT = EXTERNAL / "facts"


class FactsExample(BaseModel):
    id: str
    system_instruction: str
    user_request: str
    context_document: str


class FactsResult(BaseModel):
    example_id: str
    generator: str
    response: str
    accurate: bool | None
    gen_cost_usd: float
    judge_cost_usd: float
    unsupported_claims: int | None = None


def _csv(path: Path) -> list[dict]:
    csv.field_size_limit(10**9)
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def build(n: int = 40, max_chars: int = 30000, seed: int = 7) -> list[FactsExample]:
    rows = _csv(fetch(BASE + "examples.csv", ROOT / "raw" / "examples.csv"))
    fetch(BASE + "evaluation_prompts.csv", ROOT / "raw" / "evaluation_prompts.csv")
    ex = [
        FactsExample(id=f"facts:{i}", **{k: r[k] for k in FactsExample.model_fields if k != "id"})
        for i, r in enumerate(rows)
        if len(r["context_document"]) <= max_chars
    ]
    random.Random(seed).shuffle(ex)
    ex = ex[:n]
    (ROOT / "examples.jsonl").write_text("".join(e.model_dump_json() + "\n" for e in ex))
    return ex


def load() -> list[FactsExample]:
    return [
        FactsExample.model_validate_json(x)
        for x in (ROOT / "examples.jsonl").read_text().splitlines()
        if x
    ]


def judge_prompt(e: FactsExample, response: str) -> str:
    rows = _csv(ROOT / "raw" / "evaluation_prompts.csv")
    tmpl = next(r["evaluation_prompt"] for r in rows if r["evaluation_method"] == "response_level")
    tmpl = tmpl.replace("\\n", "\n")
    return (
        tmpl.replace("{{user_request}}", e.user_request)
        .replace("{{context_document}}", e.context_document)
        .replace("{{response}}", response)
    ) + "\n\nEnd with a final line that is exactly 'Accurate' or 'Inaccurate'."


def _verdict(text: str) -> bool | None:
    last = re.findall(r"\b(Inaccurate|Accurate)\b", text)
    return None if not last else last[-1] == "Accurate"


def generate(e: FactsExample, llm: LLMClient, model: str) -> tuple[str, float]:
    r = llm.complete(
        [Message(role="user", content=f"{e.user_request}\n\n{e.context_document}")],
        model,
        system=e.system_instruction,
        max_tokens=2048,
        effort="low",
    )
    return r.text, r.cost_usd


REVISE = """Revise the response so that every statement is supported by the context document.
Remove or correct the statements listed as unsupported; keep everything else. Output only the
revised response."""


def repair(e: FactsExample, response: str, llm: LLMClient, model: str) -> tuple[str, float, int]:
    units = [
        EvidenceUnit(id=f"E{i}", passage_id=f"{e.id}:{i}", doc_id=e.id, page=1, text=t)
        for i, t in enumerate(chunk_page(e.context_document), 1)
    ]
    st = PipelineState(
        item_id=e.id,
        question=e.user_request,
        doc_ids=[e.id],
        evidence=units,
        answer=Answer(text=response, value=response),
    )
    ctx = StageContext(corpus=Corpus(), retriever=None, default_model=model)  # type: ignore[arg-type]
    st = REGISTRY["verify"]["claim_decompose"](ctx, model).run(st, llm)
    cost = sum(t.cost_usd for t in st.trace)
    claims = {c.id: c.text for c in (st.answer.claims if st.answer else [])}
    bad = [
        claims.get(v.claim_id, "")
        for v in st.verdicts
        if v.verdict in (Verdict.UNSUPPORTED, Verdict.CONTRADICTED)
    ]
    if not bad:
        return response, cost, 0
    meter = StageMeter(llm, "repair", "revise", model)
    with meter:
        r = meter.complete(
            [
                Message(
                    role="user",
                    content=f"Context document:\n{e.context_document}\n\nRequest:\n"
                    f"{e.user_request}\n\nResponse:\n{response}\n\nUnsupported statements:\n"
                    + "\n".join(f"- {b}" for b in bad),
                )
            ],
            model,
            system=REVISE,
            max_tokens=2048,
            effort="low",
        )
    return r.text, cost + meter.trace.cost_usd, len(bad)


def run(
    generator: str,
    judge_model: str,
    llm: LLMClient,
    examples: list[FactsExample],
    workers: int = 6,
    cache_dir: str = ".cache/llm",
) -> list[FactsResult]:
    """``generator`` = ``{model}`` or ``{model}+repair``."""
    model, _, mode = generator.partition("+")
    if model == judge_model:
        raise ValueError("judge model must not be a generator in the same comparison")
    client = CachedClient(llm, cache_dir)

    def one(e: FactsExample) -> FactsResult:
        resp, cost = generate(e, client, model)
        n_bad = None
        if mode == "repair":
            resp, rc, n_bad = repair(e, resp, client, model)
            cost += rc
        j = client.complete(
            [Message(role="user", content=judge_prompt(e, resp))],
            judge_model,
            max_tokens=2048,
            effort="low",
        )
        return FactsResult(
            example_id=e.id,
            generator=generator,
            response=resp,
            accurate=_verdict(j.text),
            gen_cost_usd=cost,
            judge_cost_usd=j.cost_usd,
            unsupported_claims=n_bad,
        )

    with ThreadPoolExecutor(workers) as ex:
        return list(ex.map(one, examples))
