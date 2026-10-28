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


def _typ_state(question: str, answer: str, row: str, value: float) -> PipelineState:
    st = _state(answer, row, f"PARAMETER MIN TYP MAX UNIT\n{row}", value)
    st.question = question
    return st


def test_column_check_flags_limits_answered_as_typical():
    from gqb.stages.verify import column_check_claim

    row = "VINL Input low voltage 0.8 V max"
    st = _typ_state("What is the typical VINL?", "The typical VINL is 0.8 V.", row, 0.8)
    assert column_check_claim(st.answer.claims[0], st, "t").verdict == Verdict.UNSUPPORTED
    said = _typ_state("What is the typical VINL?", "No typical; 0.8 V maximum.", "VINL 0.8 V", 0.8)
    assert column_check_claim(said.answer.claims[0], said, "t").verdict == Verdict.UNSUPPORTED
    # a limit word that is part of the asked parameter's name is not a limit label
    name = _typ_state(
        "What is the typical maximum frequency fMAX?", "fMAX is 20 MHz.", "fMAX 20 MHz", 20
    )
    assert column_check_claim(name.answer.claims[0], name, "t").verdict == Verdict.SUPPORTED
    # questions that do not ask for a typical value are left to span_check
    lim = _typ_state("What is the maximum VINL?", "The maximum VINL is 0.8 V.", row, 0.8)
    assert column_check_claim(lim.answer.claims[0], lim, "t").verdict == Verdict.SUPPORTED


def test_near_miss_cond_rewrites_room_temperature():
    from gqb.dataset.generate import _ROOM, _TEMP, _hot

    assert _ROOM.sub(_hot, "VIN = 12V, TA = +25°C") == "VIN = 12V, TA = 85°C"
    assert _ROOM.sub(_hot, "TJ=25°C") == "TJ=85°C"
    assert not _ROOM.search("TA = 125°C") and _TEMP.search("TA = –40°C")
