"""Core data contracts. Keep these stable; every stage, scorer and dataset tool depends on them."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import IntEnum, StrEnum
from typing import Any

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Benchmark items
# ---------------------------------------------------------------------------


class Tier(IntEnum):
    DIRECT = 1  # value stated once, direct lookup
    CONDITIONAL = 2  # value depends on test condition / variant / footnote
    DERIVED = 3  # small computation over stated values
    CROSS_DOC = 4  # comparison across two same-family docs (enum answer = MPN)
    UNANSWERABLE = 5  # answer not in corpus; correct behavior is abstain
    DISTRACTOR = 6  # near-duplicate part / revision present in candidates


class AnswerType(StrEnum):
    NUMERIC = "numeric"
    ENUM = "enum"
    TEXT = "text"
    UNANSWERABLE = "unanswerable"


class ItemSource(StrEnum):
    DATASHEET = "datasheet"
    EXTERNAL = "external"  # FACTS, TAT-QA, HOVER, ... adapters


class GoldEvidence(BaseModel):
    doc_id: str
    page: int = Field(ge=1, description="1-indexed page in the original PDF")
    quote: str | None = Field(None, description="Verbatim-ish span on that page, if located")


class BenchItem(BaseModel):
    id: str
    tier: Tier
    question: str
    answer_type: AnswerType
    gold_answer: str | float | None = None  # None iff answer_type == unanswerable
    unit: str | None = None
    tolerance: float | None = Field(None, description="Relative tolerance for numeric match")
    conditions: list[str] = Field(default_factory=list)
    doc_ids: list[str] = Field(default_factory=list, description="Docs in scope for retrieval")
    gold_evidence: list[GoldEvidence] = Field(default_factory=list)
    source: ItemSource = ItemSource.DATASHEET
    audited: bool = False
    meta: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Pipeline state
# ---------------------------------------------------------------------------


class Passage(BaseModel):
    """A retrieval unit. id format: ``{doc_id}:{page}:{chunk}``."""

    id: str
    doc_id: str
    page: int
    text: str
    score: float | None = None


class EvidenceUnit(BaseModel):
    """A culled/extracted piece of evidence that claims may cite."""

    id: str
    passage_id: str
    doc_id: str
    page: int
    text: str
    char_start: int | None = None
    char_end: int | None = None


class Claim(BaseModel):
    id: str
    text: str
    citations: list[str] = Field(default_factory=list, description="EvidenceUnit or Passage ids")
    quote: str | None = Field(None, description="Verbatim span from the cited evidence")
    derived: bool = Field(False, description="Computed rather than stated verbatim")


class Answer(BaseModel):
    text: str
    value: str | float | None = Field(None, description="Normalized answer used for scoring")
    unit: str | None = None
    claims: list[Claim] = Field(default_factory=list)


class Verdict(StrEnum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    CONTRADICTED = "contradicted"
    UNVERIFIABLE = "unverifiable"


class ClaimVerdict(BaseModel):
    claim_id: str
    verdict: Verdict
    confidence: float | None = Field(None, ge=0.0, le=1.0)
    evidence_ids: list[str] = Field(default_factory=list)
    verifier: str = Field(..., description="Stage impl that produced this verdict")
    rationale: str | None = None


class StageTrace(BaseModel):
    stage: str  # retrieve | cull | generate | verify | repair
    impl: str
    model: str | None = None
    calls: int = 0
    in_tok: int = 0
    out_tok: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    error: str | None = None


class PipelineState(BaseModel):
    item_id: str
    question: str
    doc_ids: list[str] = Field(default_factory=list)
    candidates: list[Passage] = Field(default_factory=list)
    evidence: list[EvidenceUnit] = Field(default_factory=list)
    answer: Answer | None = None
    verdicts: list[ClaimVerdict] = Field(default_factory=list)
    abstained: bool = False
    trace: list[StageTrace] = Field(default_factory=list)
    scratch: dict[str, Any] = Field(
        default_factory=dict, description="Free-form inter-stage data (e.g. GAVEL subclaims)"
    )

    @classmethod
    def from_item(cls, item: BenchItem) -> PipelineState:
        return cls(item_id=item.id, question=item.question, doc_ids=list(item.doc_ids))


class RunRecord(BaseModel):
    """One (item, combo, repeat) execution, appended as a JSONL line under ``runs/``."""

    combo_id: str
    item_id: str
    repeat: int = 0
    state: PipelineState
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def total_cost_usd(self) -> float:
        return sum(t.cost_usd for t in self.state.trace)

    @property
    def total_latency_ms(self) -> float:
        return sum(t.latency_ms for t in self.state.trace)
