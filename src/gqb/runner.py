"""Run combos over items. Output: ``runs/{run_name}/{combo_slug}.jsonl`` of RunRecords.
Resumable: (item_id, repeat) pairs already present are skipped."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock

import yaml
from pydantic import BaseModel, Field

from gqb.corpus import Corpus, read_jsonl
from gqb.llm import CachedClient, LLMClient
from gqb.retrieve import BM25Retriever
from gqb.schemas import BenchItem, PipelineState, RunRecord
from gqb.stage import ComboSpec
from gqb.stages import StageContext, build_pipeline


class RunConfig(BaseModel):
    name: str = "default"
    items: str = "data/items/dev.jsonl"
    corpus: str = "data/corpus"  # external sets: data/external/{set}/corpus
    tiers: list[int] | None = None
    limit: int | None = None
    repeats: int = 1
    concurrency: int = 8
    top_k: int = 8
    scope: str = "item"
    default_model: str = "claude-haiku-5-5"
    effort: str | None = "low"


class SweepConfig(BaseModel):
    run: RunConfig = Field(default_factory=RunConfig)
    combos: list[ComboSpec]


def load_config(path: str | Path) -> SweepConfig:
    return SweepConfig.model_validate(yaml.safe_load(Path(path).read_text()))


def slug(combo_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.=-]+", "_", combo_id)[:180]


def load_items(cfg: RunConfig) -> list[BenchItem]:
    items = [BenchItem.model_validate(r) for r in read_jsonl(Path(cfg.items))]
    if cfg.tiers:
        items = [i for i in items if int(i.tier) in cfg.tiers]
    if not cfg.limit:
        return items
    # round-robin across tiers so small smoke runs still cover every tier
    by_tier: dict[int, list[BenchItem]] = {}
    for it in items:
        by_tier.setdefault(int(it.tier), []).append(it)
    out: list[BenchItem] = []
    while len(out) < min(cfg.limit, len(items)):
        for tier in sorted(by_tier):
            if by_tier[tier] and len(out) < cfg.limit:
                out.append(by_tier[tier].pop(0))
    return out


def run_combo(
    spec: ComboSpec,
    items: list[BenchItem],
    cfg: RunConfig,
    base_llm: LLMClient,
    corpus: Corpus,
    out_dir: Path = Path("runs"),
    cache_dir: str = ".cache/llm",
    progress: bool = True,
) -> Path:
    ctx = StageContext(
        corpus=corpus,
        retriever=BM25Retriever(corpus),
        top_k=cfg.top_k,
        scope=cfg.scope,
        default_model=cfg.default_model,
        effort=cfg.effort,
    )
    pipeline = build_pipeline(spec, ctx)
    out = out_dir / cfg.name / f"{slug(spec.combo_id)}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    done = {(r["item_id"], r["repeat"]) for r in read_jsonl(out)}
    clients = {
        r: CachedClient(base_llm, cache_dir, salt=f"r{r}" if r else "") for r in range(cfg.repeats)
    }
    todo = [(it, r) for r in range(cfg.repeats) for it in items if (it.id, r) not in done]
    lock = Lock()

    def one(item: BenchItem, repeat: int) -> RunRecord:
        state = pipeline.run(PipelineState.from_item(item), clients[repeat])
        return RunRecord(combo_id=spec.combo_id, item_id=item.id, repeat=repeat, state=state)

    with ThreadPoolExecutor(cfg.concurrency) as ex, out.open("a") as f:
        futures = [ex.submit(one, it, r) for it, r in todo]
        for n, fut in enumerate(as_completed(futures), 1):
            rec = fut.result()
            if any(t.error and "PendingAnswer" in t.error for t in rec.state.trace):
                continue  # out-of-band answer not written yet; rerun after answering
            with lock:
                f.write(rec.model_dump_json() + "\n")
                f.flush()
            if progress and (n % 25 == 0 or n == len(futures)):
                print(f"  {spec.combo_id}: {n}/{len(futures)}", flush=True)
    return out
