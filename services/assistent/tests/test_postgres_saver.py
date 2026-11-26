"""Тесты асинхронного адаптера и фабрики AsyncPostgresSaver."""

import inspect
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from assistent.infrastructure.persistence.postgres_saver import (
    AsyncCompatiblePostgresSaver,
    init_async_postgres_saver,
)


def test_async_postgres_saver_structure() -> None:
    """Проверка наследования от BaseCheckpointSaver и наличия нативных асинхронных методов."""
    assert issubclass(AsyncPostgresSaver, BaseCheckpointSaver)
    assert AsyncCompatiblePostgresSaver is AsyncPostgresSaver
    assert inspect.iscoroutinefunction(AsyncPostgresSaver.aget_tuple)
    assert inspect.iscoroutinefunction(AsyncPostgresSaver.aput)
    assert inspect.iscoroutinefunction(AsyncPostgresSaver.aput_writes)
    assert inspect.iscoroutinefunction(AsyncPostgresSaver.adelete_thread)
    assert inspect.iscoroutinefunction(AsyncPostgresSaver.setup)


@pytest.mark.asyncio
async def test_init_async_postgres_saver_initializes_pool_and_setup() -> None:
    """Проверка создания AsyncConnectionPool, открытия и вызова setup() у AsyncPostgresSaver."""
    conn_string = "postgresql://user:pass@localhost:5432/agent_db"

    mock_pool = MagicMock()
    mock_pool.open = AsyncMock()

    mock_saver = MagicMock(spec=AsyncPostgresSaver)
    mock_saver.setup = AsyncMock()

    with patch("assistent.infrastructure.persistence.postgres_saver.AsyncConnectionPool", return_value=mock_pool) as mock_pool_cls, \
         patch("assistent.infrastructure.persistence.postgres_saver.AsyncPostgresSaver", return_value=mock_saver) as mock_saver_cls:

        saver, pool = await init_async_postgres_saver(conn_string, timeout=3.0)

        assert saver is mock_saver
        assert pool is mock_pool

        mock_pool_cls.assert_called_once()
        _, pool_kwargs = mock_pool_cls.call_args
        assert pool_kwargs["conninfo"] == conn_string
        assert pool_kwargs["open"] is False

        mock_pool.open.assert_awaited_once_with(wait=True, timeout=3.0)
        mock_saver_cls.assert_called_once_with(conn=mock_pool)
        mock_saver.setup.assert_awaited_once()
