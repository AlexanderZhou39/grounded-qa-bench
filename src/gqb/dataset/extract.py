"""Gold-fact extraction from spec pages.

1. ``select_pages``: rank pages by spec-section keywords and unit density; keep top-N per doc.
2. ``extract_page``: an LLM reads page text (+ page image) and returns numeric spec rows, each
   with a verbatim ``row_quote`` copied from the page text.
3. ``verify_fact``: deterministic checks. The quote must be found on the page and every
   extracted value must appear in it; the parameter name or symbol must appear on the page.
   Only facts that pass become gold candidates. The LLM proposes, the page text decides.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from enum import StrEnum

from pydantic import BaseModel, Field

from gqb.corpus import Corpus, render_page_png
from gqb.llm import LLMClient, Message
from gqb.textmatch import contains_number, contains_phrase, find_span, normalize

SECTION_KEYWORDS = {
    "abs_max": ["absolute maximum ratings", "absolute maximum rating"],
    "recommended": ["recommended operating conditions", "recommended operating condition"],
    "electrical": [
        "electrical characteristics",
        "dc characteristics",
        "dc electrical characteristics",
        "ac characteristics",
        "switching characteristics",
        "timing requirements",
        "specifications",
    ],
    "thermal": ["thermal information", "thermal characteristics", "thermal resistance"],
}

_UNIT_TOKEN = re.compile(
    r"(?<![a-z])(?:[pnumk]?(?:v|a|w|hz|s|ohm|f)|mhz|khz|ghz|degc|%)(?![a-z])", re.I
)


class Section(StrEnum):
    ABS_MAX = "abs_max"
    RECOMMENDED = "recommended"
    ELECTRICAL = "electrical"
    THERMAL = "thermal"
    OTHER = "other"


class PageScore(BaseModel):
    doc_id: str
    page: int
    score: float
    sections: list[str]


def score_page(text: str) -> tuple[float, list[str]]:
    norm = normalize(text)
    sections = [s for s, kws in SECTION_KEYWORDS.items() if any(k in norm for k in kws)]
    units = len(_UNIT_TOKEN.findall(norm))
    # table of contents pages mention every section but contain no units
    toc_penalty = 0.2 if norm.count("....") > 5 else 1.0
    return (len(sections) * 20 + min(units, 120)) * toc_penalty, sections


def select_pages(corpus: Corpus, doc_id: str, max_pages: int = 8) -> list[PageScore]:
    """Top pages by score, guaranteeing the best page of each section type found."""
    scored = []
    for pno, text in enumerate(corpus.pages(doc_id), 1):
        s, secs = score_page(text)
        if s >= 25:
            scored.append(PageScore(doc_id=doc_id, page=pno, score=s, sections=secs))
    scored.sort(key=lambda p: -p.score)
    chosen: dict[int, PageScore] = {}
    for sec in SECTION_KEYWORDS:
        best = next((p for p in scored if sec in p.sections), None)
        if best and len(chosen) < max_pages:
            chosen[best.page] = best
    for p in scored:
        if len(chosen) >= max_pages:
            break
        chosen.setdefault(p.page, p)
    return sorted(chosen.values(), key=lambda p: p.page)


# ---------------------------------------------------------------------------
# LLM extraction
# ---------------------------------------------------------------------------


class ExtractedRow(BaseModel):
    parameter: str = Field(description="Parameter name as printed, e.g. 'Input voltage range'")
    symbol: str | None = Field(None, description="Symbol as printed, e.g. 'VIN', or null")
    section: Section
    min: float | None = None
    typ: float | None = None
    max: float | None = None
    unit: str = Field(description="Unit as printed, e.g. 'mA', 'V', 'MHz', 'degC'")
    conditions: list[str] = Field(
        default_factory=list, description="Test conditions for this row, verbatim"
    )
    row_quote: str = Field(
        description="Verbatim span copied from the PAGE TEXT that contains this row's "
        "parameter and its numbers. Copy characters exactly; do not paraphrase."
    )


class PageExtraction(BaseModel):
    rows: list[ExtractedRow]


EXTRACT_SYSTEM = """You extract specification table rows from an electronic component datasheet
page. Rules:
- Only rows with at least one numeric min/typ/max value printed on THIS page.
- One output row per (parameter, condition set). If a parameter has several rows for different
  conditions or part variants, output each separately with its conditions.
- Put a part variant/package/temperature grade that a row applies to into conditions.
- Do not convert units or compute anything. Use the numbers exactly as printed.
- row_quote must be copied verbatim from the provided page text (not from the image) and
  must include the numbers you extracted. Keep it under 300 characters.
- Skip rows you cannot read unambiguously. Prefer precision over recall. At most 25 rows."""


def extract_page(
    llm: LLMClient, corpus: Corpus, doc_id: str, page: int, model: str, with_image: bool = True
) -> list[ExtractedRow]:
    text = corpus.page(doc_id, page)
    images = [render_page_png(doc_id, page)] if with_image else []
    msg = Message(
        role="user",
        content=f"Datasheet: {doc_id}, page {page}.\n\nPAGE TEXT:\n<<<\n{text}\n>>>",
        images=images,
    )
    res = llm.complete(
        [msg],
        model,
        system=EXTRACT_SYSTEM,
        response_model=PageExtraction,
        max_tokens=16000,
        effort="low",
    )
    return res.parsed.rows if res.parsed else []


# ---------------------------------------------------------------------------
# Verification -> Fact
# ---------------------------------------------------------------------------


class Fact(BaseModel):
    id: str
    doc_id: str
    page: int
    parameter: str
    symbol: str | None = None
    section: Section
    min: float | None = None
    typ: float | None = None
    max: float | None = None
    unit: str
    conditions: list[str] = Field(default_factory=list)
    quote: str
    quote_match: str  # textmatch stage that located the quote


def verify_fact(row: ExtractedRow, page_text: str) -> tuple[bool, str, str]:
    """Return (ok, reason, quote_match_stage)."""
    values = [v for v in (row.min, row.typ, row.max) if v is not None]
    if not values:
        return False, "no values", ""
    m = find_span(row.row_quote, page_text, fuzzy_threshold=88)
    if m is None:
        return False, "quote not on page", ""
    for v in values:
        if not contains_number(row.row_quote, v):
            return False, f"value {v} not in quote", m.stage
    named = contains_phrase(row.parameter, page_text) or (
        row.symbol is not None and contains_phrase(row.symbol, page_text)
    )
    if not named:
        return False, "parameter/symbol not on page", m.stage
    if row.min is not None and row.max is not None and row.min > row.max:
        return False, "min > max", m.stage
    return True, "ok", m.stage


class ExtractionReport(BaseModel):
    doc_id: str
    pages: list[int]
    proposed: int = 0
    kept: int = 0
    rejected: dict[str, int] = Field(default_factory=dict)


def extract_doc(
    llm: LLMClient,
    corpus: Corpus,
    doc_id: str,
    model: str,
    max_pages: int = 8,
    workers: int = 4,
) -> tuple[list[Fact], ExtractionReport]:
    pages = [p.page for p in select_pages(corpus, doc_id, max_pages)]
    report = ExtractionReport(doc_id=doc_id, pages=pages)
    with ThreadPoolExecutor(workers) as ex:
        results = list(ex.map(lambda p: (p, extract_page(llm, corpus, doc_id, p, model)), pages))
    facts: list[Fact] = []
    for page, rows in results:
        text = corpus.page(doc_id, page)
        for row in rows:
            report.proposed += 1
            ok, reason, stage = verify_fact(row, text)
            if not ok:
                key = reason.split(" ")[0] if reason.startswith("value") else reason
                report.rejected[key] = report.rejected.get(key, 0) + 1
                continue
            facts.append(
                Fact(
                    id=f"{doc_id}:{page}:{len(facts):03d}",
                    doc_id=doc_id,
                    page=page,
                    quote=row.row_quote,
                    quote_match=stage,
                    **row.model_dump(exclude={"row_quote"}),
                )
            )
    report.kept = len(facts)
    return facts, report


def dedupe(facts: Iterable[Fact]) -> list[Fact]:
    """Drop exact duplicate rows (same doc, parameter, values, conditions) across pages."""
    seen, out = set(), []
    for f in facts:
        key = (
            f.doc_id,
            normalize(f.parameter),
            f.min,
            f.typ,
            f.max,
            normalize(f.unit),
            tuple(normalize(c) for c in f.conditions),
        )
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out
