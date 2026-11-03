"""Rebuildable Qdrant index of approved publication snapshots only."""
import hashlib
from functools import partial

import anyio.to_thread
from viio_rag.chunks import document_chunks, CHUNKER_VERSION


class PublicationIndex:
    def __init__(self, adapter):
        self.adapter = adapter

    @staticmethod
    def digest(row):
        return hashlib.sha256((CHUNKER_VERSION + '\0' + row['title'] + '\0' + row['content']).encode()).hexdigest()

    async def put(self, row):
        def write():
            parts = list(document_chunks(row['content'], 800, 120))
            chunks = [text for text, _ in parts]
            if not chunks:
                raise ValueError('Пустая публикация')
            metadata = [dict(**parts[i][1], document_id=str(row['id']), publication_id=str(row['id']),
                source_document_id=row['document_id'], title=row['title'], chunk_index=i,
                snapshot_hash=self.digest(row), expected_chunks=len(chunks), ingestion_ready=False)
                for i in range(len(chunks))]
            self.adapter.add_texts([row['title'] + '\n' + chunk for chunk in chunks], metadata)
            # Do not remove the previous index before embeddings succeed.
            self.adapter.delete_document_tail(str(row['id']), len(chunks))
            self.adapter.publish_document(str(row['id']))
        await anyio.to_thread.run_sync(write)

    async def remove(self, identifier):
        await anyio.to_thread.run_sync(self.adapter.delete_document, str(identifier))

    async def inventory(self):
        def read():
            client, collection = self.adapter._client, self.adapter._collection_name
            if not client.collection_exists(collection):
                return {}
            found, offset = {}, None
            while True:
                points, offset = client.scroll(collection, offset=offset, limit=256, with_vectors=False)
                for point in points:
                    meta = (point.payload or {}).get('metadata', {})
                    identifier = meta.get('document_id')
                    found.setdefault(identifier, []).append(meta)
                if offset is None:
                    return found
        return await anyio.to_thread.run_sync(read)

    async def search(self, query, k=24):
        return await anyio.to_thread.run_sync(partial(self.adapter.similarity_search, query, k=k))
