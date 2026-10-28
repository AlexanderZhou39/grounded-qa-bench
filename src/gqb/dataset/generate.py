"""Template-based BenchItem generation from verified facts.

Tiers (see docs/DATASET.md):
  1 direct       - parameter with a single row in the doc section
  2 conditional  - parameter with several rows; question pins the condition set
  3 derived      - range width, abs-max headroom, max-over-typ percentage (computed in code)
  5 unanswerable - parameter taken from a sibling doc and confirmed absent from this doc
  6 distractor   - tier-1 question, retrieval scope also includes a same-family sibling doc
                   that has the same parameter with a different value
"""

from __future__ import annotations

import random
import re
from collections import defaultdict

from gqb.corpus import Corpus, ManifestEntry
from gqb.dataset.extract import Fact, Section
from gqb.schemas import AnswerType, BenchItem, GoldEvidence, Tier
from gqb.textmatch import normalize, squash
from gqb.units import canon_unit, convert

FIELD_WORD = {"min": "minimum", "typ": "typical", "max": "maximum"}
SECTION_PHRASE = {
    Section.ABS_MAX: " as an absolute maximum rating",
    Section.RECOMMENDED: " under recommended operating conditions",
    Section.ELECTRICAL: "",
    Section.THERMAL: "",
    Section.OTHER: "",
}
DIRECT_TOL = 0.005
DERIVED_TOL = 0.02

_STOP = {"the", "and", "for", "with", "from", "time", "voltage", "current", "input", "output"}


def _pkey(f: Fact) -> str:
    return squash(f.parameter)


def _skey(f: Fact) -> str | None:
    return squash(f.symbol) if f.symbol and len(squash(f.symbol)) >= 2 else None


def _label(f: Fact) -> str:
    if f.symbol and squash(f.symbol) not in squash(f.parameter):
        return f"{f.parameter} ({f.symbol})"
    return f.parameter


MAX_COND_CHARS = 160
MAX_PARAM_CHARS = 70


_PARTNO = re.compile(r"\b(?!(?:LP)?DDR|JESD)[A-Z]{2,}[A-Z0-9]*\d[A-Z0-9]*-[A-Z0-9]{2,}")
_RAIL = r"\b[A-Z]*V[A-Z]{1,4}[A-Z0-9_]*\)?"


def _relative(f: Fact) -> bool:
    """Value printed relative to a supply (``AVCC2 – 0.5``): the bare number is not the spec."""
    for v in (f.min, f.typ, f.max):
        if v is None:
            continue
        num = re.escape(f"{abs(v):g}")
        if re.search(rf"{_RAIL}\s?[-+]\s?{num}(?![\d.])", re.sub(r"[−–—‐]", "-", f.quote)):
            return True
    return False


def _usable(f: Fact) -> bool:
    """Skip rows whose question would be unreadable (giant pin lists, run-on names), rows whose
    'condition' is another part number (a column header, not a condition), and values that
    are offsets from a supply rail."""
    return (
        len(_conds(f)) <= MAX_COND_CHARS
        and len(f.parameter) <= MAX_PARAM_CHARS
        and not _PARTNO.search(_conds(f))
        and not _relative(f)
    )


def _conds(f: Fact) -> str:
    return "; ".join(c.strip() for c in f.conditions if c.strip())


def _fields(f: Fact) -> list[str]:
    return [k for k in ("min", "typ", "max") if getattr(f, k) is not None]


def _question(mpn: str, f: Fact, field: str, with_conditions: bool) -> str:
    if f.section == Section.ABS_MAX and field != "typ":
        bound = "lower" if field == "min" else "upper"
        q = f"What is the {bound} limit of the absolute maximum rating for {_label(f)} of the {mpn}"
    else:
        q = f"What is the {FIELD_WORD[field]} {_label(f)} of the {mpn}{SECTION_PHRASE[f.section]}"
    if with_conditions and f.conditions:
        q += f", under the condition(s): {_conds(f)}"
    return q + "?"


def _ev(f: Fact) -> GoldEvidence:
    return GoldEvidence(doc_id=f.doc_id, page=f.page, quote=f.quote)


class Generator:
    def __init__(
        self,
        facts: list[Fact],
        corpus: Corpus,
        manifest: dict[str, ManifestEntry],
        seed: int = 13,
    ):
        self.facts = facts
        self.corpus = corpus
        self.manifest = manifest
        self.rng = random.Random(seed)
        self.by_doc: dict[str, list[Fact]] = defaultdict(list)
        for f in facts:
            if _usable(f):
                self.by_doc[f.doc_id].append(f)
        self._n = 0

    def _id(self, doc_id: str, tier: Tier) -> str:
        self._n += 1
        return f"{doc_id}:t{int(tier)}:{self._n:05d}"

    def _groups(self, doc_id: str) -> dict[tuple[str, str], list[Fact]]:
        g: dict[tuple[str, str], list[Fact]] = defaultdict(list)
        for f in self.by_doc[doc_id]:
            g[(f.section.value, _pkey(f))].append(f)
        return g

    # -- tier 1 / 2 ---------------------------------------------------------

    def direct_and_conditional(self, doc_id: str) -> tuple[list[BenchItem], list[BenchItem]]:
        mpn = self.manifest[doc_id].mpn
        t1, t2 = [], []
        for rows in self._groups(doc_id).values():
            if len(rows) == 1:
                f = rows[0]
                field = self.rng.choice(_fields(f))
                t1.append(self._numeric_item(doc_id, Tier.DIRECT, mpn, f, field, True))
                continue
            # tier 2: pick a row whose value for the field differs from all other rows
            for f in self.rng.sample(rows, len(rows)):
                if not f.conditions:
                    continue
                cond_keys = {tuple(normalize(c) for c in r.conditions) for r in rows}
                if len(cond_keys) < len(rows):  # duplicate condition sets -> ambiguous
                    break
                for field in _fields(f):
                    others = [getattr(r, field) for r in rows if r is not f]
                    if getattr(f, field) not in others:
                        t2.append(self._numeric_item(doc_id, Tier.CONDITIONAL, mpn, f, field, True))
                        break
                break
        return t1, t2

    def _numeric_item(
        self, doc_id: str, tier: Tier, mpn: str, f: Fact, field: str, with_cond: bool
    ) -> BenchItem:
        return BenchItem(
            id=self._id(doc_id, tier),
            tier=tier,
            question=_question(mpn, f, field, with_cond),
            answer_type=AnswerType.NUMERIC,
            gold_answer=float(getattr(f, field)),
            unit=canon_unit(f.unit),
            tolerance=DIRECT_TOL,
            conditions=f.conditions,
            doc_ids=[doc_id],
            gold_evidence=[_ev(f)],
            meta={"fact_ids": [f.id], "field": field, "section": f.section.value},
        )

    # -- tier 3 -------------------------------------------------------------

    def derived(self, doc_id: str) -> list[BenchItem]:
        mpn = self.manifest[doc_id].mpn
        out: list[BenchItem] = []
        facts = self.by_doc[doc_id]
        groups = self._groups(doc_id)
        for f in facts:
            if len(groups[(f.section.value, _pkey(f))]) > 1 and not f.conditions:
                continue  # ambiguous row
            cond = f", under the condition(s): {_conds(f)}" if f.conditions else ""
            if f.min is not None and f.max is not None and f.max > f.min:
                out.append(
                    self._derived_item(
                        doc_id,
                        f"For the {mpn}, what is the width of the specified {_label(f)} range "
                        f"(maximum minus minimum){SECTION_PHRASE[f.section]}{cond}?",
                        f.max - f.min,
                        canon_unit(f.unit),
                        [f],
                        "range_width",
                    )
                )
            if f.typ and f.max is not None and f.max > f.typ > 0:
                out.append(
                    self._derived_item(
                        doc_id,
                        f"For the {mpn}, by what percentage does the maximum {_label(f)} exceed "
                        f"its typical value{cond}?",
                        (f.max - f.typ) / f.typ * 100,
                        "%",
                        [f],
                        "max_over_typ_pct",
                    )
                )
        # headroom: abs max vs recommended max for the same symbol/parameter
        absmax = [f for f in facts if f.section == Section.ABS_MAX and f.max is not None]
        rec = [f for f in facts if f.section == Section.RECOMMENDED and f.max is not None]
        for a in absmax:
            matches = [
                r for r in rec if (_skey(a) and _skey(a) == _skey(r)) or _pkey(a) == _pkey(r)
            ]
            if len(matches) != 1 or a.conditions or matches[0].conditions:
                continue
            r = matches[0]
            r_in_a = convert(r.max, canon_unit(r.unit), canon_unit(a.unit))
            if r_in_a is None or a.max <= r_in_a:
                continue
            out.append(
                self._derived_item(
                    doc_id,
                    f"For the {mpn}, how much headroom is there between the recommended maximum "
                    f"{_label(r)} and its absolute maximum rating?",
                    a.max - r_in_a,
                    canon_unit(a.unit),
                    [a, r],
                    "absmax_headroom",
                )
            )
        return out

    def _derived_item(
        self,
        doc_id: str,
        question: str,
        value: float,
        unit: str | None,
        facts: list[Fact],
        formula: str,
    ) -> BenchItem:
        return BenchItem(
            id=self._id(doc_id, Tier.DERIVED),
            tier=Tier.DERIVED,
            question=question,
            answer_type=AnswerType.NUMERIC,
            gold_answer=round(value, 6),
            unit=unit,
            tolerance=DERIVED_TOL,
            conditions=[c for f in facts for c in f.conditions],
            doc_ids=[doc_id],
            gold_evidence=[_ev(f) for f in facts],
            meta={"fact_ids": [f.id for f in facts], "formula": formula},
        )

    # -- tier 5 -------------------------------------------------------------

    def _absent(self, doc_id: str, f: Fact) -> bool:
        """Conservative absence check: no page contains >= 60% of the parameter's content
        words (parenthetical qualifiers ignored), and the symbol (if distinctive) appears
        nowhere. Survivors are re-checked by an LLM over BM25 pages in ``qc.crosscheck``."""
        name = re.sub(r"\(.*?\)", " ", f.parameter)
        words = [w for w in re.findall(r"[a-z]{4,}", normalize(name)) if w not in _STOP]
        if len(words) < 2:
            return False
        sym = _skey(f)
        for page in self.corpus.pages(doc_id):
            np = normalize(page)
            if sum(w in np for w in words) / len(words) >= 0.6:
                return False
            if sym and len(sym) >= 3 and sym in squash(page):
                return False
        return True

    def unanswerable(self, doc_id: str, k: int) -> list[BenchItem]:
        entry = self.manifest[doc_id]
        siblings = [d for d, e in self.manifest.items() if e.family == entry.family and d != doc_id]
        # same-family only: borrowing from unrelated parts makes absence trivially obvious
        pool = [f for d in siblings for f in self.by_doc[d]]
        self.rng.shuffle(pool)
        out, seen = [], set()
        for f in pool:
            if len(out) >= k or _pkey(f) in seen:
                continue
            seen.add(_pkey(f))
            if not self._absent(doc_id, f):
                continue
            field = self.rng.choice(_fields(f))
            out.append(
                BenchItem(
                    id=self._id(doc_id, Tier.UNANSWERABLE),
                    tier=Tier.UNANSWERABLE,
                    question=_question(entry.mpn, f, field, False),
                    answer_type=AnswerType.UNANSWERABLE,
                    gold_answer=None,
                    doc_ids=[doc_id],
                    meta={"borrowed_fact": f.id},
                )
            )
        return out

    def near_miss(self, doc_id: str, k: int) -> list[BenchItem]:
        """Harder tier 5: ask for the *typical* value of a parameter the doc states only as a
        min and/or max limit (no row of that parameter anywhere in the doc has a typ). The
        part, parameter and table all exist, so a model that answers with the limit is wrong."""
        entry = self.manifest[doc_id]
        has_typ = {_pkey(f) for f in self.by_doc[doc_id] if f.typ is not None}
        rows = defaultdict(list)
        for f in self.by_doc[doc_id]:
            rows[_pkey(f)].append(f)
        pool = [
            fs[0]
            for key, fs in sorted(rows.items())
            if len(fs) == 1
            and key not in has_typ
            and fs[0].section in (Section.ELECTRICAL, Section.RECOMMENDED, Section.THERMAL)
            and (fs[0].min is not None or fs[0].max is not None)
        ]
        self.rng.shuffle(pool)
        out = []
        for f in pool[:k]:
            q = f"What is the typical {_label(f)} of the {entry.mpn}{SECTION_PHRASE[f.section]}"
            if f.conditions:
                q += f", under the condition(s): {_conds(f)}"
            out.append(
                BenchItem(
                    id=self._id(doc_id, Tier.UNANSWERABLE),
                    tier=Tier.UNANSWERABLE,
                    question=q + "?",
                    answer_type=AnswerType.UNANSWERABLE,
                    gold_answer=None,
                    doc_ids=[doc_id],
                    gold_evidence=[_ev(f)],
                    meta={
                        "kind": "near_miss_typ",
                        "fact_ids": [f.id],
                        "field": "typ",
                        "stated": {k: getattr(f, k) for k in ("min", "max")},
                        "mpn": entry.mpn,
                    },
                )
            )
        return out

    # -- tier 6 -------------------------------------------------------------

    def distractor(self, t1_items: list[BenchItem], k_per_doc: int) -> list[BenchItem]:
        fact_by_id = {f.id: f for f in self.facts}
        out: list[BenchItem] = []
        per_doc: dict[str, int] = defaultdict(int)
        for item in t1_items:
            doc_id = item.doc_ids[0]
            if per_doc[doc_id] >= k_per_doc:
                continue
            f = fact_by_id[item.meta["fact_ids"][0]]
            field = item.meta["field"]
            fam = self.manifest[doc_id].family
            for sib in (d for d, e in self.manifest.items() if e.family == fam and d != doc_id):
                twin = next(
                    (
                        s
                        for s in self.by_doc[sib]
                        if s.section == f.section
                        and ((_skey(f) and _skey(s) == _skey(f)) or _pkey(s) == _pkey(f))
                        and getattr(s, field) is not None
                        and getattr(s, field) != getattr(f, field)
                    ),
                    None,
                )
                if twin is None:
                    continue
                docs = [doc_id, sib]
                self.rng.shuffle(docs)
                out.append(
                    item.model_copy(
                        update={
                            "id": self._id(doc_id, Tier.DISTRACTOR),
                            "tier": Tier.DISTRACTOR,
                            "doc_ids": docs,
                            "meta": {
                                **item.meta,
                                "distractor_doc": sib,
                                "distractor_fact": twin.id,
                                "distractor_value": getattr(twin, field),
                            },
                        }
                    )
                )
                per_doc[doc_id] += 1
                break
        return out

    # -- tier 4 -------------------------------------------------------------

    def cross_doc(self, k_per_family: int) -> list[BenchItem]:
        """Which of two same-family parts has the higher/lower value of a parameter both state
        exactly once. Values must differ by >= 5% after unit conversion."""
        singles: dict[str, dict[tuple[str, str], Fact]] = {
            d: {k: rows[0] for k, rows in self._groups(d).items() if len(rows) == 1}
            for d in self.by_doc
        }
        fams: dict[str, list[str]] = defaultdict(list)
        for d in sorted(self.by_doc):
            fams[self.manifest[d].family].append(d)
        out: list[BenchItem] = []
        for docs in fams.values():
            cands = []
            for i, a in enumerate(docs):
                for b in docs[i + 1 :]:
                    for key in sorted(singles[a].keys() & singles[b].keys()):
                        fa, fb = singles[a][key], singles[b][key]
                        for field in ("max", "typ", "min"):
                            va, vb = getattr(fa, field), getattr(fb, field)
                            if va is None or vb is None:
                                continue
                            vb_in_a = convert(vb, fb.unit, fa.unit)
                            if vb_in_a is None or abs(va - vb_in_a) < 0.05 * max(
                                abs(va), abs(vb_in_a), 1e-12
                            ):
                                continue
                            cands.append((fa, fb, field, va, vb_in_a))
                            break
            self.rng.shuffle(cands)
            seen: set[str] = set()
            for fa, fb, field, va, vb in cands:
                if _pkey(fa) in seen or len(seen) >= k_per_family:
                    continue
                seen.add(_pkey(fa))
                higher = self.rng.random() < 0.5
                win = fa if (va > vb) == higher else fb
                ma, mb = self.manifest[fa.doc_id].mpn, self.manifest[fb.doc_id].mpn
                pair = [(fa, ma), (fb, mb)]
                self.rng.shuffle(pair)
                (f1, m1), (f2, m2) = pair
                q = (
                    f"Which part has the {'higher' if higher else 'lower'} {FIELD_WORD[field]} "
                    f"{_label(fa)}{SECTION_PHRASE[fa.section]}: the {m1} or the {m2}?"
                )
                out.append(
                    BenchItem(
                        id=self._id(fa.doc_id, Tier.CROSS_DOC),
                        tier=Tier.CROSS_DOC,
                        question=q,
                        answer_type=AnswerType.ENUM,
                        gold_answer=self.manifest[win.doc_id].mpn,
                        doc_ids=[f1.doc_id, f2.doc_id],
                        gold_evidence=[_ev(f1), _ev(f2)],
                        meta={
                            "fact_ids": [f1.id, f2.id],
                            "field": field,
                            "section": fa.section.value,
                            "options": [m1, m2],
                            "values": {m1: getattr(f1, field), m2: getattr(f2, field)},
                            "units": {m1: f1.unit, m2: f2.unit},
                            "direction": "higher" if higher else "lower",
                            "mpn": f"{m1} {m2}",
                        },
                    )
                )
        return out

    # -- all ----------------------------------------------------------------

    def generate(
        self,
        per_doc: dict[Tier, int] | None = None,
        per_family_cross: int = 6,
        near_miss_per_doc: int = 2,
    ) -> list[BenchItem]:
        per_doc = per_doc or {
            Tier.DIRECT: 8,
            Tier.CONDITIONAL: 6,
            Tier.DERIVED: 4,
            Tier.UNANSWERABLE: 3,
            Tier.DISTRACTOR: 3,
        }
        items: list[BenchItem] = []
        all_t1: list[BenchItem] = []
        for doc_id in sorted(self.by_doc):
            t1, t2 = self.direct_and_conditional(doc_id)
            t3 = self.derived(doc_id)
            self.rng.shuffle(t1)
            self.rng.shuffle(t2)
            self.rng.shuffle(t3)
            all_t1 += t1
            items += t1[: per_doc[Tier.DIRECT]]
            items += t2[: per_doc[Tier.CONDITIONAL]]
            items += _diverse(t3, per_doc[Tier.DERIVED])
            items += self.unanswerable(doc_id, per_doc[Tier.UNANSWERABLE])
        items += self.distractor(all_t1, per_doc[Tier.DISTRACTOR])
        for it in items:
            it.meta["mpn"] = self.manifest[
                it.doc_ids[0] if it.tier != Tier.DISTRACTOR else it.id.split(":")[0]
            ].mpn
        # appended last so adding tier 4 does not change earlier items' ids or RNG draws
        items += self.cross_doc(per_family_cross)
        for doc_id in sorted(self.by_doc):
            items += self.near_miss(doc_id, near_miss_per_doc)
        return items


def _diverse(items: list[BenchItem], k: int) -> list[BenchItem]:
    """Round-robin across derived formulas so one formula doesn't dominate."""
    by: dict[str, list[BenchItem]] = defaultdict(list)
    for it in items:
        by[it.meta["formula"]].append(it)
    out: list[BenchItem] = []
    while len(out) < k and any(by.values()):
        for key in list(by):
            if by[key] and len(out) < k:
                out.append(by[key].pop())
    return out
