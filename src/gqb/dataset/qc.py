"""Item quality control: question paraphrase and independent cross-check.

- ``paraphrase``: rewrite template questions so they read naturally. Rejected (template kept)
  unless the part number and every number in the original question survive.
- ``crosscheck``: a second model answers each answerable item from its gold-evidence page
  text alone, without seeing the gold answer. Agreement is recorded in ``meta.crosscheck``.
  This is an automated filter, *not* a human audit: ``audited`` stays False until a person
  accepts the item with ``bench audit``.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from pydantic import BaseModel, Field

from gqb.corpus import Corpus
from gqb.llm import LLMClient, Message
from gqb.retrieve import BM25Retriever
from gqb.schemas import AnswerType, BenchItem
from gqb.textmatch import contains_number, normalize, numbers_in
from gqb.units import numeric_match


class Paraphrase(BaseModel):
    question: str


PARA_SYSTEM = (
    "Rewrite the engineering question so it reads like a natural question from a hardware "
    "engineer. Keep the exact part number, parameter meaning, min/typical/maximum qualifier, "
    "every number and unit, and every condition. Do not add information. One sentence."
)


FIELD_SYNONYMS = {
    "min": ("min", "lower", "lowest", "least", "smallest", "at least"),
    "typ": ("typ", "nominal", "usual"),
    "max": ("max", "upper", "highest", "largest", "most", "greatest", "up to", "peak"),
}


def paraphrase(llm: LLMClient, items: list[BenchItem], model: str, workers: int = 8) -> int:
    """In-place. Returns number of accepted paraphrases."""

    def one(item: BenchItem) -> bool:
        res = llm.complete(
            [Message(role="user", content=item.question)],
            model,
            system=PARA_SYSTEM,
            response_model=Paraphrase,
            max_tokens=1024,
            effort="low",
        )
        q = res.parsed.question.strip() if res.parsed else ""
        mpn_ok = all(tok in normalize(q) for tok in normalize(_mpn(item)).split())
        nums_ok = all(contains_number(q, n) for n in numbers_in(item.question))
        field = item.meta.get("field")
        field_ok = field is None or any(w in q.lower() for w in FIELD_SYNONYMS[field])
        if q and mpn_ok and nums_ok and field_ok:
            item.meta["template_question"] = item.question
            item.question = q
            return True
        item.meta["paraphrase_rejected"] = q
        return False

    with ThreadPoolExecutor(workers) as ex:
        return sum(ex.map(one, items))


def _mpn(item: BenchItem) -> str:
    return str(item.meta["mpn"])


class CheckAnswer(BaseModel):
    found: bool = Field(description="False if the pages do not contain the answer")
    value: float | None = None
    unit: str | None = None
    rationale: str = Field(description="One sentence")


CHECK_SYSTEM = (
    "Answer the question using only the datasheet page text provided. If the answer requires "
    "a calculation, do it. If the text does not contain the needed values, set found=false. "
    "Report value and unit separately (value as a plain number)."
)


class CheckChoice(BaseModel):
    found: bool = Field(description="False if the text lacks the values needed to decide")
    choice: str | None = Field(None, description="The part number you choose, exactly as asked")
    rationale: str


def enum_match(answer: str, gold: str, options: list[str]) -> bool:
    """Gold option named in the answer and no other option named (normalized substring)."""
    a = normalize(answer).replace(" ", "")
    hit = [o for o in options if normalize(o).replace(" ", "") in a]
    return hit == [gold] if options else normalize(gold) == normalize(answer)


def crosscheck(
    llm: LLMClient, corpus: Corpus, items: list[BenchItem], model: str, workers: int = 8
) -> dict[str, int]:
    """In-place: sets ``meta.crosscheck``.

    Numeric items: agree | disagree | not_found (checker sees only the gold pages).
    Unanswerable items: absent_confirmed | answerable_suspect (checker sees the top BM25
    passages for the question within the doc; any value found means the item is suspect).
    """
    retriever = BM25Retriever(corpus, cache_dir=None)

    def one(item: BenchItem) -> str:
        if item.answer_type == AnswerType.UNANSWERABLE:
            hits = retriever.search(item.question, item.doc_ids, k=6)
            ctx = "\n\n".join(f"[{h.doc_id} page {h.page}]\n{h.text}" for h in hits)
            res = llm.complete(
                [Message(role="user", content=f"{ctx}\n\nQuestion: {item.question}")],
                model,
                system=CHECK_SYSTEM,
                response_model=CheckAnswer,
                max_tokens=4096,
                effort="low",
            )
            ans = res.parsed
            item.meta["crosscheck_answer"] = ans.model_dump() if ans else None
            found = ans is not None and ans.found and ans.value is not None
            return "answerable_suspect" if found else "absent_confirmed"
        if item.answer_type == AnswerType.ENUM:
            pages = sorted({(e.doc_id, e.page) for e in item.gold_evidence})
            ctx = "\n\n".join(f"[{d} page {p}]\n{corpus.page(d, p)}" for d, p in pages)
            res = llm.complete(
                [Message(role="user", content=f"{ctx}\n\nQuestion: {item.question}")],
                model,
                system=CHECK_SYSTEM.replace(
                    "Report value and unit separately (value as a plain number).",
                    "Answer with one of the options named in the question."
                    if item.meta.get("kind") == "package"
                    else "Answer with one part number.",
                ),
                response_model=CheckChoice,
                max_tokens=4096,
                effort="low",
            )
            ans = res.parsed
            item.meta["crosscheck_answer"] = ans.model_dump() if ans else None
            if ans is None or not ans.found or not ans.choice:
                return "not_found"
            ok = enum_match(ans.choice, str(item.gold_answer), item.meta.get("options", []))
            return "agree" if ok else "disagree"
        if item.answer_type != AnswerType.NUMERIC:
            return "skipped"
        pages = sorted({(e.doc_id, e.page) for e in item.gold_evidence})
        ctx = "\n\n".join(f"[{d} page {p}]\n{corpus.page(d, p)}" for d, p in pages)
        res = llm.complete(
            [Message(role="user", content=f"{ctx}\n\nQuestion: {item.question}")],
            model,
            system=CHECK_SYSTEM,
            response_model=CheckAnswer,
            max_tokens=4096,
            effort="low",
        )
        ans = res.parsed
        item.meta["crosscheck_answer"] = ans.model_dump() if ans else None
        if ans is None or not ans.found or ans.value is None:
            return "not_found"
        ok = numeric_match(
            ans.value, ans.unit, float(item.gold_answer), item.unit, max(item.tolerance or 0, 0.01)
        )
        return "agree" if ok else "disagree"

    with ThreadPoolExecutor(workers) as ex:
        verdicts = list(ex.map(one, items))
    counts: dict[str, int] = {}
    for item, v in zip(items, verdicts, strict=True):
        item.meta["crosscheck"] = v
        counts[v] = counts.get(v, 0) + 1
    return counts
