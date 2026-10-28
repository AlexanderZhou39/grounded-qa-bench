"""repair slot: act on verifier verdicts.

- drop_unsupported : remove unsupported/contradicted claims; abstain if no answer-bearing
                     claim survives.
- abstain          : abstain if any answer-bearing claim is not supported
                     (``allow_unverifiable`` keeps unverifiable derived claims; default true).
- regenerate       : if an answer-bearing claim fails, regenerate once with feedback
                     (cite generator, slot model). Not re-verified.
"""

from __future__ import annotations

from gqb.llm import StageMeter
from gqb.schemas import PipelineState, Verdict
from gqb.stages.base import BaseStage
from gqb.stages.common import answer_bearing
from gqb.stages.generate import _finish, generate_cited

BAD = {Verdict.UNSUPPORTED, Verdict.CONTRADICTED}


def _failing(state: PipelineState, allow_unverifiable: bool) -> list[str]:
    if state.answer is None or state.abstained:
        return []
    by_id = {v.claim_id: v for v in state.verdicts}
    bad = set(BAD) if allow_unverifiable else BAD | {Verdict.UNVERIFIABLE}
    return [
        c.id for c in answer_bearing(state.answer) if c.id in by_id and by_id[c.id].verdict in bad
    ]


class DropUnsupportedStage(BaseStage):
    slot = "repair"
    name = "drop_unsupported"
    uses_llm = False

    def apply(self, state: PipelineState, llm: StageMeter) -> PipelineState:
        if state.answer is None or state.abstained or not state.verdicts:
            return state
        by_id = {v.claim_id: v for v in state.verdicts}
        bearing = {c.id for c in answer_bearing(state.answer)}
        kept = [
            c for c in state.answer.claims if c.id not in by_id or by_id[c.id].verdict not in BAD
        ]
        state.scratch["dropped_claims"] = len(state.answer.claims) - len(kept)
        state.answer.claims = kept
        if bearing and not bearing & {c.id for c in kept}:
            state.abstained = True
        return state


class AbstainStage(BaseStage):
    slot = "repair"
    name = "abstain"
    uses_llm = False

    def apply(self, state: PipelineState, llm: StageMeter) -> PipelineState:
        if _failing(state, bool(self.params.get("allow_unverifiable", True))):
            state.abstained = True
        return state


class RegenerateStage(BaseStage):
    slot = "repair"
    name = "regenerate"

    def apply(self, state: PipelineState, llm: StageMeter) -> PipelineState:
        failing = _failing(state, True)
        if not failing:
            return state
        by_id = {v.claim_id: v for v in state.verdicts}
        notes = "\n".join(
            f"- {c.text} -> {by_id[c.id].verdict.value}: {by_id[c.id].rationale or ''}"
            for c in state.answer.claims
            if c.id in failing
        )
        state.scratch["regenerated"] = True
        out = generate_cited(
            self,
            state,
            llm,
            "\n\nA verifier rejected these claims from a previous answer:\n"
            f"{notes}\nAnswer again, fixing or removing them, or abstain.",
        )
        return _finish(state, out)
