"""Интеграционные тесты для эндпоинтов FastAPI."""

from typing import Any
import pytest
from fastapi.testclient import TestClient

from apiagent import create_app
from apiagent.config import Settings
from apiagent.interfaces._protocols import DocumentMetadata, IAgentService, IDocumentService


class MockAgentService(IAgentService):
    """Мок сервиса агента для тестирования API."""

    def __init__(self) -> None:
        self.should_fail = False

    async def chat(self, message: str, thread_id: str | None = None) -> tuple[str, str]:
        if self.should_fail:
            raise RuntimeError("Сервис Ollama временно недоступен")
        if not message.strip():
            raise ValueError("Сообщение не может быть пустым")
        effective_thread_id = thread_id or "test-thread-id-123"
        return f"Эхо: {message}", effective_thread_id


class MockDocumentService(IDocumentService):
    """Мок сервиса документов для тестирования API."""

    def __init__(self) -> None:
        self.count = 5
        self.should_fail = False

    def index_document(
        self, title: str, content: str, metadata: dict[str, Any] | None = None
    ) -> DocumentMetadata:
        if self.should_fail:
            raise RuntimeError("Сбой подключения к Qdrant")
        clean_title = title.strip()
        clean_content = content.strip()
        if not clean_title or not clean_content:
            raise ValueError("Пустой заголовок или содержимое")
        return DocumentMetadata(
            document_id="doc-test-1",
            title=clean_title,
            chunks_count=3,
            status="indexed",
        )

    def count_documents(self) -> int:
        if self.should_fail:
            raise RuntimeError("Сбой подключения к Qdrant")
        return self.count


@pytest.fixture
def client() -> TestClient:
    """Фикстура тестового клиента FastAPI с мок-сервисами."""
    settings = Settings(ollama_model="test-llama")
    agent_svc = MockAgentService()
    doc_svc = MockDocumentService()
    app = create_app(
        settings=settings,
        agent_service=agent_svc,
        document_service=doc_svc,
    )
    return TestClient(app)


def test_health_endpoint(client: TestClient) -> None:
    """Проверка эндпоинта GET /health."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["model"] == "test-llama"
    assert data["documents_count"] == 5


def test_create_document_endpoint_success(client: TestClient) -> None:
    """Проверка успешного добавления документа POST /api/v1/documents."""
    payload = {
        "title": "Регламент возврата",
        "content": "Полный текст регламента",
        "metadata": {"source": "wiki", "author": "support"},
    }
    response = client.post("/api/v1/documents", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["document_id"] == "doc-test-1"
    assert data["title"] == "Регламент возврата"
    assert data["chunks_count"] == 3
    assert data["status"] == "indexed"


def test_create_document_validation_failure(client: TestClient) -> None:
    """Проверка валидации при отправке пустого документа."""
    payload = {"title": "   ", "content": "Текст"}
    response = client.post("/api/v1/documents", json=payload)
    assert response.status_code in (400, 422)


def test_chat_endpoint_success(client: TestClient) -> None:
    """Проверка успешной отправки сообщения POST /api/v1/chat."""
    payload = {
        "message": "Что насчет гарантии?",
        "thread_id": "session-xyz",
    }
    response = client.post("/api/v1/chat", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["reply"] == "Эхо: Что насчет гарантии?"
    assert data["thread_id"] == "session-xyz"


def test_chat_endpoint_empty_message(client: TestClient) -> None:
    """Проверка отклонения запроса с пустым сообщением."""
    payload = {"message": "   "}
    response = client.post("/api/v1/chat", json=payload)
    assert response.status_code in (400, 422)


def test_chat_endpoint_service_error_handling() -> None:
    """Проверка возврата HTTP 503 при сбое внешнего сервиса."""
    agent_svc = MockAgentService()
    agent_svc.should_fail = True
    doc_svc = MockDocumentService()
    app = create_app(
        settings=Settings(),
        agent_service=agent_svc,
        document_service=doc_svc,
    )
    custom_client = TestClient(app)

    response = custom_client.post("/api/v1/chat", json={"message": "Привет"})
    assert response.status_code in (500, 503)


def test_create_document_uses_run_sync(monkeypatch) -> None:
    """Проверка, что вызов index_document делегируется в anyio.to_thread.run_sync."""
    import anyio.to_thread

    run_sync_called = []
    original_run_sync = anyio.to_thread.run_sync

    async def fake_run_sync(func, *args, **kwargs):
        run_sync_called.append(func)
        return await original_run_sync(func, *args, **kwargs)

    monkeypatch.setattr(anyio.to_thread, "run_sync", fake_run_sync)

    doc_svc = MockDocumentService()
    app = create_app(
        settings=Settings(),
        agent_service=MockAgentService(),
        document_service=doc_svc,
    )
    test_client = TestClient(app)
    response = test_client.post(
        "/api/v1/documents", json={"title": "Тест", "content": "Контент"}
    )
    assert response.status_code == 201
    assert len(run_sync_called) == 1
    assert run_sync_called[0] == doc_svc.index_document
