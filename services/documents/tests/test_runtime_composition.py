from unittest.mock import patch

import pytest

from viio_documents import create_app
from viio_documents.config import Settings
from viio_documents.storage_config import StorageConfig


@pytest.mark.parametrize("role", ["documents", "document-worker"])
def test_only_worker_builds_processing_dependencies(role, tmp_path):
    settings = Settings(_env_file=None, service_role=role,
                        documents_storage_path=str(tmp_path / "originals"))
    with patch("viio_documents.QdrantClient") as qdrant, \
         patch("viio_documents.create_embeddings") as embeddings, \
         patch("viio_documents.QdrantVectorStoreAdapter"), \
         patch("viio_documents.DocumentIngestion") as ingestion:
        app = create_app(settings)
    if role == "document-worker":
        embeddings.assert_called_once()
        ingestion.assert_called_once()
        assert app.state.bulk_queue.processor is app.state.ingestion_service
    else:
        embeddings.assert_not_called()
        ingestion.assert_not_called()
        assert app.state.ingestion_service is None
        assert app.state.bulk_queue.processor is None
        qdrant.return_value.collection_exists.return_value = True
        qdrant.return_value.count.return_value.count = 12
        assert app.state.document_service.count_documents() == 12


def test_storage_config_does_not_expose_service_credentials():
    settings = Settings(_env_file=None, s3_access_key="storage-test-access",
                        s3_secret_key="storage-test-secret")
    config = StorageConfig.from_settings(settings)
    assert config.s3_secret_key == "storage-test-secret"
    assert not hasattr(config, "postgres_password")
    assert not hasattr(config, "documents_admin_key")
    assert "storage-test" not in repr(config)


def test_documents_does_not_load_agent_only_settings():
    config = Settings(_env_file=None, coding_model="unused", pgsql_enabled=True)
    assert not hasattr(config, "coding_model")
    assert not hasattr(config, "pgsql_enabled")
