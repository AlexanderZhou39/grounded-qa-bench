"""End-to-end over a two-page fake corpus with a scripted FakeClient: every registered stage
runs, the runner resumes, and the report renders. No network."""

import json
import re

import pytest

from gqb.corpus import Corpus, read_jsonl
from gqb.llm import FakeClient, Message
from gqb.report import write_html
from gqb.retrieve import BM25Retriever
from gqb.runner import RunConfig, run_combo
from gqb.schemas import AnswerType, BenchItem, GoldEvidence, PipelineState, Tier, Verdict
from gqb.score import aggregate, score_record
from gqb.stage import ComboSpec
from gqb.stages import REGISTRY, StageContext, build_pipeline

ROW = "VIN   Input voltage range   4.5   24   V"
PAGES = {
    "acme_x100": [
        "ACME X100 buck converter\nRecommended operating conditions\n"
        f"Parameter  MIN  TYP  MAX  UNIT\n{ROW}\nTJ   Junction temperature  -40  125  C\n",
        "Typical application\nThe X100 drives loads up to 3 A with a small inductor.\n",
    ],
}

ANSWERABLE = BenchItem(
    id="acme_x100:t1:1",
    tier=Tier.DIRECT,
    question="What is the maximum input voltage range VIN of the X100?",
    answer_type=AnswerType.NUMERIC,
    gold_answer=24.0,
    unit="V",
    tolerance=0.005,
    doc_ids=["acme_x100"],
    gold_evidence=[GoldEvidence(doc_id="acme_x100", page=1, quote=ROW)],
)
UNANSWERABLE = BenchItem(
    id="acme_x100:t5:2",
    tier=Tier.UNANSWERABLE,
    question="What is the switching frequency fSW of the X100?",
    answer_type=AnswerType.UNANSWERABLE,
    gold_answer=None,
    doc_ids=["acme_x100"],
)


def responder(messages: list[Message], system: str | None, model: str):
    """Scripted model. Abstains when the question mentions switching frequency."""
    user = messages[-1].content
    system = system or ""
    unans = "switching frequency" in user
    if system.startswith("Select the passages"):
        return {"relevant": [] if unans else ["P1"]}
    if system.startswith("Extract the minimal"):
        return {"excerpts": [] if unans else [{"passage": "P1", "quote": ROW}]}
    if system.startswith("Draft an evidence contract"):
        b = {"subclaim": "VIN max is 24 V", "passage": "P1", "quote": ROW}
        return {"bindings": [] if unans else [b]}
    if system.startswith("Split the answer"):
        return {"claims": ["The X100 maximum input voltage is 24 V."]}
    if "verbatim supporting quote" in system:
        return {
            "verdicts": [{"index": 0, "verdict": "supported", "evidence_id": "E1", "quote": ROW}]
        }
    if system.startswith("You verify claims"):
        ids = re.findall(r"^\[(\w+)\]", user, re.M)
        return {
            "claims": [
                {"claim_id": i, "verdict": "supported", "confidence": 0.9, "rationale": "ok"}
                for i in ids
                if not i.startswith("E")
            ]
        }
    if system.startswith("Answer the question"):
        if unans or "E1" not in user:
            return {"abstain": True, "answer_text": "Not stated.", "claims": []}
        return {
            "abstain": False,
            "answer_text": "The maximum VIN is 24 V.",
            "value": 24,
            "unit": "V",
            "claims": [
                {"text": "The X100 maximum VIN is 24 V.", "citations": ["E1"], "quote": ROW}
            ],
        }
    raise AssertionError(f"unscripted prompt: {system[:60]}")


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for doc, pages in PAGES.items():
        d = tmp_path / "data" / "corpus" / doc
        d.mkdir(parents=True)
        for i, text in enumerate(pages, 1):
            (d / f"{i:04d}.txt").write_text(text)
    return Corpus(root=tmp_path / "data" / "corpus")


def _ctx(corpus):
    return StageContext(corpus=corpus, retriever=BM25Retriever(corpus, cache_dir=None), top_k=2)


LOCAL_EXTRA = {"dense", "hybrid", "nli_small"}  # need the optional `local` extra (models)


def _combos():
    yield ComboSpec()
    for slot in ("cull", "generate", "verify", "repair"):
        for impl in REGISTRY[slot]:
            if impl in LOCAL_EXTRA:
                continue
            kw = {"generate": "cite", slot: impl}
            if slot == "repair":
                kw["verify"] = "span_check"
            yield ComboSpec(**kw)


@pytest.mark.parametrize("spec", list(_combos()), ids=lambda s: s.combo_id)
def test_every_stage(corpus, spec):
    pipe = build_pipeline(spec, _ctx(corpus))
    llm = FakeClient(responder)
    for item, want_abstain in [(ANSWERABLE, False), (UNANSWERABLE, True)]:
        state = pipe.run(PipelineState.from_item(item), llm)
        assert [t.stage for t in state.trace] == [
            "retrieve",
            "cull",
            "generate",
            "verify",
            "repair",
        ]
        assert not any(t.error for t in state.trace), state.trace
        assert state.abstained == want_abstain
        if not want_abstain:
            assert state.answer.value == 24
            if spec.verify != "none":
                assert state.verdicts
                assert all(v.verdict == Verdict.SUPPORTED for v in state.verdicts)


def test_span_check_catches_unsupported_number(corpus):
    def liar(messages, system, model):
        out = responder(messages, system, model)
        if (system or "").startswith("Answer the question") and not out["abstain"]:
            out["value"] = 30
            out["claims"][0]["text"] = "The X100 maximum VIN is 30 V."
        return out

    pipe = build_pipeline(ComboSpec(verify="span_check", repair="abstain"), _ctx(corpus))
    state = pipe.run(PipelineState.from_item(ANSWERABLE), FakeClient(liar))
    assert state.verdicts[0].verdict == Verdict.UNSUPPORTED
    assert state.abstained


def test_stage_error_is_recorded(corpus):
    def broken(messages, system, model):
        return "not json"

    pipe = build_pipeline(ComboSpec(), _ctx(corpus))
    state = pipe.run(PipelineState.from_item(ANSWERABLE), FakeClient(broken))
    assert state.trace[-1].stage == "generate" and state.trace[-1].error
    assert len(state.trace) == 3


def test_runner_resume_score_report(corpus, tmp_path):
    items = [ANSWERABLE, UNANSWERABLE]
    cfg = RunConfig(name="t", repeats=2, concurrency=2, top_k=2)
    llm = FakeClient(responder)
    specs = [ComboSpec(), ComboSpec(cull="evident_align", verify="cascade")]
    outs = [run_combo(s, items, cfg, llm, corpus, progress=False) for s in specs]
    n_calls = len(llm.calls)
    run_combo(specs[0], items, cfg, llm, corpus, progress=False)  # resume: nothing to do
    assert len(llm.calls) == n_calls
    for out in outs:
        assert len(read_jsonl(out)) == 4

    from gqb.schemas import RunRecord

    by_id = {i.id: i for i in items}
    scores = [
        score_record(RunRecord.model_validate(r), by_id[r["item_id"]])
        for out in outs
        for r in read_jsonl(out)
    ]
    aggs = aggregate(scores)
    overall = [a for a in aggs if a.tier == "all"]
    assert len(overall) == 2
    assert all(a.accuracy == 1.0 and a.consistency == 1.0 for a in overall)
    assert all(a.cite_precision == 1.0 for a in overall)
    html_path = tmp_path / "report.html"
    write_html(aggs, html_path)
    html = html_path.read_text()
    assert "<svg" in html and "Tier 5" in html
    json.dumps([a.model_dump() for a in aggs])


def test_verifier_metrics(corpus):
    from gqb.schemas import RunRecord

    def liar(messages, system, model):
        out = responder(messages, system, model)
        if (system or "").startswith("Answer the question") and not out["abstain"]:
            out["value"] = 30
            out["claims"][0]["text"] = "The X100 maximum VIN is 30 V."
        return out

    spec = ComboSpec(verify="span_check", repair="abstain")
    pipe = build_pipeline(spec, _ctx(corpus))
    recs = []
    for fn in (responder, liar):
        st = pipe.run(PipelineState.from_item(ANSWERABLE), FakeClient(fn))
        recs.append(RunRecord(combo_id=spec.combo_id, item_id=ANSWERABLE.id, repeat=0, state=st))
    good, bad = (score_record(r, ANSWERABLE) for r in recs)
    assert good.raw_correct and good.flagged is False
    assert bad.raw_correct is False and bad.flagged and not bad.correct
    agg = next(a for a in aggregate([good, bad]) if a.tier == "all")
    assert agg.verifier_catch == 1.0 and agg.verifier_false_flag == 0.0
