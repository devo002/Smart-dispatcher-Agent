"""Text chunker using LlamaIndex SentenceSplitter.

Replaces the original word-count sliding window with sentence-aware chunking so
chunk boundaries never cut mid-sentence, preserving semantic coherence for embeddings.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.schema import Document
from pypdf import PdfReader


@dataclass
class Chunk:
    text: str
    source: str
    page: int | None
    chunk_index: int
    error_code: str | None = field(default=None)
    manufacturer: str | None = field(default=None)


_splitter = SentenceSplitter(chunk_size=512, chunk_overlap=64)

_KNOWN_MANUFACTURERS = ("Huawei", "SMA", "Fronius", "Goodwe", "Vaillant", "Viessmann")


def _heading_meta(heading_line: str) -> tuple[str | None, str | None]:
    """Extract error_code and manufacturer from a ## section heading line."""
    # "Error/Warning/State NNN" covers most entries
    m = re.search(r'\b(?:Error|Warning|State)\s+([\w.]+)', heading_line, re.IGNORECASE)
    if m:
        error_code: str | None = m.group(1).lower()
    else:
        # Standalone code like "F.22" or "A9" that appears before a "(" e.g. "— F.22 (Low Pressure)"
        m = re.search(r'—\s+([A-Za-z]\S*)\s+\(', heading_line)
        error_code = m.group(1).lower() if m and re.search(r'\d', m.group(1)) else None

    manufacturer: str | None = None
    lower = heading_line.lower()
    for brand in _KNOWN_MANUFACTURERS:
        if brand.lower() in lower:
            manufacturer = brand.lower()
            break

    return error_code, manufacturer


def chunk_pdf(path: Path) -> Iterable[Chunk]:
    reader = PdfReader(str(path))
    chunk_idx = 0
    for page_num, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        if not text.strip():
            continue
        for node in _splitter.get_nodes_from_documents([Document(text=text)]):
            yield Chunk(
                text=node.get_content(),
                source=path.name,
                page=page_num,
                chunk_index=chunk_idx,
            )
            chunk_idx += 1


def chunk_markdown(path: Path) -> Iterable[Chunk]:
    """Split on ## headings and --- separators first to preserve section structure,
    then apply SentenceSplitter within each section."""
    raw = path.read_text(encoding="utf-8")

    top_sections = [s.strip() for s in raw.split("\n---\n") if s.strip()]
    sections: list[str] = []
    for block in top_sections:
        parts = re.split(r"(?=\n## )", block)
        sections.extend(p.strip() for p in parts if p.strip())

    chunk_idx = 0
    for section in sections:
        # Skip preamble sections that don't start with a ## heading
        if not section.startswith("## ") and not re.search(r"\n## ", section):
            continue
        heading_line = section.split("\n")[0]
        error_code, manufacturer = _heading_meta(heading_line)
        for node in _splitter.get_nodes_from_documents([Document(text=section)]):
            yield Chunk(
                text=node.get_content(),
                source=path.name,
                page=None,
                chunk_index=chunk_idx,
                error_code=error_code,
                manufacturer=manufacturer,
            )
            chunk_idx += 1
