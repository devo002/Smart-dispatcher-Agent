"""Build the Chroma RAG index from PDF manuals + the curated known_issues.md.

Run once after dropping PDFs into data/manuals/, or whenever known_issues.md changes:

    python -m src.enpal_dispatcher.ingest.build_index
"""

from __future__ import annotations

from pathlib import Path

import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
from rich.console import Console

from ..config import settings
from .chunk_pdfs import Chunk, chunk_markdown, chunk_pdf

COLLECTION_NAME = "enpal_kb"
console = Console()


def _gather_chunks() -> list[Chunk]:
    chunks: list[Chunk] = []
    if settings.known_issues_md.exists():
        chunks.extend(chunk_markdown(settings.known_issues_md))
    for pdf in sorted(settings.manuals_dir.glob("*.pdf")):
        chunks.extend(chunk_pdf(pdf))
    return chunks


def build() -> int:
    settings.chroma_persist_dir.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(settings.chroma_persist_dir))
    embed_fn = SentenceTransformerEmbeddingFunction(model_name=settings.embedding_model)

    # Recreate collection so reruns are idempotent.
    try:
        client.delete_collection(COLLECTION_NAME)
    except Exception:
        pass
    coll = client.create_collection(name=COLLECTION_NAME, embedding_function=embed_fn)

    chunks = _gather_chunks()
    if not chunks:
        console.print("[yellow]No source documents found. Add PDFs to data/manuals/.[/]")
        return 0

    coll.add(
        ids=[f"{c.source}:{c.page}:{c.chunk_index}" for c in chunks],
        documents=[c.text for c in chunks],
        metadatas=[
            {"source": c.source, "page": c.page if c.page is not None else -1}
            for c in chunks
        ],
    )

    console.print(
        f"[green]Indexed {len(chunks)} chunks from {len({c.source for c in chunks})} source(s) "
        f"into {settings.chroma_persist_dir}[/]"
    )
    return len(chunks)


if __name__ == "__main__":
    build()
