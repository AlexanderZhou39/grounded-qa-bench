import json

from gqb.schemas import (
    AnswerType,
    BenchItem,
    GoldEvidence,
    PipelineState,
    RunRecord,
    StageTrace,
    Tier,
)
from gqb.stage import ComboSpec


def _item(**kw) -> BenchItem:
    base = dict(
        id="ti_tps51388:t1:0001",
        tier=Tier.DIRECT,
        question="What is the maximum input voltage of the TPS51388?",
        answer_type=AnswerType.NUMERIC,
        gold_answer=24.0,
        unit="V",
        tolerance=0.01,
        doc_ids=["ti_tps51388"],
        gold_evidence=[GoldEvidence(doc_id="ti_tps51388", page=4, quote="VIN ... 24 V")],
    )
    base.update(kw)
    return BenchItem(**base)


def test_bench_item_roundtrip():
    item = _item()
    again = BenchItem.model_validate(json.loads(item.model_dump_json()))
    assert again == item
    assert again.tier is Tier.DIRECT


def test_unanswerable_item():
    item = _item(
        tier=Tier.UNANSWERABLE,
        answer_type=AnswerType.UNANSWERABLE,
        gold_answer=None,
        gold_evidence=[],
    )
    assert item.gold_answer is None


def test_run_record_totals():
    state = PipelineState.from_item(_item())
    state.trace += [
        StageTrace(stage="retrieve", impl="bm25", latency_ms=5),
        StageTrace(stage="generate", impl="cite", model="m", cost_usd=0.002, latency_ms=900),
    ]
    rec = RunRecord(combo_id=ComboSpec().combo_id, item_id=state.item_id, state=state)
    assert rec.total_cost_usd == 0.002
    assert rec.total_latency_ms == 905
    assert RunRecord.model_validate_json(rec.model_dump_json()).state == state


def test_combo_id():
    spec = ComboSpec(verify="span_check", models={"generate": "a", "verify": "b"})
    assert spec.combo_id == "bm25|none|cite|span_check|none@generate=a,verify=b"
