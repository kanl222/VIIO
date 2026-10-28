"""Only the publication gateway is reachable through this application interface."""
from pydantic import BaseModel, ConfigDict, Field
import time
from viio_consultant.diagnostics import enabled, record


class PublishedMaterial(BaseModel):
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)
    id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1, max_length=50000)


class HttpPublicationSource:
    def __init__(self, client):
        self.client = client

    async def public_search(self, query):
        start = time.monotonic()
        response = await self.client.post('/api/v1/consultant-materials/search', json={'message': query, **({'diagnostics': True} if enabled() else {})})
        response.raise_for_status()
        rows = response.json()['items']
        if not isinstance(rows, list) or len(rows) > 4:
            raise ValueError('Invalid publication search response')
        items = [PublishedMaterial.model_validate(row).model_dump() for row in rows]
        if 'diagnostics' in response.json():
            record('retrieval', scope='publication_gateway', query=query,
                   duration_ms=round((time.monotonic()-start)*1000), **response.json()['diagnostics'])
        return items

    async def public_ids(self, identifiers):
        response = await self.client.post('/api/v1/consultant-materials/validate', json={'publication_ids': identifiers})
        response.raise_for_status()
        result = response.json()['publication_ids']
        if not isinstance(result, list) or any(not isinstance(item, str) for item in result):
            raise ValueError('Invalid publication validation response')
        return set(result)
