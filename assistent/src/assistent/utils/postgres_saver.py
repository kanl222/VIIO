"""Адаптер и фабрика AsyncPostgresSaver для персистентного хранения сессий LangGraph."""

from __future__ import annotations

import logging
from typing import Any
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

logger = logging.getLogger(__name__)


async def init_async_postgres_saver(
    conn_string: str,
    *,
    min_size: int = 1,
    max_size: int = 20,
    timeout: float = 5.0,
) -> tuple[AsyncPostgresSaver, AsyncConnectionPool]:
    """Создание пула соединений AsyncConnectionPool и инициализация AsyncPostgresSaver.

    Использование пула AsyncConnectionPool обеспечивает постоянное подключение,
    автоматический реконнект и безопасное параллельное обслуживание асинхронных
    запросов в LangGraph через ainvoke/astream.

    Args:
        conn_string: Строка подключения к PostgreSQL (postgresql://user:pass@host:port/db).
        min_size: Минимальное количество соединений в пуле.
        max_size: Максимальное количество соединений в пуле.
        timeout: Таймаут ожидания готовности пула соединений (в секундах).

    Returns:
        Кортеж (saver: AsyncPostgresSaver, pool: AsyncConnectionPool).
    """
    pool = AsyncConnectionPool(
        conninfo=conn_string,
        min_size=min_size,
        max_size=max_size,
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
        },
        open=False,
        timeout=timeout,
    )
    await pool.open(wait=True, timeout=timeout)
    saver = AsyncPostgresSaver(conn=pool)
    await saver.setup()
    return saver, pool


# Для обратной совместимости с существующим кодом и тестами
AsyncCompatiblePostgresSaver = AsyncPostgresSaver

__all__ = [
    "AsyncPostgresSaver",
    "AsyncCompatiblePostgresSaver",
    "init_async_postgres_saver",
]
