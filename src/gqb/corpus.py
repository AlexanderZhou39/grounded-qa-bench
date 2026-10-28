"""Corpus: manifest, PDF -> per-page text, and chunked passages for retrieval."""

from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

import yaml
from pydantic import BaseModel

from gqb.schemas import Passage

DATA = Path("data")
PDF_DIR = DATA / "pdfs"
CORPUS_DIR = DATA / "corpus"
MANIFEST = DATA / "manifest.jsonl"

CHUNK_CHARS = 1800


class ManifestEntry(BaseModel):
    doc_id: str
    filename: str  # original filename in the source folder
    sha256: str
    vendor: str
    mpn: str
    family: str
    pages: int
    url: str | None = None


def read_manifest(path: Path = MANIFEST) -> list[ManifestEntry]:
    if not path.exists():
        return []
    return [
        ManifestEntry.model_validate_json(line)
        for line in path.read_text().splitlines()
        if line.strip()
    ]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def build_manifest(
    allowlist: Path, source_dir: Path, pdf_dir: Path = PDF_DIR, manifest: Path = MANIFEST
) -> list[ManifestEntry]:
    """Copy allowlisted PDFs to ``pdf_dir/{doc_id}.pdf`` and write the manifest."""
    import pypdfium2 as pdfium

    spec = yaml.safe_load(allowlist.read_text())
    pdf_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    for d in spec["docs"]:
        src = source_dir / d["file"]
        dst = pdf_dir / f"{d['doc_id']}.pdf"
        shutil.copyfile(src, dst)
        pages = len(pdfium.PdfDocument(dst))
        entries.append(
            ManifestEntry(
                doc_id=d["doc_id"],
                filename=d["file"],
                sha256=sha256_file(dst),
                vendor=d["vendor"],
                mpn=str(d["mpn"]),
                family=d["family"],
                pages=pages,
                url=d.get("url"),
            )
        )
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text("".join(e.model_dump_json() + "\n" for e in entries))
    return entries


def zip_pdfs(entries: list[ManifestEntry], out: Path, pdf_dir: Path = PDF_DIR) -> Path:
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for e in entries:
            z.write(pdf_dir / f"{e.doc_id}.pdf", arcname=f"{e.doc_id}.pdf")
    return out


def build_corpus(
    entries: list[ManifestEntry], pdf_dir: Path = PDF_DIR, corpus_dir: Path = CORPUS_DIR
) -> int:
    """Extract text per page to ``corpus_dir/{doc_id}/{page:04d}.txt`` (1-indexed).
    Uses pypdfium2 (Apache-2.0 / BSD-3-Clause; PDFium itself is BSD-style)."""
    import pypdfium2 as pdfium

    n = 0
    for e in entries:
        out = corpus_dir / e.doc_id
        out.mkdir(parents=True, exist_ok=True)
        doc = pdfium.PdfDocument(pdf_dir / f"{e.doc_id}.pdf")
        for i, page in enumerate(doc, 1):
            (out / f"{i:04d}.txt").write_text(page.get_textpage().get_text_bounded())
            n += 1
    return n


def render_page_png(doc_id: str, page: int, dpi: int = 110, pdf_dir: Path = PDF_DIR) -> bytes:
    import io

    import pypdfium2 as pdfium

    image = pdfium.PdfDocument(pdf_dir / f"{doc_id}.pdf")[page - 1].render(scale=dpi / 72).to_pil()
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def chunk_page(text: str, max_chars: int = CHUNK_CHARS) -> list[str]:
    """Split on line boundaries into chunks of at most ~max_chars."""
    chunks, cur, size = [], [], 0
    for line in text.splitlines():
        if size + len(line) > max_chars and cur:
            chunks.append("\n".join(cur))
            cur, size = [], 0
        cur.append(line)
        size += len(line) + 1
    if cur and "".join(cur).strip():
        chunks.append("\n".join(cur))
    return chunks


@dataclass
class Corpus:
    """Read-only view over ``data/corpus``. Pages are 1-indexed."""

    root: Path = CORPUS_DIR
    _pages: dict[str, list[str]] = field(default_factory=dict, repr=False)

    def doc_ids(self) -> list[str]:
        return sorted(p.name for p in self.root.iterdir() if p.is_dir())

    def pages(self, doc_id: str) -> list[str]:
        if doc_id not in self._pages:
            files = sorted((self.root / doc_id).glob("*.txt"))
            self._pages[doc_id] = [f.read_text() for f in files]
        return self._pages[doc_id]

    def page(self, doc_id: str, page: int) -> str:
        pages = self.pages(doc_id)
        return pages[page - 1] if 1 <= page <= len(pages) else ""

    @cached_property
    def fingerprint(self) -> str:
        """Changes whenever any page text changes (e.g. after re-extraction)."""
        h = hashlib.sha256()
        for f in sorted(self.root.glob("*/*.txt")):
            h.update(f"{f.parent.name}/{f.name}:{f.stat().st_size}".encode())
        return h.hexdigest()[:16]

    def full_text(self, doc_id: str) -> str:
        return "\n".join(self.pages(doc_id))

    def passages(self, doc_id: str) -> list[Passage]:
        out = []
        for pno, text in enumerate(self.pages(doc_id), 1):
            for ci, chunk in enumerate(chunk_page(text)):
                out.append(Passage(id=f"{doc_id}:{pno}:{ci}", doc_id=doc_id, page=pno, text=chunk))
        return out

    @cached_property
    def manifest(self) -> dict[str, ManifestEntry]:
        local = self.root.parent / "manifest.jsonl"
        return {e.doc_id: e for e in read_manifest(local if local.exists() else MANIFEST)}


def write_jsonl(path: Path, rows: list[BaseModel] | list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for r in rows:
            f.write((r.model_dump_json() if isinstance(r, BaseModel) else json.dumps(r)) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
