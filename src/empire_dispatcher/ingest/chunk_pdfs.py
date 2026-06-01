"""Light text chunker. Token-ish chunks via word splitting (good enough for embeddings).

We deliberately avoid pulling in tiktoken to keep deps light; sentence-transformers
embedders truncate at 256–512 wordpieces, so ~600 words / 80 overlap fits comfortably.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from pypdf import PdfReader


@dataclass
class Chunk:
    text: str
    source: str           # filename
    page: int | None      # 1-based page; None for non-PDF sources
    chunk_index: int


def _split_words(text: str, size: int, overlap: int) -> list[str]:
    words = text.split()
    if not words:
        return []
    out: list[str] = []
    i = 0
    while i < len(words):
        out.append(" ".join(words[i : i + size]))
        if i + size >= len(words):
            break
        i += size - overlap
    return out


def chunk_pdf(path: Path, chunk_size: int = 600, overlap: int = 80) -> Iterable[Chunk]:
    reader = PdfReader(str(path))
    for page_num, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        for idx, piece in enumerate(_split_words(text, chunk_size, overlap)):
            yield Chunk(
                text=piece,
                source=path.name,
                page=page_num,
                chunk_index=idx,
            )


def chunk_markdown(path: Path, chunk_size: int = 600, overlap: int = 80) -> Iterable[Chunk]:
    """Chunk a markdown file by splitting on ## headings and --- separators."""
    import re
    raw = path.read_text(encoding="utf-8")
    # Split on --- dividers first, then on ## section headings within each block
    top_sections = [s.strip() for s in raw.split("\n---\n") if s.strip()]
    sections: list[str] = []
    for block in top_sections:
        # Split on lines that start with ## (keep the heading with its body)
        parts = re.split(r"(?=\n## )", block)
        sections.extend(p.strip() for p in parts if p.strip())
    chunk_idx = 0
    for section in sections:
        for piece in _split_words(section, chunk_size, overlap):
            yield Chunk(text=piece, source=path.name, page=None, chunk_index=chunk_idx)
            chunk_idx += 1
