"""Тесты для сервиса каталога документов DocumentCatalogService."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
import pytest
from assistent.services.document_catalog import (
    DocumentCatalogService,
    DocumentRecord,
    sha256_of_bytes,
    sha256_of_text,
)


def test_sha256_utilities() -> None:
    data = b"hello world"
    text = "hello world"
    h1 = sha256_of_bytes(data)
    h2 = sha256_of_text(text)
    assert h1 == h2
    assert len(h1) == 64


def test_document_record_from_row() -> None:
    now = datetime.now(timezone.utc)
    row = {
        "id": "doc-12345678",
        "title": "Регламент отпусков",
        "mime_type": "application/pdf",
        "file_size": 1024,
        "sha256": "abc123hash",
        "owner_id": "user-admin",
        "version": 1,
        "parent_id": None,
        "status": "ready",
        "is_public": False,
        "allowed_roles": ["employee", "manager"],
        "allowed_departments": ["hr"],
        "storage_key": "abc123key.pdf",
        "chunks_count": 5,
        "created_at": now,
        "updated_at": now,
    }
    record = DocumentRecord.from_row(row)
    assert record.id == "doc-12345678"
    assert record.title == "Регламент отпусков"
    assert record.version == 1
    assert record.chunks_count == 5
    assert record.allowed_roles == ["employee", "manager"]
    assert record.allowed_departments == ["hr"]


@pytest.mark.asyncio
async def test_catalog_create_and_get() -> None:
    now = datetime.now(timezone.utc)
    fake_row = {
        "id": "doc-test1",
        "title": "Test Title",
        "mime_type": "text/plain",
        "file_size": 42,
        "sha256": "hash42",
        "owner_id": "u1",
        "version": 1,
        "parent_id": None,
        "status": "indexed",
        "is_public": False,
        "allowed_roles": ["employee"],
        "allowed_departments": ["general"],
        "storage_key": None,
        "chunks_count": 2,
        "created_at": now,
        "updated_at": now,
    }

    mock_cur = MagicMock()
    mock_cur.fetchone = AsyncMock(return_value=fake_row)

    mock_conn = MagicMock()
    mock_conn.execute = AsyncMock(return_value=mock_cur)

    # Context manager for conn
    mock_conn_ctx = AsyncMock()
    mock_conn_ctx.__aenter__.return_value = mock_conn
    mock_conn_ctx.__aexit__.return_value = None

    mock_pool = MagicMock()
    mock_pool.connection.return_value = mock_conn_ctx

    svc = DocumentCatalogService(mock_pool)
    record = await svc.create(
        doc_id="doc-test1",
        title="Test Title",
        chunks_count=2,
        file_size=42,
        sha256="hash42",
        owner_id="u1",
    )

    assert record.id == "doc-test1"
    assert record.chunks_count == 2
    mock_conn.execute.assert_awaited_once()

    # Test get
    record_get = await svc.get("doc-test1")
    assert record_get is not None
    assert record_get.id == "doc-test1"


@pytest.mark.asyncio
async def test_catalog_delete() -> None:
    mock_cur = MagicMock()
    mock_cur.fetchone = AsyncMock(return_value={"id": "doc-test1"})

    mock_conn = MagicMock()
    mock_conn.execute = AsyncMock(return_value=mock_cur)

    mock_conn_ctx = AsyncMock()
    mock_conn_ctx.__aenter__.return_value = mock_conn
    mock_conn_ctx.__aexit__.return_value = None

    mock_pool = MagicMock()
    mock_pool.connection.return_value = mock_conn_ctx

    svc = DocumentCatalogService(mock_pool)
    deleted = await svc.delete("doc-test1")
    assert deleted is True
