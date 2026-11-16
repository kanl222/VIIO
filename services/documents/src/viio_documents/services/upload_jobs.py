"""Durable upload journal and atomic claims in the existing PostgreSQL."""

import uuid
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

JOBS_DDL = """
CREATE TABLE IF NOT EXISTS document_upload_jobs (
    id text PRIMARY KEY,
    payload jsonb NOT NULL,
    status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','processing','needs_review','ready','error','cancelled')),
    document_id text,
    chunks_count integer,
    error text,
    claim_token text,
    lease_until timestamptz,
    attempts integer NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS document_jobs_pending ON document_upload_jobs(created_at)
WHERE status IN ('queued','processing');
"""


class UploadJobs:
    def __init__(self, catalog):
        self.catalog = catalog

    async def setup(self):
        async with self.catalog.pool.connection() as conn:
            # Сериализуем аддитивные миграции при одновременном запуске нескольких процессов приложения.
            await conn.execute('SELECT pg_advisory_xact_lock(hashtext(current_schema()), 78231)')
            await conn.execute("SET LOCAL lock_timeout = '5s'")
            await conn.execute(JOBS_DDL)
            await conn.execute('ALTER TABLE document_upload_jobs ADD COLUMN IF NOT EXISTS review jsonb')
            await conn.execute('ALTER TABLE document_upload_jobs DROP CONSTRAINT IF EXISTS document_upload_jobs_status_check')
            await conn.execute("ALTER TABLE document_upload_jobs ADD CONSTRAINT document_upload_jobs_status_check CHECK (status IN ('queued','processing','needs_review','ready','error','cancelled'))")

    async def submit(self, identifier, payload):
        async with self.catalog.pool.connection() as conn:
            await conn.execute("INSERT INTO document_upload_jobs(id,payload) VALUES (%s,%s)", (identifier, Jsonb(payload)))
            await conn.execute("NOTIFY document_upload_jobs")

    async def claim(self):
        token = uuid.uuid4().hex
        async with self.catalog.pool.connection() as conn:
            conn.row_factory = dict_row
            cursor = await conn.execute("""
                UPDATE document_upload_jobs SET status='processing',claim_token=%s,
                    lease_until=now()+interval '90 seconds',attempts=attempts+1,updated_at=now()
                WHERE id=(SELECT id FROM document_upload_jobs
                    WHERE status='queued' OR (status='processing' AND lease_until<now())
                    ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED)
                RETURNING *
            """, (token,))
            return await cursor.fetchone()

    async def heartbeat(self, identifier, token):
        async with self.catalog.pool.connection() as conn:
            cursor = await conn.execute("UPDATE document_upload_jobs SET lease_until=now()+interval '90 seconds' WHERE id=%s AND claim_token=%s AND status='processing' RETURNING id", (identifier, token))
            return bool(await cursor.fetchone())

    async def finish(self, identifier, token, *, status, document_id=None, chunks_count=None, error=None):
        async with self.catalog.pool.connection() as conn:
            await conn.execute("""UPDATE document_upload_jobs SET status=%s,document_id=%s,chunks_count=%s,
                error=%s,claim_token=NULL,lease_until=NULL,updated_at=now()
                WHERE id=%s AND claim_token=%s""", (status, document_id, chunks_count, error, identifier, token))

    async def release(self, identifier, token):
        async with self.catalog.pool.connection() as conn:
            await conn.execute("UPDATE document_upload_jobs SET status='queued',claim_token=NULL,lease_until=NULL WHERE id=%s AND claim_token=%s", (identifier, token))
            await conn.execute("NOTIFY document_upload_jobs")

    async def statuses(self, identifiers):
        async with self.catalog.pool.connection() as conn:
            conn.row_factory = dict_row
            cursor = await conn.execute("SELECT id,payload,status,document_id,chunks_count,error FROM document_upload_jobs WHERE id=ANY(%s)", (identifiers,))
            return await cursor.fetchall()

    async def recent(self):
        async with self.catalog.pool.connection() as conn:
            conn.row_factory = dict_row
            cursor = await conn.execute('SELECT id,payload,status,document_id,chunks_count,error FROM document_upload_jobs ORDER BY created_at DESC LIMIT 100')
            return await cursor.fetchall()

    async def retry(self, identifier):
        async with self.catalog.pool.connection() as conn:
            cursor = await conn.execute("UPDATE document_upload_jobs SET status='queued',error=NULL,updated_at=now() WHERE id=%s AND status='error' RETURNING id", (identifier,))
            retried = bool(await cursor.fetchone())
            if retried:
                await conn.execute("NOTIFY document_upload_jobs")
            return retried

    async def require_review(self, identifier, token, prepared):
        review = {'content': prepared.content, 'engine': prepared.engine, 'pages': prepared.pages,
                  'token': uuid.uuid4().hex, 'approved': False,
                  'document': prepared.document}
        async with self.catalog.pool.connection() as conn:
            await conn.execute("""UPDATE document_upload_jobs SET status='needs_review',review=%s,
                claim_token=NULL,lease_until=NULL,updated_at=now()
                WHERE id=%s AND claim_token=%s AND status='processing'""", (Jsonb(review), identifier, token))

    async def get_review(self, identifier):
        async with self.catalog.pool.connection() as conn:
            conn.row_factory = dict_row
            cursor = await conn.execute("SELECT payload->>'filename' AS filename,review FROM document_upload_jobs WHERE id=%s AND status='needs_review'", (identifier,))
            return await cursor.fetchone()

    async def approve(self, identifier, review_token, content, reviewer_id):
        async with self.catalog.pool.connection() as conn:
            cursor = await conn.execute("""UPDATE document_upload_jobs SET status='queued',
                review=review || %s || jsonb_build_object('approved_at',now()),updated_at=now()
                WHERE id=%s AND status='needs_review' AND review->>'token'=%s RETURNING id""",
                (Jsonb({'content': content, 'approved': True, 'approved_by': reviewer_id}), identifier, review_token))
            approved = bool(await cursor.fetchone())
            if approved:
                await conn.execute("NOTIFY document_upload_jobs")
            return approved

    async def cancel(self, identifier):
        async with self.catalog.pool.connection() as conn:
            cursor = await conn.execute("""UPDATE document_upload_jobs SET status='cancelled',updated_at=now()
                WHERE id=%s AND status IN ('queued','needs_review') RETURNING id""", (identifier,))
            return bool(await cursor.fetchone())
