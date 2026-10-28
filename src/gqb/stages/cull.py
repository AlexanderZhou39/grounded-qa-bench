"""cull slot: reduce / structure evidence before generation.

- llm_filter     : LLM keeps the passages it judges relevant.
- evident_align  : EvidenT-style. LLM extracts verbatim excerpts; deterministic lexical
                   alignment drops any excerpt not found in its source passage.
- gavel_contract : GAVEL-style evidence contract. LLM states the atomic subclaims needed to
                   answer, each bound to a passage id + verbatim quote; mechanized scrutiny
                   keeps only bindings whose id exists and whose quote aligns.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from gqb.llm import Message, StageMeter
from gqb.schemas import EvidenceUnit, Passage, PipelineState
from gqb.stages.base import BaseStage
from gqb.textmatch import align_quote


def passages_to_units(passages: list[Passage]) -> list[EvidenceUnit]:
    return [
        EvidenceUnit(id=f"E{i}", passage_id=p.id, doc_id=p.doc_id, page=p.page, text=p.text)
        for i, p in enumerate(passages, 1)
    ]


def _format_passages(passages: list[Passage]) -> str:
    return "\n\n".join(
        f"[P{i}] ({p.doc_id}, page {p.page})\n{p.text}" for i, p in enumerate(passages, 1)
    )


def _passage_by_tag(passages: list[Passage], tag: str) -> Passage | None:
    tag = tag.strip("[] ").upper().removeprefix("P")
    return passages[int(tag) - 1] if tag.isdigit() and 1 <= int(tag) <= len(passages) else None


class Relevant(BaseModel):
    relevant: list[str] = Field(description="Ids of passages needed to answer, e.g. ['P2','P5']")


class LLMFilterStage(BaseStage):
    slot = "cull"
    name = "llm_filter"

    def apply(self, state: PipelineState, llm: StageMeter) -> PipelineState:
        res = self.complete(
            llm,
            [
                Message(
                    role="user",
                    content=f"{_format_passages(state.candidates)}\n\nQuestion: {state.question}",
                )
            ],
            self.model,
            system="Select the passages that contain information needed to answer the "
            "question for the exact part asked about. Return their ids; return an empty "
            "list if none are relevant.",
            response_model=Relevant,
            max_tokens=2048,
        )
        keep = []
        for tag in res.parsed.relevant if res.parsed else []:
            p = _passage_by_tag(state.candidates, tag)
            if p is not None and p not in keep:
                keep.append(p)
        state.evidence = passages_to_units(keep)
        return state


class Excerpt(BaseModel):
    passage: str = Field(description="Passage id, e.g. 'P3'")
    quote: str = Field(
        description="Verbatim excerpt (a table row with its parameter name, conditions and "
        "values, or a sentence). Copy characters exactly."
    )


class Excerpts(BaseModel):
    excerpts: list[Excerpt]


class HeadedExcerpt(Excerpt):
    header: str | None = Field(
        None,
        description="If the excerpt is a table row: that table's column-header row(s), copied "
        "verbatim from the same passage. Otherwise null.",
    )


class HeadedExcerpts(BaseModel):
    excerpts: list[HeadedExcerpt]


class EvidentAlignStage(BaseStage):
    slot = "cull"
    name = "evident_align"

    def apply(self, state: PipelineState, llm: StageMeter) -> PipelineState:
        headers = bool(self.params.get("table_headers", True))
        res = self.complete(
            llm,
            [
                Message(
                    role="user",
                    content=f"{_format_passages(state.candidates)}\n\nQuestion: {state.question}",
                )
            ],
            self.model,
            system="Extract the minimal set of verbatim excerpts from the passages that are "
            "needed to answer the question for the exact part asked about. Each excerpt must "
            "be self-contained (include the parameter name, conditions and the values). "
            "Return an empty list if nothing relevant exists. At most 8 excerpts.",
            response_model=HeadedExcerpts if headers else Excerpts,
            max_tokens=4096,
        )
        loose = self.params.get("align", "loose") == "loose"
        units: list[EvidenceUnit] = []
        dropped = 0
        for ex in res.parsed.excerpts if res.parsed else []:
            p = _passage_by_tag(state.candidates, ex.passage)
            m = align_quote(ex.quote, p.text, loose) if p else None
            if p is None or m is None:
                dropped += 1
                continue
            units.append(
                EvidenceUnit(
                    id=f"E{len(units) + 1}",
                    passage_id=p.id,
                    doc_id=p.doc_id,
                    page=p.page,
                    # a table row keeps its column headers, so its values stay interpretable
                    text=_headed(getattr(ex, "header", None), ex.quote, p.text, loose),
                    char_start=m.start,
                    char_end=m.end,
                )
            )
        state.evidence = units
        state.scratch["cull_dropped"] = dropped
        return state


class Binding(BaseModel):
    subclaim: str = Field(description="Atomic fact that must hold to answer the question")
    passage: str = Field(description="Passage id that establishes it, e.g. 'P1'")
    quote: str = Field(description="Verbatim span from that passage establishing the subclaim")


class Contract(BaseModel):
    bindings: list[Binding]


class HeadedBinding(Binding):
    header: str | None = Field(
        None,
        description="If the quote is a table row: that table's column-header row(s), copied "
        "verbatim from the same passage. Otherwise null.",
    )


class HeadedContract(BaseModel):
    bindings: list[HeadedBinding]


def _headed(head: str | None, quote: str, text: str, loose: bool) -> str:
    """Prefix a table-row quote with its column headers when they align to the source."""
    if not head or head in quote or not align_quote(head, text, loose):
        return quote
    return f"{head}\n{quote}"


class GavelContractStage(BaseStage):
    slot = "cull"
    name = "gavel_contract"

    def apply(self, state: PipelineState, llm: StageMeter) -> PipelineState:
        headers = bool(self.params.get("table_headers", True))
        res = self.complete(
            llm,
            [
                Message(
                    role="user",
                    content=f"{_format_passages(state.candidates)}\n\nQuestion: {state.question}",
                )
            ],
            self.model,
            system="Draft an evidence contract for answering the question. List the atomic "
            "subclaims that must be established (e.g. which part, which table row, which "
            "condition column, which value), and bind each to exactly one passage id and a "
            "verbatim quote from that passage. Only bind what the passages actually state. "
            "Return an empty list if the passages cannot establish the answer.",
            response_model=HeadedContract if headers else Contract,
            max_tokens=4096,
        )
        units: list[EvidenceUnit] = []
        ledger = []
        for b in res.parsed.bindings if res.parsed else []:
            p = _passage_by_tag(state.candidates, b.passage)
            loose = self.params.get("align", "loose") == "loose"
            m = align_quote(b.quote, p.text, loose) if p else None
            ok = p is not None and m is not None
            ledger.append({"subclaim": b.subclaim, "passage": b.passage, "valid": ok})
            if not ok:
                continue
            units.append(
                EvidenceUnit(
                    id=f"E{len(units) + 1}",
                    passage_id=p.id,
                    doc_id=p.doc_id,
                    page=p.page,
                    text=_headed(getattr(b, "header", None), b.quote, p.text, loose)
                    + f"\n(establishes: {b.subclaim})",
                    char_start=m.start,
                    char_end=m.end,
                )
            )
        state.evidence = units
        state.scratch["contract"] = ledger
        return state
