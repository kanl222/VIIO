"""Тесты конфигурации PostgreSQL и отказоустойчивости AsyncPostgresSaver для assistent."""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from assistent import create_app, lifespan
from assistent.config import Settings


@pytest.mark.asyncio
async def test_catalog_starts_when_checkpoint_pool_already_exists():
    from fastapi import FastAPI
    from types import SimpleNamespace
    app=FastAPI()
    app.state.settings=Settings(database_migrations_enabled=False)
    app.state.agent_service=MagicMock()
    app.state.enable_postgres_checkpointer=False
    app.state.pool=None
    app.state.catalog_service=SimpleNamespace(pool=None)
    facts=MagicMock()
    manager=MagicMock()
    manager.__aenter__=AsyncMock(return_value=facts)
    manager.__aexit__=AsyncMock(return_value=None)
    with patch('assistent.AsyncConnectionPool',return_value=manager):
        async with lifespan(app):assert app.state.catalog_service.pool is facts
    assert app.state.catalog_service.pool is None


def test_postgres_default_settings() -> None:
    """Проверка значений конфигурации PostgreSQL по умолчанию."""
    settings = Settings()

    assert settings.postgres_host == "localhost"
    assert settings.postgres_port == 5432
    assert settings.postgres_db == "agent_db"
    assert settings.postgres_user == "postgres"
    assert settings.postgres_password == "postgres"


def test_postgres_settings_override_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверка переопределения конфигурации PostgreSQL через переменные окружения."""
    monkeypatch.setenv("POSTGRES_HOST", "custom-postgres")
    monkeypatch.setenv("POSTGRES_PORT", "5433")
    monkeypatch.setenv("POSTGRES_DB", "custom_agent_db")
    monkeypatch.setenv("POSTGRES_USER", "agent_user")
    monkeypatch.setenv("POSTGRES_PASSWORD", "secret_pass")

    settings = Settings()

    assert settings.postgres_host == "custom-postgres"
    assert settings.postgres_port == 5433
    assert settings.postgres_db == "custom_agent_db"
    assert settings.postgres_user == "agent_user"
    assert settings.postgres_password == "secret_pass"


def test_create_app_initializes_with_memory_saver_and_sets_lifespan() -> None:
    """Проверка начальной инициализации приложения с MemorySaver и флагом enable_postgres."""
    settings = Settings(postgres_host="test-db")

    with patch("assistent.create_chat_model"), \
         patch("assistent.create_coding_model"), \
         patch("assistent.build_agent_graph") as mock_build_graph:

        mock_graph = MagicMock()
        mock_build_graph.return_value = mock_graph

        app = create_app(settings=settings)

        assert app is not None
        assert app.state.enable_postgres_checkpointer is True
        # Проверяем, что в граф передан MemorySaver в качестве initial_checkpointer
        _, kwargs = mock_build_graph.call_args
        checkpointer = kwargs.get("checkpointer")
        assert isinstance(checkpointer, MemorySaver)


def test_create_app_preserves_explicit_checkpointer() -> None:
    """Проверка, что явно переданный checkpointer сохраняется и отключает замену на PostgreSQL."""
    settings = Settings()
    custom_checkpointer = MemorySaver()

    with patch("assistent.create_chat_model"), \
         patch("assistent.create_coding_model"), \
         patch("assistent.build_agent_graph") as mock_build_graph:

        mock_graph = MagicMock()
        mock_build_graph.return_value = mock_graph

        app = create_app(settings=settings, checkpointer=custom_checkpointer)

        assert app is not None
        assert app.state.enable_postgres_checkpointer is False
        _, kwargs = mock_build_graph.call_args
        assert kwargs.get("checkpointer") is custom_checkpointer


@pytest.mark.asyncio
async def test_lifespan_uses_async_postgres_saver_when_available() -> None:
    """Проверка успешной регистрации AsyncPostgresSaver в lifespan и закрытия пула."""
    settings = Settings(
        postgres_host="test-db",
        postgres_port=5432,
        postgres_db="agent_db",
        postgres_user="test_user",
        postgres_password="test_password",
    )

    mock_saver = MagicMock(spec=AsyncPostgresSaver)
    mock_pool = MagicMock()
    mock_pool.close = AsyncMock()

    mock_agent_svc = MagicMock()
    expected_conn_string = "postgresql://test_user:test_password@test-db:5432/agent_db"

    app = MagicMock()
    app.state.settings = settings
    app.state.agent_service = mock_agent_svc
    app.state.enable_postgres_checkpointer = True
    app.state.pool = None
    app.state.catalog_service = None
    app.state.bulk_queue = None

    with patch("assistent.init_async_postgres_saver", new_callable=AsyncMock) as mock_init:
        mock_init.return_value = (mock_saver, mock_pool)

        async with lifespan(app):
            mock_init.assert_awaited_once()
            assert mock_init.call_args.kwargs == {"run_migrations": True}
            mock_agent_svc.set_checkpointer.assert_called_once_with(mock_saver)
            assert app.state.pool is mock_pool

        mock_pool.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_lifespan_fallback_when_postgres_fails() -> None:
    """Проверка плавного отката в lifespan при сбое подключения к PostgreSQL."""
    settings = Settings(postgres_host="unreachable-host")

    mock_agent_svc = MagicMock()

    app = MagicMock()
    app.state.settings = settings
    app.state.agent_service = mock_agent_svc
    app.state.enable_postgres_checkpointer = True
    app.state.pool = None
    app.state.catalog_service = None
    app.state.bulk_queue = None

    with patch("assistent.init_async_postgres_saver", new_callable=AsyncMock) as mock_init:
        mock_init.side_effect = RuntimeError("Connection refused")

        with pytest.raises(RuntimeError,match='Connection refused'):
            async with lifespan(app):pass
        mock_agent_svc.set_checkpointer.assert_not_called()
