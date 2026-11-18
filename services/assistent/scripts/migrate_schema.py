"""Apply agent checkpoint/facts migrations under deployment credentials."""
import asyncio
from types import SimpleNamespace
from psycopg.conninfo import make_conninfo
from assistent.config import Settings
from assistent.utils.postgres_saver import init_async_postgres_saver

MEMORY_STATEMENTS = [
    """CREATE TABLE IF NOT EXISTS user_facts (
        id uuid PRIMARY KEY,
        owner_id text NOT NULL,
        access_scope text NOT NULL,
        content text NOT NULL,
        source_thread text NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now()
    )""",
    """CREATE INDEX IF NOT EXISTS user_facts_owner_scope ON user_facts(owner_id, access_scope, created_at DESC)"""
]

async def setup_facts(pool):
    async with pool.connection() as conn:
        for stmt in MEMORY_STATEMENTS:
            await conn.execute(stmt)

async def main():
    s = Settings()
    _, pool = await init_async_postgres_saver(make_conninfo(host=s.postgres_host, port=s.postgres_port,
        dbname=s.postgres_db, user=s.postgres_user, password=s.postgres_password))
    try:
        await setup_facts(pool)
    finally:
        await pool.close()
    print('Agent schema migrated; no model calls started')

if __name__ == '__main__':
    import sys
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
