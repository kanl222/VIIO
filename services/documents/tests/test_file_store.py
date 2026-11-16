"""Тесты для хранилищ файлов LocalFileStore и S3FileStore."""

from unittest.mock import MagicMock
import pytest

from viio_documents.config import Settings
from viio_documents.services.file_store import (
    IFileStore,
    LocalFileStore,
    S3FileStore,
    create_file_store,
)


def test_local_file_store_lifecycle(tmp_path):
    store = LocalFileStore(tmp_path)
    assert isinstance(store, IFileStore)

    data = b"Hello VIIO Storage"
    key = store.save(data, suffix=".txt")
    assert key.endswith(".txt")
    assert store.exists(key)
    assert store.load(key) == data

    assert store.delete(key) is True
    assert not store.exists(key)
    assert store.delete(key) is False

    with pytest.raises(FileNotFoundError):
        store.load(key)


def test_local_file_store_path_traversal(tmp_path):
    store = LocalFileStore(tmp_path)
    with pytest.raises(ValueError):
        store.load("../evil.txt")
    with pytest.raises(ValueError):
        store.load("invalid_key_format")


def test_create_file_store_factory(tmp_path):
    local_settings = Settings(_env_file=None, documents_storage_backend="local", documents_storage_path=str(tmp_path))
    local_store = create_file_store(local_settings)
    assert isinstance(local_store, LocalFileStore)

    s3_settings = Settings(
        _env_file=None,
        documents_storage_backend="s3",
        s3_bucket="custom-bucket",
        s3_endpoint_url="http://minio:9000",
        s3_access_key="test-key",
        s3_secret_key="test-secret",
    )
    s3_store = create_file_store(s3_settings)
    assert isinstance(s3_store, S3FileStore)
    assert s3_store.bucket == "custom-bucket"
    assert s3_store.endpoint_url == "http://minio:9000"


def test_s3_file_store_mocked_operations():
    s3_store = S3FileStore(bucket="test-bucket")
    mock_boto = MagicMock()
    s3_store._client = mock_boto

    data = b"S3 Payload"
    key = s3_store.save(data, suffix=".pdf")
    assert key.endswith(".pdf")
    mock_boto.put_object.assert_called_once_with(Bucket="test-bucket", Key=key, Body=data)

    mock_body = MagicMock()
    mock_body.read.return_value = data
    mock_boto.get_object.return_value = {"Body": mock_body}
    assert s3_store.load(key) == data

    mock_boto.head_object.return_value = {}
    assert s3_store.exists(key) is True

    assert s3_store.delete(key) is True
    mock_boto.delete_object.assert_called_once_with(Bucket="test-bucket", Key=key)
