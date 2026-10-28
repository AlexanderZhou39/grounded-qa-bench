import json

import pytest
from pydantic import BaseModel

from gqb.llm import FileQueueClient, Message, ModelMapClient, PendingAnswer


class Out(BaseModel):
    verdict: str


def test_file_queue_roundtrip(tmp_path):
    q = FileQueueClient(tmp_path)
    msgs = [Message(role="user", content="Is the sky blue?")]
    with pytest.raises(PendingAnswer):
        q.complete(msgs, "claude-opus-5-5", response_model=Out)
    (req,) = list((tmp_path / "requests").glob("*.json"))
    assert json.loads(req.read_text())["schema"]["title"] == "Out"
    (tmp_path / "answers").mkdir()
    (tmp_path / "answers" / req.name).write_text(json.dumps({"text": '{"verdict": "yes"}'}))
    res = q.complete(msgs, "claude-opus-5-5", response_model=Out)
    assert res.parsed.verdict == "yes" and res.cost_usd > 0


def test_model_map_keeps_logical_id_and_prices(tmp_path):
    seen = []

    class Backend:
        def complete(self, messages, model, **kw):
            from gqb.llm import LLMResult

            seen.append(model)
            return LLMResult(text="ok", model=model, in_tok=1_000_000, out_tok=0)

    res = ModelMapClient(Backend(), {"claude-haiku-5-5": "gateway/small"}).complete(
        [Message(role="user", content="hi")], "claude-haiku-5-5"
    )
    assert seen == ["gateway/small"]
    assert res.model == "claude-haiku-5-5" and res.cost_usd == pytest.approx(0.10)
