# cleanup_costar.py  — replace with this version
from ingestion.embedder import get_collection
import json

col = get_collection()
total = col.count()
print(f"Total chunks in DB: {total}")

# Get all chunks and find CoStar ones by source field
results = col.get(limit=min(total, 9999), include=["metadatas"])
ids_to_delete = []

for i, meta in enumerate(results["metadatas"]):
    source = meta.get("source", "")
    fname  = meta.get("filename", "")
    typ    = meta.get("type", "")
    if (
        "CostarExport" in source or
        "CostarExport" in fname or
        typ == "email_attachment_excel"
    ):
        ids_to_delete.append(results["ids"][i])

print(f"CoStar chunks to delete: {len(ids_to_delete)}")

if ids_to_delete:
    col.delete(ids=ids_to_delete)
    print("Deleted.")

# Clear email registry
with open("data/email_registry.json", "w") as f:
    json.dump([], f)
print("Email registry cleared.")
print(f"Chunks remaining: {col.count()}")