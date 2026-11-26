"""Read-only index health used by the Documents API without embedding models."""
from qdrant_client import QdrantClient


class DocumentIndexStatus:
    def __init__(self, client: QdrantClient, collection_name: str):
        self.client = client
        self.collection_name = collection_name

    def count_documents(self) -> int:
        if not self.client.collection_exists(self.collection_name):
            return 0
        return self.client.count(collection_name=self.collection_name).count
