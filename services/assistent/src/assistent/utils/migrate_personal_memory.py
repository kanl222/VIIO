"""Run once with a schema migration role: python -m assistent.utils.migrate_personal_memory."""

import asyncio
from types import SimpleNamespace

from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from assistent.config import Settings
from assistent.services.personal_memory import PersonalFacts


async def main():
    settings = Settings()
    dsn = make_conninfo(host=settings.postgres_host, port=settings.postgres_port,
        dbname=settings.postgres_db, user=settings.postgres_user, password=settings.postgres_password)
    async with AsyncConnectionPool(dsn, open=False, kwargs={
            'row_factory': dict_row, 'autocommit': True}) as pool:
        count = await PersonalFacts(SimpleNamespace(pool=pool)).setup()
        print(f'Personal facts migration complete: {count} imported.')


if __name__ == '__main__':
    asyncio.run(main())
