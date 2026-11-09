"""Private project notes stored outside the document indexes."""

import hashlib
import re
import unicodedata

import anyio.to_thread
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from qdrant_client import models


COLLECTION = 'project_memory'
SECRET_PATTERN = re.compile(
    r'(?:password|пароль|api[_ -]?key|токен|secret)\s*[:=]|-----BEGIN .*PRIVATE KEY|\bsk-[a-zA-Z0-9]{16,}',
    re.I,
)


class ProjectMemoryRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)

    owner_scope: str = Field(pattern=r'^[a-f0-9]{64}$')
    project: str = Field(min_length=1, max_length=120)
    text: str = Field(min_length=1, max_length=2000)

    @property
    def project_key(self):
        return ' '.join(unicodedata.normalize('NFKC', self.project).casefold().split())


def register_project_memory(app, vectors):
    @app.post('/api/v1/project-memory/save')
    async def save(request: ProjectMemoryRequest):
        if SECRET_PATTERN.search(request.text):
            raise HTTPException(422, 'Пароли, токены и ключи не сохраняются в памяти проектов.')
        identifier = hashlib.sha256(
            f'{request.owner_scope}\0{request.project_key}\0{request.text}'.encode()
        ).hexdigest()
        metadata = {'document_id': identifier, 'chunk_index': 0,
                    'owner_scope': request.owner_scope, 'project_key': request.project_key}
        await anyio.to_thread.run_sync(vectors.add_texts, [request.text], [metadata])
        return {'id': identifier}

    @app.post('/api/v1/project-memory/search')
    async def search(request: ProjectMemoryRequest):
        filters = models.Filter(must=[
            models.FieldCondition(key=f'metadata.{key}', match=models.MatchValue(value=value))
            for key, value in (('owner_scope', request.owner_scope), ('project_key', request.project_key))
        ])
        matches = await anyio.to_thread.run_sync(vectors.similarity_search, request.text, 5, filters)
        return {'notes': [match.page_content for match in matches]}
