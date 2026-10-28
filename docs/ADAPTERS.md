# LLM backends

Stages call one interface, `LLMClient.complete(messages, model, *, system, response_model,
max_tokens, effort) -> LLMResult` (`src/gqb/llm.py`). Configs and reports use **logical model ids**
(`claude-haiku-5-5`, `claude-sonnet-5-5`, `claude-opus-5-5`). The backend is chosen by environment,
so the same configs and cache layout work with any provider or gateway.

| `GQB_LLM_CLIENT` | Backend |
|---|---|
| unset | `RoutingClient`: Anthropic SDK for `claude-*` (`ANTHROPIC_API_KEY`), OpenAI-compatible otherwise (`OPENAI_BASE_URL`, `OPENAI_API_KEY`) |
| `package.module:factory` | Any zero-arg callable that returns an object with the `complete` signature above |
| `queue` | `FileQueueClient`: requests are written to files and answered out of band (see below) |

`GQB_MODEL_MAP` is a JSON object mapping logical ids to backend ids, for example
`{"claude-haiku-5-5": "my-gateway/haiku"}`. `ModelMapClient` applies it and keeps the logical id on the
result. When the backend reports no cost, it fills `cost_usd` at public list prices (`PRICES`), so
cost columns stay comparable across backends.

Every backend sits behind the disk cache (`.cache/llm/`, keyed by request hash plus repeat salt).
Reruns are free, and `bench spend` sums what the cache holds.

## Writing an adapter

```python
# mypkg/gateway.py
from gqb.llm import LLMResult, extract_json, Message


class Gateway:
    def complete(
        self,
        messages: list[Message],
        model: str,
        *,
        system=None,
        response_model=None,
        max_tokens=4096,
        effort=None,
    ) -> LLMResult:
        text, in_tok, out_tok = call_your_service(system, messages, model, max_tokens)
        parsed = response_model.model_validate(extract_json(text)) if response_model else None
        return LLMResult(text=text, parsed=parsed, model=model, in_tok=in_tok, out_tok=out_tok)


def make() -> Gateway:
    return Gateway()
```

```bash
GQB_LLM_CLIENT=mypkg.gateway:make GQB_MODEL_MAP='{"claude-haiku-5-5":"haiku-latest"}' \
  uv run bench run configs/final_datasheet.yaml
```

Structured output: when `response_model` is set, the stage expects JSON matching
`response_model.model_json_schema()`. The adapter can either use the service's native structured
output, or append the schema to the prompt and parse with `extract_json`.

## File queue: answering requests with a coding agent or by hand

```bash
GQB_LLM_CLIENT=queue GQB_QUEUE_DIR=.queue uv run bench facts --generator claude-haiku-5-5 --n 40
# -> .queue/requests/{key}.json  (system, messages, model, schema, max_tokens)
# answer each as .queue/answers/{key}.json = {"text": "..."}   (JSON object text if schema is set)
# rerun the same command; answered requests are consumed, new ones are queued
```

Records whose pipeline is still waiting on an answer are not written, so a run resumes cleanly.
Token counts are estimated at chars/4 so that cost columns still show public-rate equivalents.

Use the queue for **judging** roles (FACTS judge, free-text scoring), where the answerer should be a
strong model and nobody needs per-call latency. Do not use it for the pipeline stages under test:
latency becomes meaningless, and the answerer's model is not the one named in the combo id.
