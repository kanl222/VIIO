"""Only the publication gateway is reachable through this application interface."""
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
import time
import hashlib
from viio_consultant.observability.diagnostics import enabled, record


class PublishedMaterial(BaseModel):
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)
    id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1, max_length=1_000_000)
    access_class: Literal['public', 'restricted'] = 'restricted'


class HttpPublicationSource:
    def __init__(self, client, *, cloud_public_material_hashes=None, cloud_public_only=False):
        self.client = client
        self.cloud_hashes = None if cloud_public_material_hashes is None else frozenset(cloud_public_material_hashes)
        self.cloud_public_only = cloud_public_only

    async def public_search(self, query):
        start = time.monotonic()
        response = await self.client.post('/api/v1/consultant-materials/search', json={'message': query, **({'diagnostics': True} if enabled() else {})})
        response.raise_for_status()
        rows = response.json()['items']
        if not isinstance(rows, list) or len(rows) > 4:
            raise ValueError('Invalid publication search response')
        materials = [PublishedMaterial.model_validate(row) for row in rows]
        if self.cloud_public_only:
            materials = [item for item in materials if item.access_class == 'public']
        items = [item.model_dump(exclude={'access_class'}) for item in materials]
        if self.cloud_hashes is not None:
            items = [item for item in items if hashlib.sha256(
                (item['title'] + '\n' + item['content']).encode('utf-8')).hexdigest() in self.cloud_hashes]
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
