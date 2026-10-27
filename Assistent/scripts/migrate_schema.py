"""Apply agent checkpoint/facts migrations under deployment credentials."""
import asyncio
from types import SimpleNamespace
from psycopg.conninfo import make_conninfo
from assistent.config import Settings
from assistent.services.personal_memory import PersonalFacts
from assistent.utils.postgres_saver import init_async_postgres_saver

async def main():
    s=Settings()
    _,pool=await init_async_postgres_saver(make_conninfo(host=s.postgres_host,port=s.postgres_port,
        dbname=s.postgres_db,user=s.postgres_user,password=s.postgres_password))
    try:await PersonalFacts(SimpleNamespace(pool=pool)).setup()
    finally:await pool.close()
    print('Agent schema migrated; no model calls started')

if __name__=='__main__':asyncio.run(main())
