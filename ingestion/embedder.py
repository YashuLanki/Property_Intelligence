"""
ingestion/embedder.py
---------------------
Handles all ChromaDB interactions for the Vaulterup ingestion pipeline.

Every chunk is stored with full metadata tags:
  - filename, ingested_at, page_count, ocr_used
  - property, state, category  ← new property-aware tags

This allows searching across all documents OR filtering by property/state.

Examples:
  query_documents("flood zone")                          # search everything
  query_documents("flood zone", state="arizona")         # Arizona only
  query_documents("easements", property="Magic Ranch 50") # one property
"""

import hashlib
import logging

import numpy as np
import chromadb
from chromadb import EmbeddingFunction, Documents, Embeddings

from config import CHROMA_DIR, CHROMA_COLLECTION_NAME, EMBEDDING_DIM

log = logging.getLogger("vaulter.embedder")


# ─── Embedding Function ───────────────────────────────────────────────────────

class LocalHashEmbedding(EmbeddingFunction):
    """
    Deterministic pseudo-embedding based on word position hashing.
    No model downloads required — safe for offline environments.

    Production upgrade path:
        from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
        ef = SentenceTransformerEmbeddingFunction(model_name="all-MiniLM-L6-v2")
    """

    def __init__(self):
        pass

    def __call__(self, input: Documents) -> Embeddings:
        result = []
        for text in input:
            words = text.lower().split()
            vec = np.zeros(EMBEDDING_DIM)
            for i, word in enumerate(words[:500]):
                h = int(hashlib.md5(word.encode()).hexdigest(), 16)
                idx = h % EMBEDDING_DIM
                vec[idx] += 1.0 / (i + 1)
            norm = np.linalg.norm(vec)
            if norm > 0:
                vec = vec / norm
            result.append(vec.tolist())
        return result


# ─── ChromaDB Client ──────────────────────────────────────────────────────────

def get_collection():
    """Initialize and return the ChromaDB collection."""
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    collection = client.get_or_create_collection(
        name=CHROMA_COLLECTION_NAME,
        embedding_function=LocalHashEmbedding(),
        metadata={"hnsw:space": "cosine"},
    )
    return collection


# ─── Storage ──────────────────────────────────────────────────────────────────

def store_chunks(chunks: list[str], metadata: dict, doc_hash: str):
    """
    Store text chunks in ChromaDB with full metadata tags including
    property, state, and category for property-aware search.
    """
    if not chunks:
        log.warning(f"No chunks to store for {metadata['filename']}")
        return

    collection = get_collection()

    ids = [f"{doc_hash}_{i}" for i in range(len(chunks))]
    metadatas = [
        {
            "filename":     metadata["filename"],
            "ingested_at":  metadata["ingested_at"],
            "page_count":   str(metadata["page_count"]),
            "has_tables":   str(metadata["has_tables"]),
            "ocr_used":     str(metadata["ocr_used"]),
            "property":     metadata.get("property", "unknown"),
            "state":        metadata.get("state", "unknown"),
            "category":     metadata.get("category", "unknown"),
            "chunk_index":  str(i),
            "total_chunks": str(len(chunks)),
            "doc_hash":     doc_hash,
        }
        for i in range(len(chunks))
    ]

    collection.add(documents=chunks, metadatas=metadatas, ids=ids)
    log.info(f"  Stored {len(chunks)} chunks in ChromaDB")


# ─── Retrieval ────────────────────────────────────────────────────────────────

def query_documents(
    question: str,
    n_results: int = 5,
    state: str = None,
    property_name: str = None,
) -> list[dict]:
    """
    Search ChromaDB for chunks relevant to a question.

    Optional filters:
      state         — only search documents from this state
      property_name — only search documents from this property

    Examples:
      query_documents("flood zone")
      query_documents("easements", state="arizona")
      query_documents("legal description", property_name="Magic Ranch 50")
    """
    collection = get_collection()
    count = collection.count()

    if count == 0:
        return []

    # Build optional where filter
    where = None
    if state and property_name:
        where = {"$and": [{"state": state}, {"property": property_name}]}
    elif state:
        where = {"state": state}
    elif property_name:
        where = {"property": property_name}

    query_params = {
        "query_texts": [question],
        "n_results": min(n_results, count),
    }
    if where:
        query_params["where"] = where

    results = collection.query(**query_params)

    output = []
    if results and results["documents"]:
        for i, doc in enumerate(results["documents"][0]):
            meta = results["metadatas"][0][i]
            dist = results["distances"][0][i] if results.get("distances") else None
            output.append({
                "text":      doc,
                "filename":  meta.get("filename"),
                "property":  meta.get("property"),
                "state":     meta.get("state"),
                "category":  meta.get("category"),
                "chunk":     meta.get("chunk_index"),
                "ocr":       meta.get("ocr_used"),
                "score":     round(1 - dist, 4) if dist is not None else None,
            })
    return output


def get_stats() -> dict:
    """Return a summary of what is currently stored in ChromaDB."""
    collection = get_collection()
    return {"total_chunks": collection.count()}
