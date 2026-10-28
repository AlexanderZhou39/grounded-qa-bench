from gqb.dataset.extract import Fact, Section
from gqb.dataset.generate import _PARTNO, _relative, _usable
from gqb.textmatch import contains_number, find_span, normalize, numbers_in
from gqb.units import canon_unit, convert, numeric_match


def test_normalize_symbols():
    assert normalize("–0.3 V to +25 µA, 10 Ω, 85 ºC") == "-0.3 v to +25 ua, 10 ohm, 85 degc"


def test_numbers_and_contains():
    assert numbers_in("VCC 2.3 V to 4.8 V, –0.5") == [2.3, 4.8, -0.5]
    assert contains_number("IOH = –1.0 mA", 1.0)  # sign-insensitive
    assert not contains_number("12.5 V", 2.5)


def test_find_span_stages():
    page = "Parameter  MIN  TYP  MAX\nVIN   Input  voltage   4.5   24   V\n"
    assert find_span("VIN   Input  voltage   4.5   24   V", page).stage == "exact"
    assert find_span("VIN Input voltage 4.5 24 V", page).stage in {"normalized", "squashed"}
    assert find_span("VOUT output 3.3 V nothing like it", page) is None
    assert find_span("", page) is None


def test_units():
    assert convert(1.5, "mA", "uA") == 1500.0
    assert convert(1, "kHz", "MHz") == 0.001
    assert convert(5, "V", "A") is None
    assert canon_unit("µA") == "uA"
    assert canon_unit("ºC") == canon_unit("°C") == "degC"
    assert convert(0.5, "%VDDQ", "% VDDQ") == 0.5


def test_numeric_match():
    assert numeric_match(1500, "uA", 1.5, "mA", 0.005)
    assert numeric_match(1.0, "mA", -1.0, "mA", 0.005)  # current sign convention
    assert not numeric_match(-3.3, "V", 3.3, "V", 0.005)
    assert numeric_match(-55, "ºC", -55, "°C", 0.005)
    assert numeric_match(-65, "oC", -65, "degC", 0.005)


def _fact(**kw) -> Fact:
    base = dict(
        id="d:1:000",
        doc_id="d",
        page=1,
        quote="",
        quote_match="exact",
        section=Section.ELECTRICAL,
        parameter="Output high voltage",
        symbol="VOH",
        conditions=[],
        min=None,
        typ=None,
        max=None,
        unit="V",
    )
    base.update(kw)
    return Fact(**base)


def test_generator_filters():
    rel = _fact(min=-0.5, quote="P40 to P47   VOH   AVCC2 – 0.5  —    —     V")
    assert _relative(rel) and not _usable(rel)
    table = _fact(min=-10, max=60, quote="Operating Temperature    -10     +60   ºC")
    assert not _relative(table) and _usable(table)
    assert _PARTNO.search("XGL4040-223ME_")
    assert not _PARTNO.search("DDR4-2133")
    assert not _PARTNO.search("per JEDEC specification JESD22-C101")


def test_find_tokens_table_rows():
    from gqb.textmatch import find_tokens

    page = (
        "Iccphy  PHY Operating   -   30   60   mA\n"
        "   Supply Current   High-speed operation at 480 MHz\n"
    )
    quote = "Iccphy PHY Operating Supply Current 30 60 mA High-speed operation at 480 MHz"
    assert find_span(quote, page) is None
    assert find_tokens(quote, page)
    assert not find_tokens(quote.replace("60", "70"), page)


def test_enum_scoring():
    from gqb.schemas import AnswerType, BenchItem, GoldEvidence, Tier
    from gqb.score import enum_correct

    item = BenchItem(
        id="x:t4:1",
        tier=Tier.CROSS_DOC,
        question="Which part has the higher max Tstg: the A1 or the B2?",
        answer_type=AnswerType.ENUM,
        gold_answer="A1",
        doc_ids=["a", "b"],
        gold_evidence=[GoldEvidence(doc_id="a", page=1)],
        meta={
            "options": ["A1", "B2"],
            "values": {"A1": 150, "B2": 125},
            "units": {"A1": "°C", "B2": "°C"},
        },
    )
    assert enum_correct("The A1", None, item)
    assert not enum_correct("A1 (150) beats B2", None, item)  # names both options
    assert not enum_correct("B2", None, item)
    assert enum_correct(150.0, "°C", item)
    assert not enum_correct(125.0, "°C", item)


def test_tatqa_unit_free_scoring():
    from gqb.schemas import AnswerType, BenchItem, Tier
    from gqb.score import as_number, numeric_correct

    item = BenchItem(
        id="t:1",
        tier=Tier.DIRECT,
        question="What is the total net sales in 2019?",
        answer_type=AnswerType.NUMERIC,
        gold_answer=1791790.0,
        tolerance=0.01,
        meta={"unit_free": True, "scale_factors": [1e3]},
    )
    assert numeric_correct(1791.8, "million USD", item)
    assert numeric_correct(1791790, "$ thousands", item)
    assert numeric_correct(-1791790, None, item)  # sign-lenient
    assert not numeric_correct(1500.0, "million USD", item)
    assert as_number("2019") == 2019.0 and as_number("2018/2019") is None
