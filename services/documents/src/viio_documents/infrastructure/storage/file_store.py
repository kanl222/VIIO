"""Файловое хранилище оригиналов документов.

Интерфейс IFileStore намеренно минимален: save / load / delete.
LocalFileStore хранит файлы на локальном томе Docker.  Интерфейс
позволяет подключить S3-совместимое хранилище позже без изменения
логики каталога и очереди.

Ключ хранения (`storage_key`) — это неизменяемый относительный путь
внутри корневой директории хранилища.  Пользовательское имя файла
намеренно не используется как ключ.
"""

from __future__ import annotations

import logging
import os
import re
import uuid
from pathlib import Path
from typing import Protocol, runtime_checkable
from viio_documents.storage_config import StorageConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Интерфейс
# ---------------------------------------------------------------------------

@runtime_checkable
class IFileStore(Protocol):
    """Абстракция постоянного хранилища файлов."""

    def save(self, data: bytes, *, suffix: str = "") -> str:
        """Сохранить байты, вернуть storage_key для последующего доступа.

        Args:
            data: Содержимое файла в байтах.
            suffix: Расширение файла (например, ``.pdf``, ``.txt``).

        Returns:
            Строковый ключ, однозначно идентифицирующий файл в хранилище.
        """
        ...

    def load(self, storage_key: str) -> bytes:
        """Загрузить файл по ключу.

        Raises:
            FileNotFoundError: Если файл отсутствует или ключ некорректен.
        """
        ...

    def delete(self, storage_key: str) -> bool:
        """Удалить файл. Возвращает True, если файл существовал."""
        ...

    def exists(self, storage_key: str) -> bool:
        """Проверить наличие файла без загрузки содержимого."""
        ...


# ---------------------------------------------------------------------------
# Реализация: локальный том Docker
# ---------------------------------------------------------------------------

class LocalFileStore:
    """Хранилище файлов на локальной файловой системе (Docker-том).

    Каждый файл сохраняется по пути ``<root>/<storage_key>``, где
    ``storage_key`` имеет вид ``<uuid4_hex><suffix>``.  Вложенных
    директорий не создаётся — все файлы в одной плоской директории.

    Args:
        root: Путь к корневой директории хранилища.
    """

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)
        logger.info("LocalFileStore: корневая директория %s", self._root)

    # ------------------------------------------------------------------
    # IFileStore
    # ------------------------------------------------------------------

    def save(self, data: bytes, *, suffix: str = "") -> str:
        """Сохранить файл, вернуть storage_key."""
        clean_suffix = self._clean_suffix(suffix)
        key = uuid.uuid4().hex + clean_suffix
        path = self._root / key
        with path.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        logger.debug("LocalFileStore: сохранён %s (%d байт)", key, len(data))
        return key

    def load(self, storage_key: str) -> bytes:
        """Загрузить файл по ключу."""
        path = self._resolve(storage_key)
        if not path.exists():
            raise FileNotFoundError(f"Файл не найден: {storage_key!r}")
        return path.read_bytes()

    def delete(self, storage_key: str) -> bool:
        """Удалить файл. Возвращает True, если удаление произошло."""
        path = self._resolve(storage_key)
        if path.exists():
            path.unlink()
            logger.debug("LocalFileStore: удалён %s", storage_key)
            return True
        return False

    def exists(self, storage_key: str) -> bool:
        """Проверить наличие файла."""
        return self._resolve(storage_key).exists()

    # ------------------------------------------------------------------
    # Вспомогательные методы
    # ------------------------------------------------------------------

    def _resolve(self, storage_key: str) -> Path:
        """Разрешить ключ в абсолютный путь с проверкой выхода за пределы корня."""
        if not storage_key or "/" in storage_key or "\\" in storage_key or ".." in storage_key:
            raise ValueError(f"Недопустимый storage_key: {storage_key!r}")
        if not re.fullmatch(r"[a-f0-9]{32}(?:\.[A-Za-z0-9]{1,7})?", storage_key):
            raise ValueError("Недопустимый ключ файла")
        path = (self._root / storage_key).resolve()
        if path.parent != self._root.resolve():
            raise ValueError("Недопустимый путь файла")
        return path

    @staticmethod
    def _clean_suffix(suffix: str) -> str:
        """Нормализовать расширение файла: оставить только буквы, цифры и точку."""
        if not suffix:
            return ""
        clean = "".join(c for c in suffix if c.isascii() and c.isalnum())[:7]
        return "." + clean if clean else ""


# ---------------------------------------------------------------------------
# Реализация: S3-совместимое хранилище (MinIO / Ceph / AWS S3)
# ---------------------------------------------------------------------------

class S3FileStore:
    """Хранилище файлов в S3-совместимом объектном хранилище (MinIO, Ceph, AWS S3).

    Реализует протокол IFileStore. Позволяет горизонтально масштабировать воркеры
    без разделяемых локальных Docker-томов.
    """

    def __init__(
        self,
        bucket: str = "documents",
        endpoint_url: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        region: str = "us-east-1",
    ) -> None:
        self.bucket = bucket
        self.endpoint_url = endpoint_url
        self.access_key = access_key
        self.secret_key = secret_key
        self.region = region
        self._client = None
        try:
            import boto3
            self._client = boto3.client(
                "s3",
                endpoint_url=self.endpoint_url,
                aws_access_key_id=self.access_key,
                aws_secret_access_key=self.secret_key,
                region_name=self.region,
            )
            try:
                self._client.head_bucket(Bucket=self.bucket)
            except Exception:
                try:
                    self._client.create_bucket(Bucket=self.bucket)
                except Exception as e:
                    logger.warning("Не удалось проверить или создать S3 бакет %s: %s", self.bucket, e)
        except ImportError:
            logger.info("boto3 не установлен; S3FileStore потребует boto3 при обращении.")

    def _get_client(self):
        if self._client is None:
            try:
                import boto3
                self._client = boto3.client(
                    "s3",
                    endpoint_url=self.endpoint_url,
                    aws_access_key_id=self.access_key,
                    aws_secret_access_key=self.secret_key,
                    region_name=self.region,
                )
            except ImportError:
                raise RuntimeError("Для использования S3FileStore требуется пакет boto3 (uv add boto3)")
        return self._client

    def save(self, data: bytes, *, suffix: str = "") -> str:
        clean_suffix = LocalFileStore._clean_suffix(suffix)
        key = uuid.uuid4().hex + clean_suffix
        client = self._get_client()
        client.put_object(Bucket=self.bucket, Key=key, Body=data)
        logger.debug("S3FileStore: сохранён %s/%s (%d байт)", self.bucket, key, len(data))
        return key

    def load(self, storage_key: str) -> bytes:
        if not storage_key or "/" in storage_key or "\\" in storage_key or ".." in storage_key:
            raise ValueError(f"Недопустимый storage_key: {storage_key!r}")
        client = self._get_client()
        try:
            response = client.get_object(Bucket=self.bucket, Key=storage_key)
            return response["Body"].read()
        except Exception as e:
            if "NoSuchKey" in str(e) or "404" in str(e):
                raise FileNotFoundError(f"Файл не найден в S3: {storage_key!r}") from e
            raise

    def delete(self, storage_key: str) -> bool:
        if not storage_key or "/" in storage_key or "\\" in storage_key or ".." in storage_key:
            return False
        client = self._get_client()
        try:
            if not self.exists(storage_key):
                return False
            client.delete_object(Bucket=self.bucket, Key=storage_key)
            logger.debug("S3FileStore: удалён %s/%s", self.bucket, storage_key)
            return True
        except Exception:
            return False

    def exists(self, storage_key: str) -> bool:
        if not storage_key or "/" in storage_key or "\\" in storage_key or ".." in storage_key:
            return False
        client = self._get_client()
        try:
            client.head_object(Bucket=self.bucket, Key=storage_key)
            return True
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Фабрика хранилища
# ---------------------------------------------------------------------------

def create_file_store(settings: StorageConfig) -> IFileStore:
    """Создаёт экземпляр IFileStore в зависимости от конфигурации (local или s3)."""
    backend = getattr(settings, "documents_storage_backend", "local")
    if backend == "s3":
        return S3FileStore(
            bucket=getattr(settings, "s3_bucket", "documents"),
            endpoint_url=getattr(settings, "s3_endpoint_url", None),
            access_key=getattr(settings, "s3_access_key", None),
            secret_key=getattr(settings, "s3_secret_key", None),
            region=getattr(settings, "s3_region", "us-east-1"),
        )
    return LocalFileStore(getattr(settings, "documents_storage_path", "data/documents"))

