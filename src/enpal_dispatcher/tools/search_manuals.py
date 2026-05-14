"""RAG search over the manuals + known_issues corpus."""

from __future__ import annotations

from functools import lru_cache

import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
from pydantic import BaseModel, Field

from ..config import settings
from ..ingest.build_index import COLLECTION_NAME


class SearchManualsInput(BaseModel):
    query: str = Field(..., description="Natural-language description of the symptom or error code.")
    top_k: int = Field(4, ge=1, le=10)


class ManualHit(BaseModel):
    text: str
    source: str
    page: int | None
    score: float


class SearchManualsOutput(BaseModel):
    query: str
    hits: list[ManualHit]


@lru_cache(maxsize=1)
def _collection():
    client = chromadb.PersistentClient(path=str(settings.chroma_persist_dir))
    embed_fn = SentenceTransformerEmbeddingFunction(model_name=settings.embedding_model)
    return client.get_collection(name=COLLECTION_NAME, embedding_function=embed_fn)


def search_manuals(payload: SearchManualsInput) -> SearchManualsOutput:
    coll = _collection()
    res = coll.query(query_texts=[payload.query], n_results=payload.top_k)
    hits: list[ManualHit] = []
    docs = (res.get("documents") or [[]])[0]
    metas = (res.get("metadatas") or [[]])[0]
    dists = (res.get("distances") or [[]])[0]
    for doc, meta, dist in zip(docs, metas, dists):
        page = meta.get("page")
        hits.append(
            ManualHit(
                text=doc,
                source=str(meta.get("source", "unknown")),
                page=None if page in (None, -1) else int(page),
                score=float(1.0 / (1.0 + dist)) if dist is not None else 0.0,
            )
        )
    return SearchManualsOutput(query=payload.query, hits=hits)
