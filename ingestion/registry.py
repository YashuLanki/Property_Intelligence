"""
ingestion/registry.py
---------------------
Tracks which files have already been ingested using SHA-256 file hashing.
Prevents duplicate documents from being stored in ChromaDB.
Now also records property, state, and category for each ingested file.
"""

import hashlib
import json
from datetime import datetime
from pathlib import Path

from config import REGISTRY_FILE


def load_registry() -> dict:
    """Load the ingestion registry from disk. Returns empty dict if none exists."""
    if REGISTRY_FILE.exists():
        with open(REGISTRY_FILE) as f:
            return json.load(f)
    return {}


def save_registry(registry: dict):
    """Save the ingestion registry to disk."""
    with open(REGISTRY_FILE, "w") as f:
        json.dump(registry, f, indent=2)


def get_file_hash(path: Path) -> str:
    """
    Compute a SHA-256 hash of a file's contents.
    Used to detect duplicate files regardless of filename.
    """
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def is_already_ingested(file_hash: str) -> bool:
    """Return True if this file hash exists in the registry."""
    registry = load_registry()
    return file_hash in registry


def record_ingestion(
    file_hash: str,
    filename: str,
    chunks: int,
    pages: int,
    ocr_used: bool,
    property_name: str = "unknown",
    state: str = "unknown",
    category: str = "unknown",
):
    """Add a successfully ingested file to the registry."""
    registry = load_registry()
    registry[file_hash] = {
        "filename":     filename,
        "ingested_at":  datetime.now().isoformat(),
        "chunks":       chunks,
        "pages":        pages,
        "ocr_used":     ocr_used,
        "property":     property_name,
        "state":        state,
        "category":     category,
    }
    save_registry(registry)
