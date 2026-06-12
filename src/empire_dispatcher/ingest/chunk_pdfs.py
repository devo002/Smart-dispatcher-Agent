"""Text chunker using LlamaIndex SentenceSplitter.

Replaces the original word-count sliding window with sentence-aware chunking so
chunk boundaries never cut mid-sentence, preserving semantic coherence for embeddings.
"""

from __future__ import annotations

from dataclasses import dataclass
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


_splitter = SentenceSplitter(chunk_size=512, chunk_overlap=64)


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
    import re

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
        for node in _splitter.get_nodes_from_documents([Document(text=section)]):
            yield Chunk(
                text=node.get_content(),
                source=path.name,
                page=None,
                chunk_index=chunk_idx,
            )
            chunk_idx += 1
