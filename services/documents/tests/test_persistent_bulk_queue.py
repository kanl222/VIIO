"""Unit тесты для PersistentBulkQueue и механизма LISTEN/NOTIFY."""

import asyncio
from unittest.mock import AsyncMock, MagicMock
import pytest

from viio_documents.services.bulk_queue import BulkUploadTask
from viio_documents.services.persistent_bulk_queue import PersistentBulkQueue


@pytest.mark.asyncio
async def test_persistent_bulk_queue_event_trigger_on_enqueue():
    processor = AsyncMock()
    catalog = MagicMock()
    catalog.available = False
    store = MagicMock()
    store.save.return_value = "key123.txt"

    queue = PersistentBulkQueue(processor, catalog, store, max_workers=1)
    queue.jobs.submit = AsyncMock()

    task = BulkUploadTask(
        task_id="task-1",
        filename="test.txt",
        data=b"hello",
        title="Test",
        owner_id="u1",
        is_public=False,
        mime_type="text/plain",
        allowed_roles=["employee"],
        allowed_departments=["general"],
    )

    assert not queue._notify_event.is_set()
    res = await queue.enqueue(task)
    assert res.task_id == "task-1"
    assert res.status == "queued"
    assert queue._notify_event.is_set()


@pytest.mark.asyncio
async def test_persistent_bulk_queue_event_trigger_on_retry_and_approve():
    processor = AsyncMock()
    catalog = MagicMock()
    catalog.available = False
    store = MagicMock()

    queue = PersistentBulkQueue(processor, catalog, store, max_workers=1)
    queue.jobs.retry = AsyncMock(return_value=True)
    queue.jobs.approve = AsyncMock(return_value=True)

    assert not queue._notify_event.is_set()
    assert await queue.retry("task-1") is True
    assert queue._notify_event.is_set()

    queue._notify_event.clear()
    assert not queue._notify_event.is_set()

    assert await queue.approve("task-1", "token123", "approved text", "admin") is True
    assert queue._notify_event.is_set()


@pytest.mark.asyncio
async def test_persistent_bulk_queue_start_stop_lifecycle():
    processor = AsyncMock()
    catalog = MagicMock()
    catalog.available = False
    store = MagicMock()

    queue = PersistentBulkQueue(processor, catalog, store, max_workers=2)
    queue.start()

    assert len(queue.workers) == 2
    assert queue.listener_task is not None
    assert not queue.listener_task.done()

    await queue.stop()
    assert len(queue.workers) == 0
    assert queue.listener_task is None
