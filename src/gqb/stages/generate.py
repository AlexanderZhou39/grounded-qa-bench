"""generate slot.

- plain        : answer only, no citations requested.
- cite         : answer + claims with evidence ids and verbatim quotes.
- gavel_debate : n debaters independently propose cited answers; each proposal goes through
                 mechanized scrutiny (ids exist, quotes align); a judge sees proposals + the
                 scrutiny report and issues the final cited answer or abstains.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from gqb.llm import Message, StageMeter
from gqb.schemas import PipelineState
from gqb.stages.base import BaseStage
from gqb.stages.common import ANSWER_RULES, CITE_RULES, GenOut, format_evidence, to_answer
from gqb.textmatch import find_span


def _ask(state: PipelineState, extra: str = "") -> list[Message]:
    return [
        Message(
            role="user",
            content=f"EVIDENCE:\n{format_evidence(state.evidence)}\n\n"
            f"QUESTION: {state.question}{extra}",
        )
    ]


def generate_cited(stage: BaseStage, state: PipelineState, llm: StageMeter, extra: str = ""):
    res = stage.complete(
        llm,
        _ask(state, extra),
        stage.model,
        system=ANSWER_RULES + CITE_RULES,
        response_model=GenOut,
        max_tokens=4096,
    )
    return res.parsed


def _finish(state: PipelineState, out: GenOut | None) -> PipelineState:
    if out is None:
        state.abstained = True
        return state
    state.answer = to_answer(out, {u.id for u in state.evidence})
    state.abstained = out.abstain or state.answer.value is None
    return state


class PlainStage(BaseStage):
    slot = "generate"
    name = "plain"

    def apply(self, state: PipelineState, llm: StageMeter) -> PipelineState:
        res = self.complete(
            llm,
            _ask(state),
            self.model,
            system=ANSWER_RULES + "\n- Leave claims empty.",
            response_model=GenOut,
            max_tokens=4096,
        )
        out = res.parsed
        if out is not None:
            out.claims = []
        return _finish(state, out)


class CiteStage(BaseStage):
    slot = "generate"
    name = "cite"

    def apply(self, state: PipelineState, llm: StageMeter) -> PipelineState:
        return _finish(state, generate_cited(self, state, llm))


def scrutinize(out: GenOut, state: PipelineState) -> list[str]:
    """Mechanized chain of scrutiny over a proposal. Returns human-readable problems."""
    units = {u.id: u for u in state.evidence}
    problems = []
    for i, c in enumerate(out.claims, 1):
        cites = [x.strip("[] ") for x in c.citations]
        missing = [x for x in cites if x not in units]
        if not cites:
            problems.append(f"claim {i} cites nothing")
        if missing:
            problems.append(f"claim {i} cites unknown ids {missing}")
        if c.quote and not any(find_span(c.quote, units[x].text) for x in cites if x in units):
            problems.append(f"claim {i} quote not found in cited evidence")
    return problems


class GavelDebateStage(BaseStage):
    slot = "generate"
    name = "gavel_debate"

    STANCES = [
        "Give the most direct answer the evidence supports.",
        "Be skeptical: check that the row, condition, column (min/typ/max) and part number "
        "exactly match the question before answering; abstain if they do not.",
        "Consider alternative readings of the evidence (other rows, variants, units) and "
        "argue for the best-supported one.",
    ]

    def apply(self, state: PipelineState, llm: StageMeter) -> PipelineState:
        n = int(self.params.get("debaters", 2))
        stances = self.STANCES[:n]
        with ThreadPoolExecutor(n) as ex:
            proposals = list(
                ex.map(
                    lambda s: generate_cited(self, state, llm, f"\n\nDEBATER INSTRUCTION: {s}"),
                    stances,
                )
            )
        report = []
        for i, p in enumerate(proposals, 1):
            if p is None:
                report.append(f"PROPOSAL {i}: (failed)")
                continue
            probs = scrutinize(p, state)
            report.append(
                f"PROPOSAL {i}:\n{p.model_dump_json(indent=1)}\n"
                f"SCRUTINY: {'; '.join(probs) if probs else 'all citations and quotes verified'}"
            )
        state.scratch["debate"] = report
        judge_model = self.params.get("judge_model", self.model)
        res = self.complete(
            llm,
            _ask(
                state,
                "\n\nYou are the JUDGE. Debaters proposed the answers below; a mechanical "
                "scrutinizer checked their citations. Select the sufficient evidence and "
                "issue the final answer with your own claims, or abstain if no proposal is "
                "properly supported.\n\n" + "\n\n".join(report),
            ),
            judge_model,
            system=ANSWER_RULES + CITE_RULES,
            response_model=GenOut,
            max_tokens=4096,
        )
        return _finish(state, res.parsed)
