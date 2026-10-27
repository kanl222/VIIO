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
