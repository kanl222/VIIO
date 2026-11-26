"""Uploaded bytes live on disk; PostgreSQL owns task state and recovery."""

import asyncio
import logging
from dataclasses import asdict
from functools import partial
import anyio.to_thread

from viio_documents.application.bulk_queue import BulkUploadTask, BulkUploadResult, PreparedReview, DocumentVersionConflict
from viio_documents.application.upload_jobs import UploadJobs
from viio_documents.infrastructure.extraction.office_extraction import OfficeExtractionError
from viio_documents.infrastructure.extraction.pdf_extraction import PdfExtractionError

logger = logging.getLogger(__name__)


class PersistentBulkQueue:
    def __init__(self, processor, catalog, store, max_workers=1, processing_slots=None):
        self.processor = processor
        self.catalog = catalog
        self.store = store
        self.jobs = UploadJobs(catalog)
        self.max_workers = max_workers
        self.workers = []
        self.listener_task = None
        self._notify_event = asyncio.Event()
        self.processing_slots = processing_slots

    async def setup(self):
        await self.jobs.setup()

    def start(self):
        if not self.workers:
            self.workers = [asyncio.create_task(self._worker()) for _ in range(self.max_workers)]
        if self.listener_task is None or self.listener_task.done():
            self.listener_task = asyncio.create_task(self._listener())

    async def stop(self):
        if self.listener_task:
            self.listener_task.cancel()
            try:
                await self.listener_task
            except (asyncio.CancelledError, Exception):
                pass
            self.listener_task = None
        for worker in self.workers:
            worker.cancel()
        await asyncio.gather(*self.workers, return_exceptions=True)
        self.workers = []

    async def enqueue(self, task):
        suffix = '.' + task.filename.rsplit('.', 1)[-1].lower()
        key = await anyio.to_thread.run_sync(partial(self.store.save, task.data, suffix=suffix))
        payload = asdict(task)
        payload.pop('data')
        payload['storage_key'] = key
        try:
            await self.jobs.submit(task.task_id, payload)
            self._notify_event.set()
        except BaseException:
            await anyio.to_thread.run_sync(self.store.delete, key)
            raise
        return BulkUploadResult(task.task_id, task.filename, 'queued')

    async def get_statuses(self, identifiers):
        rows = await self.jobs.statuses(identifiers) if identifiers else await self.jobs.recent()
        return [BulkUploadResult(row['id'], row['payload']['filename'], row['status'], row['document_id'], row['chunks_count'], row['error']) for row in rows]

    async def retry(self, identifier):
        retried = await self.jobs.retry(identifier)
        if retried:
            self._notify_event.set()
        return retried

    async def cancel(self, identifier):
        return await self.jobs.cancel(identifier)

    async def get_review(self, identifier):
        return await self.jobs.get_review(identifier)

    async def approve(self, identifier, review_token, content, reviewer_id):
        approved = await self.jobs.approve(identifier, review_token, content, reviewer_id)
        if approved:
            self._notify_event.set()
        return approved

    async def _keep_lease(self, row):
        while True:
            await asyncio.sleep(20)
            if not await self.jobs.heartbeat(row['id'], row['claim_token']):
                raise RuntimeError('Потеряно право обработки задания')

    async def _process(self, row):
        if self.processing_slots is not None:
            async with self.processing_slots:
                return await self._process_unlimited(row)
        return await self._process_unlimited(row)

    async def _process_unlimited(self, row):
        payload = dict(row['payload'])
        key = payload.pop('storage_key')
        data = await anyio.to_thread.run_sync(self.store.load, key)
        task = BulkUploadTask(data=data, **payload)
        if task.mime_type == 'application/pdf':
            review = row.get('review') or {}
            if not review.get('approved'):
                return await self.processor.prepare_pdf(task)
            task.reviewed_content = review['content']
            task.metadata.update({'extraction_engine': review['engine'], 'pages': review['pages'],
                                  'reviewed_by': review['approved_by'], 'reviewed_at': review['approved_at']})
        return await self.processor(task, key)

    async def _work(self, row):
        # Аренда (lease) и обработка разделяют границу сбоя: замерший heartbeat
        # должен отменять обработку, а не позволять второму воркеру перехватить её.
        heartbeat = asyncio.create_task(self._keep_lease(row))
        process = asyncio.create_task(self._process(row))
        try:
            done, _ = await asyncio.wait([heartbeat, process], return_when=asyncio.FIRST_COMPLETED)
            if heartbeat in done:
                await heartbeat
            result = await process
            if isinstance(result, PreparedReview):
                await self.jobs.require_review(row['id'], row['claim_token'], result)
                return
            document_id, chunks = result
            await self.jobs.finish(row['id'], row['claim_token'], status='ready', document_id=document_id, chunks_count=chunks)
        except asyncio.CancelledError:
            process.cancel()
            heartbeat.cancel()
            await asyncio.gather(heartbeat, process, return_exceptions=True)
            await self.jobs.release(row['id'], row['claim_token'])
            raise
        except Exception as error:
            logger.exception('Ошибка обработки загрузки %s', row['id'])
            message = str(error) if isinstance(error, (DocumentVersionConflict, OfficeExtractionError, PdfExtractionError)) else 'Обработка не завершена. Повторите загрузку или обратитесь к администратору.'
            await self.jobs.finish(row['id'], row['claim_token'], status='error', error=message)
        finally:
            heartbeat.cancel()
            process.cancel()
            await asyncio.gather(heartbeat, process, return_exceptions=True)

    async def _listener(self):
        while True:
            try:
                if not getattr(self.catalog, "available", False):
                    await asyncio.sleep(1)
                    continue
                async with self.catalog.pool.connection() as conn:
                    conn.autocommit = True
                    await conn.execute("LISTEN document_upload_jobs")
                    async for _ in conn.notifies():
                        self._notify_event.set()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.debug("Сбой соединения в слушателе NOTIFY; повтор через 3с")
                await asyncio.sleep(3)

    async def _worker(self):
        while True:
            try:
                row = await self.jobs.claim()
                if row:
                    await self._work(row)
                    continue
                try:
                    await asyncio.wait_for(self._notify_event.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    pass
                finally:
                    self._notify_event.clear()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception('Ошибка очереди загрузки; повтор подключения')
                await asyncio.sleep(2)
