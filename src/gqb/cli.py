"""`bench` CLI.

Dataset build (needs the PDFs): build-manifest -> build-corpus -> extract-facts -> make-items
-> qc -> split. Experiments: run -> report. See AGENTS.md.
"""

from __future__ import annotations

import collections
import hashlib
import json
import random
from pathlib import Path

import typer

from gqb.schemas import AnswerType, BenchItem

app = typer.Typer(no_args_is_help=True, add_completion=False)

DATA = Path("data")
LOCAL = DATA / "local"
ITEMS = DATA / "items"
KEEP = {"agree", "absent_confirmed"}


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _llm():
    from gqb.llm import CachedClient, load_client

    return CachedClient(load_client())


# ---------------------------------------------------------------------------
# corpus
# ---------------------------------------------------------------------------


@app.command("build-manifest")
def build_manifest_cmd(
    source_dir: Path,
    allowlist: Path = Path("tools/export/corpus_allowlist.yaml"),
    zip_out: Path | None = None,
) -> None:
    """Copy allowlisted PDFs from SOURCE_DIR into data/pdfs and write data/manifest.jsonl."""
    from gqb.corpus import build_manifest, zip_pdfs

    entries = build_manifest(allowlist, source_dir)
    typer.echo(f"{len(entries)} docs, {sum(e.pages for e in entries)} pages")
    if zip_out:
        zip_pdfs(entries, zip_out)
        typer.echo(f"wrote {zip_out}")


@app.command("verify-corpus")
def verify_corpus(manifest: Path = DATA / "manifest.jsonl", pdf_dir: Path = DATA / "pdfs") -> None:
    """Check every manifest entry has a PDF in pdf_dir with a matching sha256."""
    entries = _read_jsonl(manifest) if manifest.exists() else []
    if not entries:
        typer.echo(f"{manifest} is empty or missing.")
        raise typer.Exit(1)
    bad = 0
    for e in entries:
        pdf = pdf_dir / f"{e['doc_id']}.pdf"
        if not pdf.exists():
            typer.echo(f"MISSING  {pdf}")
            bad += 1
        elif hashlib.sha256(pdf.read_bytes()).hexdigest() != e["sha256"]:
            typer.echo(f"MISMATCH {pdf}")
            bad += 1
    typer.echo(f"{len(entries) - bad}/{len(entries)} ok")
    raise typer.Exit(1 if bad else 0)


@app.command("build-corpus")
def build_corpus_cmd() -> None:
    """Extract per-page text for every manifest doc into data/corpus/."""
    from gqb.corpus import build_corpus, read_manifest

    typer.echo(f"{build_corpus(read_manifest())} pages written")


# ---------------------------------------------------------------------------
# dataset
# ---------------------------------------------------------------------------


@app.command("extract-facts")
def extract_facts(
    model: str = "claude-sonnet-5-5",
    max_pages: int = 8,
    docs: list[str] = typer.Option(None, help="Subset of doc_ids (default: all)"),
) -> None:
    """LLM proposes spec rows per page; deterministic checks keep only verbatim-grounded rows.
    Writes data/local/facts.jsonl + extraction_report.json."""
    from gqb.corpus import Corpus, write_jsonl
    from gqb.dataset.extract import dedupe, extract_doc

    corpus, llm = Corpus(), _llm()
    facts, reports = [], []
    for doc_id in docs or corpus.doc_ids():
        f, r = extract_doc(llm, corpus, doc_id, model, max_pages=max_pages)
        facts += f
        reports.append(r.model_dump())
        typer.echo(f"{doc_id}: kept {r.kept}/{r.proposed}")
    facts = dedupe(facts)
    write_jsonl(LOCAL / "facts.jsonl", facts)
    (LOCAL / "extraction_report.json").write_text(json.dumps(reports, indent=1))
    typer.echo(f"{len(facts)} facts")


@app.command("make-items")
def make_items(
    seed: int = 13,
    near_miss_per_doc: int = 2,
    near_miss_cond_per_doc: int = 0,
    corpus_dir: Path = typer.Option(None, help="Corpus the facts were extracted from"),
) -> None:
    """Deterministically generate tiered items from facts -> data/local/items_raw.jsonl."""
    from gqb.corpus import Corpus, write_jsonl
    from gqb.dataset.extract import Fact
    from gqb.dataset.generate import Generator

    corpus = Corpus(corpus_dir) if corpus_dir else Corpus()
    facts = [Fact.model_validate(r) for r in _read_jsonl(LOCAL / "facts.jsonl")]
    items = Generator(facts, corpus, corpus.manifest, seed=seed).generate(
        near_miss_per_doc=near_miss_per_doc, near_miss_cond_per_doc=near_miss_cond_per_doc
    )
    write_jsonl(LOCAL / "items_raw.jsonl", items)
    typer.echo(dict(collections.Counter(f"T{int(i.tier)}" for i in items)))


@app.command("qc")
def qc(
    para_model: str = "claude-haiku-5-5",
    check_model: str = "claude-opus-5-5",
    workers: int = 6,
    corpus_dir: Path = typer.Option(None, help="Corpus the items were generated from"),
) -> None:
    """Paraphrase questions, then independently cross-check every item against its pages.
    -> data/local/items_qc.jsonl"""
    from gqb.corpus import Corpus, write_jsonl
    from gqb.dataset.qc import crosscheck, paraphrase
    from gqb.llm import StageMeter

    corpus, llm = (Corpus(corpus_dir) if corpus_dir else Corpus()), _llm()
    items = [BenchItem.model_validate(r) for r in _read_jsonl(LOCAL / "items_raw.jsonl")]
    m1 = StageMeter(llm, "qc", "paraphrase")
    with m1:
        n = paraphrase(m1, items, para_model, workers)
    typer.echo(f"paraphrased {n}/{len(items)}  ({m1.cached_calls} cached)")
    m2 = StageMeter(llm, "qc", "crosscheck")
    with m2:
        counts = crosscheck(m2, corpus, items, check_model, workers)
    typer.echo(f"crosscheck {counts}  ({m2.cached_calls}/{m2.trace.calls} cached)")
    write_jsonl(LOCAL / "items_qc.jsonl", items)


@app.command("split")
def split(dev_size: int = 100, seed: int = 7) -> None:
    """items_qc -> data/items/{dev,test,disputed}.jsonl (+ STATS.md). Items the cross-check
    disputed go to disputed.jsonl, never to dev/test. dev is stratified by tier."""
    from gqb.corpus import write_jsonl

    items = [BenchItem.model_validate(r) for r in _read_jsonl(LOCAL / "items_qc.jsonl")]
    kept = [i for i in items if i.meta.get("crosscheck") in KEEP]
    disputed = [i for i in items if i.meta.get("crosscheck") not in KEEP]
    # stable: items already in dev/test keep their split; only new items are assigned
    old_dev = (
        {r["id"] for r in _read_jsonl(ITEMS / "dev.jsonl")}
        if (ITEMS / "dev.jsonl").exists()
        else set()
    )
    old_test = (
        {r["id"] for r in _read_jsonl(ITEMS / "test.jsonl")}
        if (ITEMS / "test.jsonl").exists()
        else set()
    )
    by_tier: dict[int, list[BenchItem]] = collections.defaultdict(list)
    for i in kept:
        by_tier[int(i.tier)].append(i)
    dev, test = [], []
    for tier, its in sorted(by_tier.items()):
        rng = random.Random(seed * 100 + tier)
        k = round(len(its) * dev_size / len(kept))
        dev += [i for i in its if i.id in old_dev]
        test += [i for i in its if i.id in old_test]
        new = [i for i in its if i.id not in old_dev | old_test]
        rng.shuffle(new)
        n_dev = max(0, k - sum(i.id in old_dev for i in its))
        dev += new[:n_dev]
        test += new[n_dev:]
    order = {i.id: n for n, i in enumerate(items)}
    for part in (dev, test):
        part.sort(key=lambda i: order[i.id])
    write_jsonl(ITEMS / "dev.jsonl", dev)
    write_jsonl(ITEMS / "test.jsonl", test)
    write_jsonl(ITEMS / "disputed.jsonl", disputed)
    _write_stats(items, dev, test, disputed)
    typer.echo(f"dev {len(dev)}  test {len(test)}  disputed {len(disputed)}")


def _write_stats(items, dev, test, disputed) -> None:
    def tiers(xs):
        c = collections.Counter(int(i.tier) for i in xs)
        return " | ".join(str(c.get(t, 0)) for t in (1, 2, 3, 4, 5, 6))

    lines = [
        "# Item statistics",
        "",
        "Generated by `bench split`. Do not edit by hand.",
        "",
        "| split | total | T1 direct | T2 conditional | T3 derived | T4 cross-doc "
        "| T5 unanswerable | T6 distractor |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for name, xs in [("dev", dev), ("test", test), ("disputed", disputed), ("all", items)]:
        lines.append(f"| {name} | {len(xs)} | {tiers(xs)} |")
    cc = collections.Counter(i.meta.get("crosscheck") for i in items)
    para = sum("template_question" in i.meta for i in items)
    docs = collections.Counter(i.doc_ids[0] for i in dev + test if int(i.tier) not in (4, 6))
    lines += [
        "",
        "## Cross-check outcomes (independent model, gold pages only)",
        "",
        *(f"- {k}: {v}" for k, v in sorted(cc.items())),
        "",
        f"Paraphrased questions: {para}/{len(items)} (the rest keep the template question).",
        "",
        "## Items per document (dev + test, excluding cross-doc and distractors)",
        "",
        *(f"- {d}: {n}" for d, n in sorted(docs.items())),
    ]
    report = LOCAL / "extraction_report.json"
    if report.exists():
        reps = json.loads(report.read_text())
        lines += [
            "",
            "## Fact extraction",
            "",
            f"Pages sent to the extractor: {sum(len(r['pages']) for r in reps)}. "
            f"Proposed rows: {sum(r['proposed'] for r in reps)}, "
            f"kept after verbatim checks: {sum(r['kept'] for r in reps)}.",
        ]
    (ITEMS / "STATS.md").write_text("\n".join(lines) + "\n")


@app.command("validate-items")
def validate_items(path: Path, check_quotes: bool = True) -> None:
    """Schema-validate a BenchItem JSONL file, check invariants and (if the corpus is built)
    that every gold quote is found on its page."""
    from gqb.corpus import CORPUS_DIR, Corpus
    from gqb.textmatch import find_span, find_tokens

    corpus = Corpus() if check_quotes and CORPUS_DIR.exists() else None
    errors = 0
    seen: set[str] = set()
    for n, row in enumerate(_read_jsonl(path), 1):
        try:
            item = BenchItem.model_validate(row)
        except Exception as exc:
            typer.echo(f"line {n}: {exc}")
            errors += 1
            continue
        if item.id in seen:
            typer.echo(f"line {n}: duplicate id {item.id}")
            errors += 1
        seen.add(item.id)
        if (item.answer_type == AnswerType.UNANSWERABLE) != (item.gold_answer is None):
            typer.echo(f"line {n}: gold_answer must be None iff unanswerable")
            errors += 1
        if item.answer_type != AnswerType.UNANSWERABLE and not item.gold_evidence:
            typer.echo(f"line {n}: answerable item without gold_evidence")
            errors += 1
        for ev in item.gold_evidence if corpus else []:
            text = corpus.page(ev.doc_id, ev.page)
            if not text:
                typer.echo(f"line {n}: {ev.doc_id} page {ev.page} missing")
                errors += 1
            # order-free fallback: PDF extractors differ in how they serialize table rows
            elif ev.quote and not (
                find_span(ev.quote, text) or find_tokens(ev.quote, text, 4.0, 60)
            ):
                typer.echo(f"line {n}: quote not found on {ev.doc_id} p{ev.page}")
                errors += 1
    typer.echo(f"{len(seen)} items, {errors} errors" + ("" if corpus else " (quotes unchecked)"))
    raise typer.Exit(1 if errors else 0)


@app.command("audit")
def audit(
    path: Path = ITEMS / "dev.jsonl",
    log: Path = LOCAL / "audit.jsonl",
    apply: bool = typer.Option(False, help="Write decisions back into PATH and exit"),
) -> None:
    """Human audit: shows question, gold answer and page text around the gold quote.
    a=accept, r=reject, s=skip, q=quit. Decisions append to LOG. --apply marks accepted items
    audited=true and moves rejected items to data/items/disputed.jsonl."""
    from gqb.corpus import Corpus
    from gqb.textmatch import find_span

    decisions = {r["id"]: r for r in _read_jsonl(log)} if log.exists() else {}
    items = [BenchItem.model_validate(r) for r in _read_jsonl(path)]
    if apply:
        keep, rejected = [], []
        for i in items:
            d = decisions.get(i.id, {}).get("decision")
            if d == "reject":
                i.meta["audit_note"] = decisions[i.id].get("note", "")
                rejected.append(i)
                continue
            i.audited = i.audited or d == "accept"
            keep.append(i)
        path.write_text("".join(i.model_dump_json() + "\n" for i in keep))
        with (ITEMS / "disputed.jsonl").open("a") as f:
            for i in rejected:
                f.write(i.model_dump_json() + "\n")
        typer.echo(f"{sum(i.audited for i in keep)} audited, {len(rejected)} rejected")
        return
    corpus = Corpus()
    log.parent.mkdir(parents=True, exist_ok=True)
    todo = [i for i in items if i.id not in decisions]
    typer.echo(f"{len(todo)} items to audit ({len(decisions)} done)")
    for n, item in enumerate(todo, 1):
        typer.echo("\n" + "=" * 100)
        typer.echo(f"[{n}/{len(todo)}] {item.id}  tier {int(item.tier)}")
        typer.secho(item.question, bold=True)
        typer.echo(f"gold: {item.gold_answer} {item.unit or ''}  {item.meta.get('formula', '')}")
        for ev in item.gold_evidence:
            text = corpus.page(ev.doc_id, ev.page)
            m = find_span(ev.quote, text) if ev.quote else None
            lo, hi = (m.start, m.end) if m else (0, 0)
            typer.echo(f"--- {ev.doc_id} p{ev.page}")
            typer.echo(text[max(0, lo - 600) : lo], nl=False)
            typer.secho(text[lo:hi], fg="yellow", nl=False)
            typer.echo(text[hi : hi + 600])
        if item.answer_type == AnswerType.UNANSWERABLE:
            typer.echo(f"cross-check: {item.meta.get('crosscheck_answer')}")
        choice = typer.prompt("[a]ccept / [r]eject / [s]kip / [q]uit", default="a").lower()[:1]
        if choice == "q":
            break
        if choice == "s":
            continue
        note = typer.prompt("note", default="") if choice == "r" else ""
        rec = {"id": item.id, "decision": "accept" if choice == "a" else "reject", "note": note}
        with log.open("a") as f:
            f.write(json.dumps(rec) + "\n")


# ---------------------------------------------------------------------------
# experiments
# ---------------------------------------------------------------------------


@app.command("run")
def run(
    config: Path,
    only: list[str] = typer.Option(None, help="Run only combos whose id contains this"),
    limit: int | None = None,
) -> None:
    """Run every combo in CONFIG over its item split. Resumable; LLM calls are disk-cached."""
    from gqb.corpus import Corpus
    from gqb.llm import load_client
    from gqb.runner import load_config, load_items, run_combo

    cfg = load_config(config)
    if limit:
        cfg.run.limit = limit
    items = load_items(cfg.run)
    corpus = Corpus(Path(cfg.run.corpus))
    for spec in cfg.combos:
        if only and not any(o in spec.combo_id for o in only):
            continue
        out = run_combo(spec, items, cfg.run, load_client(), corpus)
        typer.echo(f"wrote {out}")


@app.command("fetch-external")
def fetch_external(
    name: str = typer.Argument(..., help="tatqa | ragtruth | facts | hover | lifecycle"),
    n: int = typer.Option(150, help="tatqa: items (half arithmetic, half span)"),
    per_cell: int = typer.Option(25, help="ragtruth/hover: pairs per cell"),
    split: str = typer.Option("dev", help="lifecycle: datasheet split to derive from"),
) -> None:
    """Download an external set and adapt it under data/external/{name}/ (not committed)."""
    if name == "tatqa":
        from gqb.external import tatqa

        items = tatqa.build(n=n)
        typer.echo(f"{len(items)} items -> data/external/tatqa/items.jsonl (+ corpus/)")
    elif name == "ragtruth":
        from gqb.external import ragtruth

        pairs = ragtruth.build(per_cell=per_cell)
        typer.echo(f"{len(pairs)} pairs -> data/external/ragtruth/pairs.jsonl")
    elif name == "facts":
        from gqb.external import facts

        ex = facts.build(n=n)
        typer.echo(f"{len(ex)} examples -> data/external/facts/examples.jsonl")
    elif name == "lifecycle":
        from gqb.external import lifecycle

        items = lifecycle.build(split)
        typer.echo(f"{len(items)} items -> data/external/lifecycle/items_{split}.jsonl")
    elif name == "hover":
        from gqb.external import hover

        pairs = hover.build(per_cell=per_cell)
        typer.echo(f"{len(pairs)} pairs -> data/external/hover/pairs.jsonl")
        op = hover.build_open()
        rec = sum(p.meta["recall"] for p in op) / len(op)
        typer.echo(
            f"{len(op)} open-retrieval pairs (gold-article recall {rec:.2f}) -> pairs_open.jsonl"
        )
    else:
        raise typer.BadParameter(f"unknown set {name!r}")


@app.command("vtrack")
def vtrack(
    pairs: Path = Path("data/external/ragtruth/pairs.jsonl"),
    verifier: list[str] = typer.Option(["llm_judge"], help="verify impl(s) from the registry"),
    model: list[str] = typer.Option(["claude-haiku-5-5"]),
    out: Path = Path("runs/vtrack"),
    limit: int | None = None,
) -> None:
    """Verifier track: score verify stages on (context, response, label) pairs.
    Writes {out}/{verifier}@{model}.jsonl and prints P/R/F1/BAcc per task."""
    from gqb.corpus import write_jsonl
    from gqb.llm import load_client
    from gqb.vtrack import calibrate, load_pairs, metrics, run_verifier

    ps = load_pairs(pairs)[:limit] if limit else load_pairs(pairs)
    out.mkdir(parents=True, exist_ok=True)
    summary_path = out / "summary.json"
    summary = json.loads(summary_path.read_text()) if summary_path.exists() else {}
    for v in verifier:
        for m in model:
            res = run_verifier(v, m, ps, load_client())
            write_jsonl(out / f"{v}@{m}.jsonl", res)
            summary[f"{v}@{m}"] = met = metrics(res)
            for task, r in met.items():
                typer.echo(
                    f"{v}@{m:18s} {task:9s} n={r['n']:<4} P={r['precision']:.3f} "
                    f"R={r['recall']:.3f} F1={r['f1']:.3f} BAcc={r['bacc']:.3f} "
                    f"err={r['error_rate']:.2f} ${r['cost_per_pair']:.5f}/pair"
                )
            if any(x.score is not None for x in res) and v in ("nli_small",):
                cal = calibrate(res)
                met["calibrated"] = cal
                typer.echo(f"{v}@{m} calibrated on half: {cal}")
    summary_path.write_text(json.dumps(summary, indent=1))


@app.command("facts")
def facts_cmd(
    generator: list[str] = typer.Option(
        ["claude-haiku-5-5", "claude-haiku-5-5+repair", "claude-sonnet-5-5"],
        help="{model} or {model}+repair (claim_decompose -> revise)",
    ),
    judge: str = "claude-opus-5-5",
    n: int = 40,
    out: Path = Path("runs/facts"),
) -> None:
    """FACTS Grounding track: long-form answers judged with FACTS's response-level prompt."""
    from gqb.corpus import write_jsonl
    from gqb.external import facts
    from gqb.llm import load_client

    path = facts.ROOT / "examples.jsonl"
    ex = facts.load() if path.exists() else facts.build(n=n)
    out.mkdir(parents=True, exist_ok=True)
    for g in generator:
        res = facts.run(g, judge, load_client(), ex[:n])
        write_jsonl(out / f"{g}.jsonl", res)
        judged = [r for r in res if r.accurate is not None]
        acc = sum(r.accurate for r in judged) / len(judged) if judged else 0.0
        gen = sum(r.gen_cost_usd for r in res) / len(res)
        typer.echo(
            f"{g:28s} grounded={acc:.3f} (n={len(judged)}/{len(res)})  gen ${gen:.5f}/ex  "
            f"judge={judge}"
        )


@app.command("report")
def report(
    run_dir: Path,
    items: list[Path] = typer.Option(None, help="Item files (default: data/items/*.jsonl)"),
) -> None:
    """Score every combo JSONL in RUN_DIR against data/items -> scores.csv, report.html,
    record_scores.jsonl (in RUN_DIR)."""
    from gqb.corpus import write_jsonl
    from gqb.report import write_csv, write_html
    from gqb.schemas import RunRecord
    from gqb.score import aggregate, score_record

    by_id: dict[str, BenchItem] = {}
    for p in items or sorted(ITEMS.glob("*.jsonl")):
        for r in _read_jsonl(p):
            by_id.setdefault(r["id"], BenchItem.model_validate(r))
    scores = []
    for f in sorted(run_dir.glob("*.jsonl")):
        if f.name == "record_scores.jsonl":
            continue
        for r in _read_jsonl(f):
            rec = RunRecord.model_validate(r)
            if rec.item_id in by_id:
                scores.append(score_record(rec, by_id[rec.item_id]))
    aggs = aggregate(scores)
    write_jsonl(run_dir / "record_scores.jsonl", scores)
    write_csv(aggs, run_dir / "scores.csv")
    write_html(aggs, run_dir / "report.html", title=f"Groundedness benchmark: {run_dir.name}")
    for a in aggs:
        if a.tier == "all":
            typer.echo(
                f"{a.accuracy:.3f} acc  ${a.cost_per_item:.5f}/item  "
                f"p50 {a.latency_p50_ms:.0f}ms  {a.combo_id}"
            )
    typer.echo(f"wrote {run_dir / 'report.html'}")


@app.command("spend")
def spend(cache_dir: Path = Path(".cache/llm")) -> None:
    """Real API spend so far: every cache file is exactly one paid call."""
    tot: collections.Counter[str] = collections.Counter()
    n: collections.Counter[str] = collections.Counter()
    for f in cache_dir.glob("*/*.json"):
        r = json.loads(f.read_text())
        r = r.get("result", r)
        tot[r.get("model", "?")] += r.get("cost_usd", 0.0)
        n[r.get("model", "?")] += 1
    for m in sorted(tot):
        typer.echo(f"{m:24s} {n[m]:6d} calls  ${tot[m]:.3f}")
    typer.echo(f"{'total':24s} {sum(n.values()):6d} calls  ${sum(tot.values()):.3f}")


if __name__ == "__main__":
    app()
