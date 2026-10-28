"""Shared helpers for stage implementations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from gqb.corpus import Corpus
from gqb.retrieve import BM25Retriever
from gqb.schemas import Answer, Claim, EvidenceUnit, PipelineState
from gqb.textmatch import contains_number, numbers_in

DEFAULT_MODEL = "claude-haiku-5-5"


@dataclass
class StageContext:
    corpus: Corpus
    retriever: BM25Retriever
    top_k: int = 8
    scope: str = "item"  # item: retrieve within item.doc_ids | corpus: all docs
    default_model: str = DEFAULT_MODEL
    effort: str | None = "low"
    extra: dict[str, Any] = field(default_factory=dict)


def format_evidence(units: list[EvidenceUnit]) -> str:
    return "\n\n".join(f"[{u.id}] ({u.doc_id}, page {u.page})\n{u.text}" for u in units)


def unit_map(state: PipelineState) -> dict[str, EvidenceUnit]:
    return {u.id: u for u in state.evidence}


# ---------------------------------------------------------------------------
# Generator output (shared by every generate impl and by repair=regenerate)
# ---------------------------------------------------------------------------


class ClaimOut(BaseModel):
    text: str = Field(description="One atomic factual statement supporting the answer")
    citations: list[str] = Field(default_factory=list, description="Evidence ids, e.g. ['E2']")
    quote: str | None = Field(
        None, description="Verbatim span copied from the cited evidence that supports the claim"
    )
    derived: bool = Field(
        False, description="True if the claim is computed from evidence values, not stated"
    )


class GenOut(BaseModel):
    abstain: bool = Field(description="True if the evidence does not contain the answer")
    answer_text: str = Field(description="Short answer sentence (or why you abstain)")
    value: float | None = Field(None, description="Numeric answer as a plain number, if numeric")
    value_text: str | None = Field(None, description="Non-numeric answer, if not numeric")
    unit: str | None = Field(None, description="Unit of value, as printed, e.g. 'mA'")
    claims: list[ClaimOut] = Field(default_factory=list)


ANSWER_RULES = """Answer the question using ONLY the evidence below.
- If the evidence does not state the needed values for the exact part and conditions asked,
  set abstain=true. Do not use outside knowledge.
- Watch for evidence from similar but different parts, and for rows with different
  conditions, variants or min/typ/max columns. Use the one the question asks for.
- value is a plain number in the unit you give (e.g. value=3.3, unit="V").
- If you must compute the answer from evidence values, do the arithmetic carefully."""

CITE_RULES = """
- Support the answer with claims. Each claim cites evidence ids and includes a verbatim quote
  copied from the cited evidence. Mark computed claims derived=true and state the inputs."""


def to_answer(out: GenOut, evidence_ids: set[str]) -> Answer:
    claims = []
    for i, c in enumerate(out.claims, 1):
        cites = [x.strip("[] ") for x in c.citations]
        claims.append(
            Claim(
                id=f"C{i}",
                text=c.text,
                citations=[x for x in cites if x],
                quote=c.quote,
                derived=c.derived,
            )
        )
    value: str | float | None = out.value if out.value is not None else out.value_text
    return Answer(
        text=out.answer_text, value=None if out.abstain else value, unit=out.unit, claims=claims
    )


def answer_bearing(answer: Answer) -> list[Claim]:
    """Claims that state the answer value. Falls back to all claims."""
    if isinstance(answer.value, float | int):
        hits = [c for c in answer.claims if contains_number(c.text, float(answer.value))]
        if hits:
            return hits
    return list(answer.claims)


def claim_numbers(claim: Claim) -> list[float]:
    return numbers_in(claim.text)
