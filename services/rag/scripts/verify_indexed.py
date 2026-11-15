import os
from qdrant_client import QdrantClient, models

client = QdrantClient(url=os.environ.get("QDRANT_URL", "http://qdrant:6333"), api_key=os.environ.get("QDRANT_API_KEY"))

docs = [
    "doc-kontakty-tehpodderzhki-islo-oas",
    "doc-elektronny-recept-karta-orenburzhtsa",
    "doc-prikazy-mz-rf-1094n-1093n-pravila-otpuska",
    "doc-mdlp-shemy-vybytiya-registrator-vybytiya",
    "doc-goryachie-linii-lekarstvennogo-obespecheniya-oo",
    "doc-federalnye-zakony-323-61-178-3-fz",
    "doc-postanovleniya-pravitelstva-rf-890-1556",
    "doc-regionalnye-akty-oo-postanovleniya-528-597",
    "doc-regionalnye-rasporyazheniya-mz-oo-reestry"
]

print("=== Verification of Qdrant Big RAG Collection ===")
total_pts = 0
for d in docs:
    pts, _ = client.scroll(
        "knowledge_base_big_rag_20261025",
        scroll_filter=models.Filter(must=[models.FieldCondition(key="metadata.document_id", match=models.MatchValue(value=d))]),
        limit=100
    )
    total_pts += len(pts)
    print(f"[{d}]: {len(pts)} chunks")
print(f"Total new chunks in big_rag: {total_pts}")

print("\n=== Verification of Qdrant Public Collection ===")
pub_pts = 0
for d in docs:
    pts, _ = client.scroll(
        "knowledge_public",
        scroll_filter=models.Filter(must=[models.FieldCondition(key="metadata.source_document_id", match=models.MatchValue(value=d))]),
        limit=100
    )
    pub_pts += len(pts)
    print(f"[{d}]: {len(pts)} chunks")
print(f"Total public chunks: {pub_pts}")
