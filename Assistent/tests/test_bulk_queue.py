"""Тесты для очереди фоновой пакетной обработки BulkQueue."""

import asyncio
import pytest
from viio_documents.services.bulk_queue import BulkQueue, BulkUploadTask, BulkUploadResult


@pytest.mark.asyncio
async def test_bulk_queue_processes_tasks() -> None:
    processed = []

    async def mock_processor(task: BulkUploadTask) -> tuple[str, int]:
        await asyncio.sleep(0.01)
        doc_id = f"doc-{task.task_id[:6]}"
        processed.append(task.filename)
        return doc_id, 3

    queue = BulkQueue(processor=mock_processor, max_workers=2)
    queue.start()

    task1 = BulkUploadTask(
        task_id="task-1",
        filename="report1.pdf",
        data=b"dummy1",
        title="Report 1",
        owner_id="user-1",
        is_public=False,
        mime_type="application/pdf",
        allowed_roles=["employee"],
        allowed_departments=["general"],
    )
    task2 = BulkUploadTask(
        task_id="task-2",
        filename="report2.txt",
        data=b"dummy2",
        title="Report 2",
        owner_id="user-1",
        is_public=True,
        mime_type="text/plain",
        allowed_roles=["employee"],
        allowed_departments=["general"],
    )

    res1 = queue.enqueue(task1)
    res2 = queue.enqueue(task2)
    assert res1.status == "queued"
    assert res2.status == "queued"

    # Wait for completion
    for _ in range(50):
        s1 = queue.get_status("task-1")
        s2 = queue.get_status("task-2")
        if s1 and s1.status == "ready" and s2 and s2.status == "ready":
            break
        await asyncio.sleep(0.02)

    await queue.stop()

    assert len(processed) == 2
    assert "report1.pdf" in processed
    assert "report2.txt" in processed

    final_s1 = queue.get_status("task-1")
    assert final_s1 is not None
    assert final_s1.status == "ready"
    assert final_s1.document_id == "doc-task-1"
    assert final_s1.chunks_count == 3


@pytest.mark.asyncio
async def test_bulk_queue_handles_errors_gracefully() -> None:
    async def failing_processor(task: BulkUploadTask) -> tuple[str, int]:
        if "bad" in task.filename:
            raise ValueError("Corrupt file")
        return "doc-ok", 1

    queue = BulkQueue(processor=failing_processor, max_workers=1)
    queue.start()

    queue.enqueue(BulkUploadTask(
        task_id="t-bad", filename="bad.pdf", data=b"", title="Bad",
        owner_id=None, is_public=False, mime_type="application/pdf",
        allowed_roles=[], allowed_departments=[],
    ))
    queue.enqueue(BulkUploadTask(
        task_id="t-good", filename="good.txt", data=b"ok", title="Good",
        owner_id=None, is_public=False, mime_type="text/plain",
        allowed_roles=[], allowed_departments=[],
    ))

    for _ in range(50):
        s_bad = queue.get_status("t-bad")
        s_good = queue.get_status("t-good")
        if s_bad and s_bad.status in ("ready", "error") and s_good and s_good.status in ("ready", "error"):
            break
        await asyncio.sleep(0.02)

    await queue.stop()

    s_bad = queue.get_status("t-bad")
    s_good = queue.get_status("t-good")
    assert s_bad is not None and s_bad.status == "error"
    assert "Corrupt file" in (s_bad.error or "")
    assert s_good is not None and s_good.status == "ready"
    assert s_good.document_id == "doc-ok"
