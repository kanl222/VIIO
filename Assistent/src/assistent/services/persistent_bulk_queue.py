"""Uploaded bytes live on disk; PostgreSQL owns task state and recovery."""

import asyncio
import logging
from dataclasses import asdict
from functools import partial
import anyio.to_thread

from assistent.services.bulk_queue import BulkUploadTask, BulkUploadResult, PreparedReview, DocumentVersionConflict
from assistent.services.upload_jobs import UploadJobs
from assistent.services.office_extraction import OfficeExtractionError

logger = logging.getLogger(__name__)


class PersistentBulkQueue:
    def __init__(self, processor, catalog, store, max_workers=1, processing_slots=None):
        self.processor = processor
        self.store = store
        self.jobs = UploadJobs(catalog)
        self.max_workers = max_workers
        self.workers = []
        self.processing_slots = processing_slots

    async def setup(self):
        await self.jobs.setup()

    def start(self):
        if not self.workers:
            self.workers = [asyncio.create_task(self._worker()) for _ in range(self.max_workers)]

    async def stop(self):
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
        except BaseException:
            await anyio.to_thread.run_sync(self.store.delete, key)
            raise
        return BulkUploadResult(task.task_id, task.filename, 'queued')

    async def get_statuses(self, identifiers):
        rows = await self.jobs.statuses(identifiers) if identifiers else await self.jobs.recent()
        return [BulkUploadResult(row['id'], row['payload']['filename'], row['status'], row['document_id'], row['chunks_count'], row['error']) for row in rows]

    async def retry(self, identifier):
        return await self.jobs.retry(identifier)

    async def cancel(self, identifier):
        return await self.jobs.cancel(identifier)

    async def get_review(self, identifier):
        return await self.jobs.get_review(identifier)

    async def approve(self, identifier, review_token, content, reviewer_id):
        return await self.jobs.approve(identifier, review_token, content, reviewer_id)

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
        # The lease and processing share a failure boundary: a dead heartbeat
        # must cancel processing rather than letting a second worker take over.
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
            message = str(error) if isinstance(error, (DocumentVersionConflict, OfficeExtractionError)) else 'Обработка не завершена. Повторите загрузку или обратитесь к администратору.'
            await self.jobs.finish(row['id'], row['claim_token'], status='error', error=message)
        finally:
            heartbeat.cancel()
            process.cancel()
            await asyncio.gather(heartbeat, process, return_exceptions=True)

    async def _worker(self):
        while True:
            try:
                row = await self.jobs.claim()
                if row:
                    await self._work(row)
                else:
                    await asyncio.sleep(0.5)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception('Ошибка очереди загрузки; повтор подключения')
                await asyncio.sleep(2)
