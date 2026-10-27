"""Compatibility proxy; public inference runs only in the standalone service."""
import httpx


class ConsultantProxy:
    def __init__(self, url, api_key):
        self.url, self.api_key = url.rstrip('/'), api_key

    async def chat(self, message):
        async with httpx.AsyncClient(timeout=65, trust_env=False, follow_redirects=False) as client:
            response = await client.post(self.url + '/api/v1/consultant/chat', json={'message': message},
                                         headers={'X-Consultant-Key': self.api_key})
            if response.status_code == 504:
                raise TimeoutError('Consultant request timed out')
            response.raise_for_status()
            result = response.json()
            if not isinstance(result.get('reply'), str) or not isinstance(result.get('sources'), list):
                raise ValueError('Invalid consultant response')
            return result
