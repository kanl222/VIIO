"""Тесты для REST API каталога документов, скачивания и пакетной загрузки."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
import pytest
from fastapi.testclient import TestClient

from assistent import create_app
from viio_documents.config import Settings
from viio_documents.services.document_catalog import DocumentListResult, DocumentRecord


@pytest.fixture
def api_client():
    settings = Settings(
        internal_api_key="test-secret-key",
        documents_catalog_enabled=False,  # не подключаемся к реальному postgres в тестах
    )
    mock_agent_svc = MagicMock()
    mock_doc_svc = MagicMock()

    # Создаем mock catalog и file_store
    mock_catalog = MagicMock()
    mock_file_store = MagicMock()
    mock_bulk_queue = MagicMock()

    from viio_documents.router import create_api_router
    from fastapi import FastAPI

    app = FastAPI()
    router = create_api_router(
        agent_service=mock_agent_svc,
        document_service=mock_doc_svc,
        settings=settings,
        catalog_service=mock_catalog,
        file_store=mock_file_store,
        bulk_queue=mock_bulk_queue,
    )
    app.include_router(router)

    client = TestClient(app)
    return client, mock_catalog, mock_file_store, mock_bulk_queue


def test_catalog_list_endpoint(api_client):
    client, mock_catalog, _, _ = api_client
    now = datetime.now(timezone.utc)
    mock_catalog.list_documents = AsyncMock(return_value=DocumentListResult(
        items=[
            DocumentRecord(
                id="doc-1", title="Doc 1", mime_type="text/plain",
                file_size=100, sha256="abc", owner_id="u1", version=1,
                parent_id=None, status="indexed", is_public=True,
                allowed_roles=["employee"], allowed_departments=["general"],
                storage_key="k1", chunks_count=2, created_at=now, updated_at=now,
            )
        ],
        total=1, page=1, page_size=20,
    ))

    resp = client.get(
        "/api/v1/documents/catalog?page=1&page_size=20",
        headers={"X-Internal-Token": "test-secret-key"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["id"] == "doc-1"
    assert data["items"][0]["has_original"] is True


def test_catalog_download_forbidden_for_disallowed_role(api_client):
    client, mock_catalog, mock_file_store, _ = api_client
    now = datetime.now(timezone.utc)
    mock_catalog.get = AsyncMock(return_value=DocumentRecord(
        id="doc-secret", title="Secret Doc", mime_type="application/pdf",
        file_size=500, sha256="secret", owner_id="admin", version=1,
        parent_id=None, status="indexed", is_public=False,
        allowed_roles=["admin"], allowed_departments=["hr"],
        storage_key="secret.pdf", chunks_count=1, created_at=now, updated_at=now,
    ))

    # Сотрудник пытается скачать документ, доступный только администратору
    resp = client.get(
        "/api/v1/documents/download/doc-secret?user_role=employee",
        headers={"X-Internal-Token": "test-secret-key"},
    )
    assert resp.status_code == 403
    assert "Доступ к документу запрещён" in resp.json()["detail"]


def test_catalog_download_allowed_for_permitted_role(api_client):
    client, mock_catalog, mock_file_store, _ = api_client
    now = datetime.now(timezone.utc)
    mock_catalog.get = AsyncMock(return_value=DocumentRecord(
        id="doc-ok", title="Employee Guide", mime_type="text/plain",
        file_size=12, sha256="ok", owner_id="admin", version=1,
        parent_id=None, status="indexed", is_public=False,
        allowed_roles=["employee", "admin"], allowed_departments=["general"],
        storage_key="guide.txt", chunks_count=1, created_at=now, updated_at=now,
    ))
    mock_file_store.load.return_value = b"Hello Guide!"

    resp = client.get(
        "/api/v1/documents/download/doc-ok?user_role=employee",
        headers={"X-Internal-Token": "test-secret-key"},
    )
    assert resp.status_code == 200
    assert resp.content == b"Hello Guide!"
    assert resp.headers["content-type"].startswith("text/plain")


def test_catalog_download_allowed_for_admin_always(api_client):
    client, mock_catalog, mock_file_store, _ = api_client
    now = datetime.now(timezone.utc)
    mock_catalog.get = AsyncMock(return_value=DocumentRecord(
        id="doc-private", title="Private", mime_type="text/plain",
        file_size=8, sha256="priv", owner_id="hr-manager", version=1,
        parent_id=None, status="indexed", is_public=False,
        allowed_roles=["manager"], allowed_departments=["hr"],
        storage_key="priv.txt", chunks_count=1, created_at=now, updated_at=now,
    ))
    mock_file_store.load.return_value = b"Admin OK"

    resp = client.get(
        "/api/v1/documents/download/doc-private?user_role=admin",
        headers={"X-Internal-Token": "test-secret-key"},
    )
    assert resp.status_code == 200
    assert resp.content == b"Admin OK"
