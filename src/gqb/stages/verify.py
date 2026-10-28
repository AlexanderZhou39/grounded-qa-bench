"""verify slot: produce ClaimVerdicts for the answer's claims.

- span_check       : deterministic. Every number in the claim must appear in the cited
                     evidence text. Derived claims with unmatched numbers -> unverifiable.
- gavel_scrutinize : deterministic. Cited ids exist, the claim's quote aligns inside a cited
                     unit, and the claim's numbers appear in that quote. ``align: loose`` also
                     accepts order-free token alignment (table rows split by PDF extraction).
- llm_judge        : one LLM call judges all claims against the evidence.
- claim_decompose  : rag-facts-check style. LLM re-decomposes the answer text into atomic
                     claims, then a second call verifies each with a quote; quotes are aligned
                     deterministically and a claim is supported only if its quote aligns.
- nli_small        : local NLI cross-encoder, sentence x evidence-window max entailment.
- cascade          : cheap deterministic verifier first; claims under ``escalate_below``
                     confidence go to an expensive LLM verifier.

Answers without claims (generate=plain) get one implicit claim made from the answer text,
citing all evidence.
"""

from __future__ import annotations

import re
import threading
from typing import Any, Literal

from pydantic import BaseModel, Field

from gqb.llm import Message, StageMeter
from gqb.schemas import Claim, ClaimVerdict, PipelineState, Verdict
from gqb.stages.base import BaseStage
from gqb.stages.common import format_evidence, unit_map
from gqb.textmatch import contains_number, find_span, find_tokens, numbers_in


def _claims(state: PipelineState) -> list[Claim]:
    if state.answer is None or state.abstained:
        return []
    if state.answer.claims:
        return state.answer.claims
    implicit = Claim(id="C0", text=state.answer.text, citations=[u.id for u in state.evidence])
    state.answer.claims = [implicit]
    return state.answer.claims


def _question_numbers(state: PipelineState) -> list[float]:
    return numbers_in(state.question)


def span_check_claim(claim: Claim, state: PipelineState, verifier: str) -> ClaimVerdict:
    units = unit_map(state)
    cited = [units[c] for c in claim.citations if c in units]
    if not cited:
        return ClaimVerdict(
            claim_id=claim.id,
            verdict=Verdict.UNSUPPORTED,
            confidence=0.9,
            verifier=verifier,
            rationale="no valid citation",
        )
    text = "\n".join(u.text for u in cited)
    qnums = _question_numbers(state)
    nums = [n for n in numbers_in(claim.text) if not any(abs(n - q) < 1e-9 for q in qnums)]
    missing = [n for n in nums if not contains_number(text, n)]
    if not nums:
        return ClaimVerdict(
            claim_id=claim.id,
            verdict=Verdict.SUPPORTED,
            confidence=0.5,
            verifier=verifier,
            evidence_ids=[u.id for u in cited],
            rationale="no numbers to check",
        )
    if not missing:
        return ClaimVerdict(
            claim_id=claim.id,
            verdict=Verdict.SUPPORTED,
            confidence=0.95,
            verifier=verifier,
            evidence_ids=[u.id for u in cited],
        )
    if claim.derived:
        return ClaimVerdict(
            claim_id=claim.id,
            verdict=Verdict.UNVERIFIABLE,
            confidence=0.4,
            verifier=verifier,
            evidence_ids=[u.id for u in cited],
            rationale=f"derived values {missing} not stated",
        )
    return ClaimVerdict(
        claim_id=claim.id,
        verdict=Verdict.UNSUPPORTED,
        confidence=0.8,
        verifier=verifier,
        evidence_ids=[u.id for u in cited],
        rationale=f"numbers {missing} not in cited evidence",
    )


def _aligns(quote: str, text: str, align: str) -> bool:
    return bool(find_span(quote, text)) or (align == "loose" and find_tokens(quote, text))


def scrutinize_claim(
    claim: Claim, state: PipelineState, verifier: str, align: str = "strict"
) -> ClaimVerdict:
    units = unit_map(state)
    cited = [units[c] for c in claim.citations if c in units]
    bad_ids = [c for c in claim.citations if c not in units]
    if not cited or bad_ids:
        return ClaimVerdict(
            claim_id=claim.id,
            verdict=Verdict.UNSUPPORTED,
            confidence=0.9,
            verifier=verifier,
            rationale=f"invalid citations {bad_ids}" if bad_ids else "no citation",
        )
    if not claim.quote:
        return ClaimVerdict(
            claim_id=claim.id,
            verdict=Verdict.UNVERIFIABLE,
            confidence=0.3,
            verifier=verifier,
            rationale="no quote",
        )
    host = next((u for u in cited if _aligns(claim.quote, u.text, align)), None)
    if host is None:
        return ClaimVerdict(
            claim_id=claim.id,
            verdict=Verdict.UNSUPPORTED,
            confidence=0.85,
            verifier=verifier,
            rationale="quote not found in cited evidence",
        )
    qnums = _question_numbers(state)
    nums = [n for n in numbers_in(claim.text) if not any(abs(n - q) < 1e-9 for q in qnums)]
    missing = [n for n in nums if not contains_number(claim.quote, n)]
    if missing and claim.derived:
        return ClaimVerdict(
            claim_id=claim.id,
            verdict=Verdict.UNVERIFIABLE,
            confidence=0.5,
            verifier=verifier,
            evidence_ids=[host.id],
            rationale=f"derived values {missing}; inputs quoted",
        )
    if missing:
        return ClaimVerdict(
            claim_id=claim.id,
            verdict=Verdict.UNSUPPORTED,
            confidence=0.7,
            verifier=verifier,
            evidence_ids=[host.id],
            rationale=f"numbers {missing} not in quote",
        )
    return ClaimVerdict(
        claim_id=claim.id,
        verdict=Verdict.SUPPORTED,
        confidence=0.95,
        verifier=verifier,
        evidence_ids=[host.id],
    )


class SpanCheckStage(BaseStage):
    slot = "verify"
    name = "span_check"
    uses_llm = False

    def apply(self, state: PipelineState, llm: StageMeter) -> PipelineState:
        state.verdicts = [span_check_claim(c, state, self.name) for c in _claims(state)]
        return state


class GavelScrutinizeStage(BaseStage):
    slot = "verify"
    name = "gavel_scrutinize"
    uses_llm = False

    def apply(self, state: PipelineState, llm: StageMeter) -> PipelineState:
        align = self.params.get("align", "strict")
        state.verdicts = [scrutinize_claim(c, state, self.name, align) for c in _claims(state)]
        return state


class JudgedClaim(BaseModel):
    claim_id: str
    verdict: Literal["supported", "unsupported", "contradicted"]
    confidence: float = Field(ge=0, le=1)
    evidence_ids: list[str] = Field(default_factory=list)
    rationale: str


class Judgement(BaseModel):
    claims: list[JudgedClaim]


JUDGE_SYSTEM = (
    "You verify claims against evidence. A claim is supported only if the evidence states it "
    "for the same part and conditions, or it follows by correct arithmetic from stated values. "
    "contradicted = evidence states something incompatible. Otherwise unsupported. "
    "Return one verdict per claim id."
)


def llm_judge_claims(
    stage: BaseStage, state: PipelineState, llm: StageMeter, claims: list[Claim], model: str
) -> list[ClaimVerdict]:
    if not claims:
        return []
    listing = "\n".join(f"[{c.id}] {c.text} (cites: {', '.join(c.citations)})" for c in claims)
    res = stage.complete(
        llm,
        [
            Message(
                role="user",
                content=f"EVIDENCE:\n{format_evidence(state.evidence)}\n\n"
                f"QUESTION: {state.question}\n\nCLAIMS:\n{listing}",
            )
        ],
        model,
        system=JUDGE_SYSTEM,
        response_model=Judgement,
        max_tokens=4096,
    )
    got = {j.claim_id.strip("[] "): j for j in (res.parsed.claims if res.parsed else [])}
    out = []
    for c in claims:
        j = got.get(c.id)
        if j is None:
            out.append(
                ClaimVerdict(
                    claim_id=c.id,
                    verdict=Verdict.UNVERIFIABLE,
                    confidence=0.0,
                    verifier=stage.name,
                    rationale="judge returned no verdict",
                )
            )
            continue
        out.append(
            ClaimVerdict(
                claim_id=c.id,
                verdict=Verdict(j.verdict),
                confidence=j.confidence,
                evidence_ids=j.evidence_ids,
                verifier=stage.name,
                rationale=j.rationale,
            )
        )
    return out


class LLMJudgeStage(BaseStage):
    slot = "verify"
    name = "llm_judge"

    def apply(self, state: PipelineState, llm: StageMeter) -> PipelineState:
        state.verdicts = llm_judge_claims(self, state, llm, _claims(state), self.model)
        return state


class AtomicClaims(BaseModel):
    claims: list[str] = Field(description="Atomic, self-contained factual statements")


class QuotedVerdict(BaseModel):
    index: int
    verdict: Literal["supported", "unsupported", "contradicted"]
    evidence_id: str | None = None
    quote: str | None = Field(None, description="Verbatim evidence span, if supported")


class QuotedVerdicts(BaseModel):
    verdicts: list[QuotedVerdict]


class ClaimDecomposeStage(BaseStage):
    slot = "verify"
    name = "claim_decompose"

    def apply(self, state: PipelineState, llm: StageMeter) -> PipelineState:
        if state.answer is None or state.abstained:
            return state
        dec = self.complete(
            llm,
            [Message(role="user", content=f"Q: {state.question}\nA: {state.answer.text}")],
            self.model,
            system="Split the answer into atomic factual claims. Each claim must be "
            "self-contained (name the part, parameter, condition and value). Skip hedges.",
            response_model=AtomicClaims,
            max_tokens=2048,
        )
        atoms = dec.parsed.claims if dec.parsed else []
        if not atoms:
            atoms = [state.answer.text]
        listing = "\n".join(f"{i}. {a}" for i, a in enumerate(atoms))
        ver = self.complete(
            llm,
            [
                Message(
                    role="user",
                    content=f"EVIDENCE:\n{format_evidence(state.evidence)}\n\nCLAIMS:\n{listing}",
                )
            ],
            self.model,
            system=JUDGE_SYSTEM + " For supported claims give the evidence id and copy a "
            "verbatim supporting quote.",
            response_model=QuotedVerdicts,
            max_tokens=4096,
        )
        units = unit_map(state)
        got = {v.index: v for v in (ver.parsed.verdicts if ver.parsed else [])}
        state.scratch["generator_claims"] = [c.model_dump() for c in state.answer.claims]
        claims, verdicts = [], []
        for i, a in enumerate(atoms):
            cid = f"D{i}"
            v = got.get(i)
            eid = (v.evidence_id or "").strip("[] ") if v else ""
            claims.append(
                Claim(id=cid, text=a, citations=[eid] if eid else [], quote=v.quote if v else None)
            )
            if v is None:
                verdict, conf = Verdict.UNVERIFIABLE, 0.0
            elif v.verdict == "supported":
                aligned = eid in units and v.quote and find_span(v.quote, units[eid].text)
                verdict, conf = (Verdict.SUPPORTED, 0.9) if aligned else (Verdict.UNSUPPORTED, 0.6)
            else:
                verdict, conf = Verdict(v.verdict), 0.8
            verdicts.append(
                ClaimVerdict(
                    claim_id=cid,
                    verdict=verdict,
                    confidence=conf,
                    verifier=self.name,
                    evidence_ids=[eid] if eid else [],
                )
            )
        state.answer.claims = claims
        state.verdicts = verdicts
        return state


class CascadeStage(BaseStage):
    """params: cheap (span_check|gavel_scrutinize), escalate_below (float),
    expensive_model (defaults to slot model)."""

    slot = "verify"
    name = "cascade"

    def apply(self, state: PipelineState, llm: StageMeter) -> PipelineState:
        cheap = self.params.get("cheap", "gavel_scrutinize")
        threshold = float(self.params.get("escalate_below", 0.7))
        align = self.params.get("align", "strict")
        claims = _claims(state)
        first = [
            scrutinize_claim(c, state, f"cascade:{cheap}", align)
            if cheap == "gavel_scrutinize"
            else span_check_claim(c, state, f"cascade:{cheap}")
            for c in claims
        ]
        escalate = [
            c for c, v in zip(claims, first, strict=True) if (v.confidence or 0) < threshold
        ]
        judged = {
            v.claim_id: v
            for v in llm_judge_claims(
                self, state, llm, escalate, self.params.get("expensive_model", self.model)
            )
        }
        state.verdicts = [judged.get(v.claim_id, v) for v in first]
        state.scratch["cascade_escalated"] = len(escalate)
        return state


_NLI: dict[str, Any] = {}
_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")


_NLI_LOCK = threading.Lock()


def _nli_model(name: str):
    with _NLI_LOCK:
        return _load_nli(name)


def _load_nli(name: str):
    if name not in _NLI:
        import os

        from sentence_transformers import CrossEncoder

        _NLI[name] = CrossEncoder(name, device=os.environ.get("GQB_DEVICE", "cpu"))
    return _NLI[name]


def _windows(text: str, size: int = 1200, stride: int = 900) -> list[str]:
    if len(text) <= size:
        return [text]
    return [text[i : i + size] for i in range(0, max(1, len(text) - size + stride), stride)]


class NLISmallStage(BaseStage):
    """Local NLI cross-encoder (extra ``local``; CPU by default), SummaC-style: the claim is
    split into sentences, each scored against overlapping windows of its cited evidence, and a
    sentence counts as entailed if any window entails it. Claim verdict: all sentences entailed
    -> supported; a sentence contradicted (and not entailed) -> contradicted; else unsupported.
    Params: ``nli_model``, ``threshold`` (entailment probability, default 0.5)."""

    slot = "verify"
    name = "nli_small"
    uses_llm = False

    def apply(self, state: PipelineState, llm: StageMeter) -> PipelineState:
        import numpy as np

        model = _nli_model(self.params.get("nli_model", "cross-encoder/nli-deberta-v3-base"))
        labels = {v.lower(): k for k, v in model.model.config.id2label.items()}
        thr = float(self.params.get("threshold", 0.5))
        units = unit_map(state)
        verdicts = []
        for c in _claims(state):
            premises = [w for cid in c.citations if cid in units for w in _windows(units[cid].text)]
            if not premises:
                verdicts.append(
                    ClaimVerdict(claim_id=c.id, verdict=Verdict.UNSUPPORTED, verifier=self.name)
                )
                continue
            sents = [s for s in _SENT.split(c.text.strip()) if len(s.split()) >= 3] or [c.text]
            pairs = [(p, s) for s in sents for p in premises]
            logits = np.asarray(model.predict(pairs, batch_size=32))
            probs = np.exp(logits - logits.max(1, keepdims=True))
            probs /= probs.sum(1, keepdims=True)
            probs = probs.reshape(len(sents), len(premises), -1)
            ent = probs[:, :, labels["entailment"]].max(1)
            con = probs[:, :, labels["contradiction"]].max(1)
            if (ent >= thr).all():
                verdict = Verdict.SUPPORTED
            elif ((con >= thr) & (ent < thr)).any():
                verdict = Verdict.CONTRADICTED
            else:
                verdict = Verdict.UNSUPPORTED
            verdicts.append(
                ClaimVerdict(
                    claim_id=c.id,
                    verdict=verdict,
                    confidence=float(ent.min()),
                    verifier=self.name,
                )
            )
        state.verdicts = verdicts
        return state
