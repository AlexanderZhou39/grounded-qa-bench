from gqb.schemas import Answer, Claim, EvidenceUnit, PipelineState, Verdict
from gqb.stages.retrieve import lifecycle_scope
from gqb.stages.verify import scrutinize_claim
from gqb.textmatch import align_quote


def test_lifecycle_scope_keeps_newest_and_flags_stale():
    revs = {
        "a~cur": {"family": "a", "date": "2023-08", "superseded_by": None},
        "a~old": {"family": "a", "date": "2019-04", "superseded_by": "a~cur"},
    }
    assert lifecycle_scope(["a~cur", "a~old", "x"], revs) == (["a~cur", "x"], [])
    assert lifecycle_scope(["a~old"], revs) == (["a~old"], ["a~old"])


def _state(claim_text: str, quote: str, unit_text: str, value: float) -> PipelineState:
    u = EvidenceUnit(id="E1", passage_id="p", doc_id="d", page=1, text=unit_text)
    claim = Claim(id="C1", text=claim_text, citations=["E1"], quote=quote)
    st = PipelineState(item_id="i", question="What was revenue?", evidence=[u])
    st.answer = Answer(text=claim_text, value=value, claims=[claim])
    return st


def test_scrutiny_allows_context_numbers_but_not_wrong_values():
    unit = "Fiscal year ended February 1, 2019\nSoftware maintenance revenue  4,351"
    ok = _state(
        "Revenue for the year ended February 1, 2019 was 4351.", "revenue  4,351", unit, 4351
    )
    assert scrutinize_claim(ok.answer.claims[0], ok, "t").verdict == Verdict.SUPPORTED
    bad = _state(
        "Revenue for the year ended February 1, 2019 was 4361.", "revenue  4,351", unit, 4361
    )
    assert scrutinize_claim(bad.answer.claims[0], bad, "t").verdict == Verdict.UNSUPPORTED
    scaled = _state("The current is 1.5 mA.", "ICC  1500  uA", "ICC  1500  uA", 1.5)
    assert scrutinize_claim(scaled.answer.claims[0], scaled, "t").verdict == Verdict.SUPPORTED


def test_align_quote_loose_tolerates_reordered_table_cells():
    page = "Iccphy  PHY Operating   -   30   60   mA\n   Supply Current   High-speed at 480 MHz\n"
    quote = "Iccphy PHY Operating Supply Current 30 60 mA High-speed at 480 MHz"
    assert align_quote(quote, page) is not None
    assert align_quote(quote, page, loose=False) is None
    assert align_quote(quote.replace("60", "70"), page) is None


def test_stale_only_scoring_accepts_explicit_warning():
    from gqb.score import STALE_WORDS

    assert STALE_WORDS.search("1.5 pF, but this comes from superseded revision B")
    assert not STALE_WORDS.search("The shunt capacitance is 1.5 pF.")


def test_near_miss_cond_rewrites_room_temperature():
    from gqb.dataset.generate import _ROOM, _TEMP, _hot

    assert _ROOM.sub(_hot, "VIN = 12V, TA = +25°C") == "VIN = 12V, TA = 85°C"
    assert _ROOM.sub(_hot, "TJ=25°C") == "TJ=85°C"
    assert not _ROOM.search("TA = 125°C") and _TEMP.search("TA = –40°C")


def test_grounding_metrics():
    from gqb.schemas import AnswerType, BenchItem, GoldEvidence, RunRecord, Tier
    from gqb.score import score_record

    item = BenchItem(
        id="d:t1:1",
        tier=Tier.DIRECT,
        question="What is the max VIN?",
        answer_type=AnswerType.NUMERIC,
        gold_answer=24.0,
        unit="V",
        tolerance=0.01,
        doc_ids=["d"],
        gold_evidence=[GoldEvidence(doc_id="d", page=1)],
    )

    def rec(value: float, unit_text: str, page: int) -> RunRecord:
        u = EvidenceUnit(id="E1", passage_id="p", doc_id="d", page=page, text=unit_text)
        claim = Claim(id="C1", text=f"VIN max is {value} V", citations=["E1"], quote=unit_text)
        st = PipelineState(item_id=item.id, question=item.question, evidence=[u])
        st.answer = Answer(text=claim.text, value=value, unit="V", claims=[claim])
        return RunRecord(combo_id="c", item_id=item.id, state=st)

    good = score_record(rec(24.0, "VIN 4.5 24 V", 1), item)
    assert good.correct and good.attributed and good.grounded_correct
    # right value, but cited from the wrong page: correct, not grounded
    lucky = score_record(rec(24.0, "VIN 4.5 24 V", 7), item)
    assert lucky.correct and not lucky.grounded_correct
    # right value, gold page cited, but the value is not in the cited evidence
    unattributed = score_record(rec(24.0, "VIN 4.5 to 18 V", 1), item)
    assert unattributed.correct and unattributed.attributed is False
