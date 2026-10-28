"""Package facts: which packages each part is offered in, with a verbatim quote.

A second, text-only extraction pass (one call per doc, over the ordering/package pages). It feeds
two item kinds:

- ``package`` (tier 1, enum): "Which of these packages is the X offered in: A, B or C?" The gold
  is one of X's packages; the distractors are packages of other parts that X is not offered in.
- ``near_miss_pkg`` (tier 5, unanswerable): asks for X's junction-to-ambient thermal resistance
  in a package X is not offered in. The doc does state θJA for X's real package, so a model that
  ignores the package answers with that number and is wrong.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor

from pydantic import BaseModel, Field

from gqb.corpus import Corpus
from gqb.llm import LLMClient, Message
from gqb.textmatch import find_span, find_tokens, normalize

_PAGES = re.compile(
    r"(?i)ordering information|package information|package option|device information|"
    r"package type|available packages?|orderable"
)


class PackageRow(BaseModel):
    name: str = Field(description="Package name as printed, e.g. 'VQFN', 'SOT-23', '48-pin LQFP'")
    pins: int | None = Field(None, description="Pin/ball count if printed, else null")
    quote: str = Field(description="Verbatim span from the PAGE TEXT naming this package")
    page: int


class PackageList(BaseModel):
    packages: list[PackageRow]


SYSTEM = """You list the packages an electronic part is offered in, from datasheet page text.
Rules:
- Only packages this datasheet says the part (or its orderable variants) is offered in.
- name: the package name as printed (e.g. 'VQFN', 'SOIC', 'TSSOP', 'LQFP', 'BGA', 'SOT-23').
- quote: copy verbatim from the page text (under 200 characters); it must contain the name.
- page: the page number the quote is on.
- Skip anything unclear (e.g. package drawings of other parts, tape-and-reel only). At most 8."""


def select_pages(corpus: Corpus, doc_id: str, k: int = 4) -> list[int]:
    texts = corpus.pages(doc_id)
    hits = [n for n, t in enumerate(texts, 1) if _PAGES.search(t)]
    return sorted(dict.fromkeys([1, *hits[:k]]))


def extract(
    llm: LLMClient, corpus: Corpus, doc_ids: list[str], model: str, workers: int = 6
) -> list[dict]:
    """-> rows ``{doc_id, name, pins, quote, page}`` whose quote is found on its page."""

    def one(doc_id: str) -> list[dict]:
        pages = select_pages(corpus, doc_id)
        text = "\n\n".join(f"[page {p}]\n{corpus.page(doc_id, p)}" for p in pages)
        res = llm.complete(
            [Message(role="user", content=f"Datasheet: {doc_id}\n\n<<<\n{text}\n>>>")],
            model,
            system=SYSTEM,
            response_model=PackageList,
            max_tokens=4096,
            effort="low",
        )
        out, seen = [], set()
        for r in res.parsed.packages if res.parsed else []:
            if r.page not in pages or key(r.name) in seen:
                continue
            page = corpus.page(doc_id, r.page)
            found = find_span(r.quote, page) or find_tokens(r.quote, page)
            if not found or key(r.name) not in key(r.quote):
                continue
            seen.add(key(r.name))
            out.append({"doc_id": doc_id, **r.model_dump()})
        return out

    with ThreadPoolExecutor(workers) as ex:
        return [r for rows in ex.map(one, doc_ids) for r in rows]


def key(name: str) -> str:
    """Package family key: letters only ('48-pin LQFP' -> 'lqfp', 'SOT-23-5' -> 'sot')."""
    return re.sub(r"[^a-z]", "", normalize(name).replace("pin", ""))


_PLAIN = re.compile(r"^[A-Z][A-Z0-9]{2,7}$")  # 'VQFN', 'TSSOP', 'SOT23': unambiguous in answers
_THETA_JA = re.compile(r"(?i)junction[- ]to[- ]ambient|θ\s*ja|r\s*θ\s*ja|theta\s*ja|\bRθJA")


def foreign(own: list[str], pool: list[str]) -> list[str]:
    """Packages from ``pool`` that are clearly not one of ``own`` (no family-key containment,
    so 'QFN' is never a distractor for a 'WQFN' part, nor 'BGA' for 'FBGA')."""
    ks = [key(o) for o in own]
    return [
        p
        for p in pool
        if key(p) not in _GENERIC and not any(key(p) in k or k in key(p) for k in ks if len(k) >= 2)
    ]


# umbrella families: 'SON' covers TDFN/WSON/X2SON, 'BGA' covers FBGA/DSBGA, ...
_GENERIC = {"son", "sot", "qfn", "bga", "sop", "dip", "lga", "qfp", "so"}


def theta_ja(facts: list, doc_id: str) -> list:
    return [
        f
        for f in facts
        if f.doc_id == doc_id
        and f.section == "thermal"
        and _THETA_JA.search(f"{f.parameter} {f.symbol or ''}")
    ]
