"""Тесты для локального хранилища оригиналов документов LocalFileStore."""

import os
from pathlib import Path
import pytest
from apiagent.services.file_store import LocalFileStore, IFileStore


def test_file_store_implements_interface(tmp_path: Path) -> None:
    store = LocalFileStore(tmp_path)
    assert isinstance(store, IFileStore)


def test_file_store_save_and_load(tmp_path: Path) -> None:
    store = LocalFileStore(tmp_path)
    data = b"%PDF-1.4 test document content"
    key = store.save(data, suffix=".pdf")

    assert key.endswith(".pdf")
    assert store.exists(key) is True

    loaded = store.load(key)
    assert loaded == data


def test_file_store_delete(tmp_path: Path) -> None:
    store = LocalFileStore(tmp_path)
    data = b"sample text"
    key = store.save(data, suffix=".txt")

    assert store.exists(key) is True
    assert store.delete(key) is True
    assert store.exists(key) is False
    assert store.delete(key) is False


def test_file_store_not_found_raises(tmp_path: Path) -> None:
    store = LocalFileStore(tmp_path)
    with pytest.raises(FileNotFoundError):
        store.load("00000000000000000000000000000000.txt")


def test_file_store_rejects_path_traversal(tmp_path: Path) -> None:
    store = LocalFileStore(tmp_path)
    with pytest.raises(ValueError, match="Недопустимый storage_key"):
        store.load("../secret.txt")

    with pytest.raises(ValueError, match="Недопустимый storage_key"):
        store.load("nested/file.txt")

    with pytest.raises(ValueError, match="Недопустимый storage_key"):
        store.load("")
