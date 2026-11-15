"""Shared Managed RAG cloud proxy."""
from __future__ import annotations

from typing import Any
import httpx


class ManagedRagError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


async def query_managed_rag(
    endpoint_url: str,
    auth_header_name: str,
    auth_token: str,
    message: str,
    confirmed_public: bool,
    timeout: float = 75.0,
) -> dict[str, Any]:
    """Execute a public query against external Managed RAG."""
    if not confirmed_public:
        raise ManagedRagError(403, "Подтвердите публичность вопроса для внешнего облака")

    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False, follow_redirects=False) as client:
            response = await client.post(
                endpoint_url,
                headers={auth_header_name: auth_token or ""},
                json={"message": message, "confirmed_public_for_external_cloud": True},
            )
        if response.status_code in (400, 403, 422):
            raise ManagedRagError(response.status_code, "Проверьте вопрос и подтверждение его публичности")
        response.raise_for_status()
        result = response.json()
        if not isinstance(result.get("reply"), str) or not isinstance(result.get("sources"), list):
            raise ValueError("Invalid managed RAG response format")
        return result
    except ManagedRagError:
        raise
    except (httpx.HTTPError, ValueError) as err:
        raise ManagedRagError(503, "Yandex RAG временно недоступен") from err
