"""Upload task/result types and the original in-memory queue used by unit tests.

The application uses PersistentBulkQueue from persistent_bulk_queue.py.
BulkQueue below is a test-only implementation; it does not survive a restart.
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

logger = logging.getLogger(__name__)


class DocumentVersionConflict(ValueError):
    pass


# ---------------------------------------------------------------------------
# Задача очереди
# ---------------------------------------------------------------------------

@dataclass
class BulkUploadTask:
    """Описание одной задачи пакетной загрузки."""

    task_id: str
    """Уникальный идентификатор задачи (клиентский, для отслеживания прогресса)."""

    filename: str
    """Оригинальное имя файла."""

    data: bytes
    """Байты файла."""

    title: str
    """Название документа для каталога и поиска."""

    owner_id: str | None
    """Владелец документа."""

    is_public: bool
    mime_type: str
    allowed_roles: list[str]
    allowed_departments: list[str]

    # ------------------------------------------------------------------
    # Статус задачи (управляется BulkQueue)
    # ------------------------------------------------------------------
    status: str = "queued"
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    reviewed_content: str | None = None
    version_parent_id: str | None = None
    kind: str = 'document'


@dataclass
class PreparedReview:
    content: str
    engine: str
    pages: int
    document: dict | None = None


@dataclass
class BulkUploadResult:
    """Результат одной задачи пакетной загрузки."""

    task_id: str
    filename: str
    status: str
    document_id: str | None = None
    chunks_count: int | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Очередь
# ---------------------------------------------------------------------------

# Тип процессора: принимает BulkUploadTask, возвращает (document_id, chunks_count)
ProcessorType = Callable[[BulkUploadTask], Awaitable[tuple[str, int]]]


class BulkQueue:
    """asyncio.Queue с N параллельными воркерами и отслеживанием статусов.

    Args:
        processor: Async-функция, которая принимает BulkUploadTask и
                   возвращает (document_id, chunks_count).
        max_workers: Максимальное число параллельных задач.
        max_queue_size: Максимальный размер очереди (0 = неограничен).
    """

    def __init__(
        self,
        processor: ProcessorType,
        max_workers: int = 2,
        max_queue_size: int = 0,
    ) -> None:
        self._processor = processor
        self._max_workers = max(1, max_workers)
        self._queue: asyncio.Queue[BulkUploadTask] = asyncio.Queue(maxsize=max_queue_size)
        self._results: dict[str, BulkUploadResult] = {}
        self._worker_tasks: list[asyncio.Task[None]] = []
        self._running = False

    # ------------------------------------------------------------------
    # Жизненный цикл
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Запустить воркеры. Вызывается однократно при старте приложения."""
        if self._running:
            return
        self._running = True
        for i in range(self._max_workers):
            task = asyncio.create_task(self._worker(i), name=f"bulk-worker-{i}")
            self._worker_tasks.append(task)
        logger.info("BulkQueue: запущено %d воркеров", self._max_workers)

    async def stop(self) -> None:
        """Остановить воркеры, дождаться завершения текущих задач."""
        self._running = False
        for _ in self._worker_tasks:
            await self._queue.put(_sentinel())  # type: ignore[arg-type]
        await asyncio.gather(*self._worker_tasks, return_exceptions=True)
        self._worker_tasks.clear()
        logger.info("BulkQueue: все воркеры остановлены")

    # ------------------------------------------------------------------
    # Публичный интерфейс
    # ------------------------------------------------------------------

    def enqueue(self, task: BulkUploadTask) -> BulkUploadResult:
        """Поставить задачу в очередь.

        Raises:
            asyncio.QueueFull: Если очередь заполнена (max_queue_size > 0).
        """
        task.status = "queued"
        result = BulkUploadResult(
            task_id=task.task_id,
            filename=task.filename,
            status="queued",
        )
        self._results[task.task_id] = result
        self._queue.put_nowait(task)
        logger.info("BulkQueue: задача %s поставлена в очередь (%s)", task.task_id, task.filename)
        return result

    def get_status(self, task_id: str) -> BulkUploadResult | None:
        """Текущий статус задачи."""
        return self._results.get(task_id)

    def get_statuses(self, task_ids: list[str]) -> list[BulkUploadResult]:
        """Статусы группы задач."""
        return [self._results[tid] for tid in task_ids if tid in self._results]

    # ------------------------------------------------------------------
    # Воркер
    # ------------------------------------------------------------------

    async def _worker(self, worker_id: int) -> None:
        logger.debug("BulkQueue: воркер %d запущен", worker_id)
        while True:
            task = await self._queue.get()
            if isinstance(task, _Sentinel):
                self._queue.task_done()
                break
            result = self._results.get(task.task_id)
            if result is None:
                self._queue.task_done()
                continue

            result.status = "processing"
            task.status = "processing"
            logger.info(
                "BulkQueue[%d]: обработка %s (%s)", worker_id, task.task_id, task.filename
            )

            try:
                doc_id, chunks = await self._processor(task)
                result.document_id = doc_id
                result.chunks_count = chunks
                result.status = "ready"
                logger.info(
                    "BulkQueue[%d]: готово %s → doc=%s, chunks=%d",
                    worker_id, task.task_id, doc_id, chunks,
                )
            except Exception as exc:
                result.status = "error"
                result.error = str(exc) or repr(exc)
                logger.error(
                    "BulkQueue[%d]: ошибка %s — %s",
                    worker_id, task.task_id, result.error,
                )
            finally:
                self._queue.task_done()


# Sentinel — сигнал остановки воркера
class _Sentinel:
    pass


def _sentinel() -> _Sentinel:
    return _Sentinel()
