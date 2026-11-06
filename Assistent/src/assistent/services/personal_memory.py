"""Explicit facts backed by LangGraph Store; summaries never become confirmed facts."""
import asyncio
import re
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from langgraph.store.postgres.aio import AsyncPostgresStore
from assistent.services.graph_builder import scope_key

MIGRATION_NAMESPACE = ('viio_migrations',)
MIGRATION_KEY = 'personal_facts_v1'


async def migrate_legacy_facts(store, rows):
    """Idempotent import preserving IDs, scopes and source metadata; no legacy deletion."""
    if await store.aget(MIGRATION_NAMESPACE, MIGRATION_KEY):
        return 0
    count = 0
    for row in rows:
        namespace = ('user_facts', str(row['owner_id']), row['access_scope'])
        identifier = str(row['id'])
        if await store.aget(namespace, identifier) is None:
            created = row['created_at']
            await store.aput(namespace, identifier, {
                'content': row['content'], 'source_thread': row['source_thread'],
                'created_at': created.isoformat() if hasattr(created, 'isoformat') else str(created),
            }, index=False)
            count += 1
    await store.aput(MIGRATION_NAMESPACE, MIGRATION_KEY, {'completed': True}, index=False)
    return count


class PersonalFacts:
    def __init__(self, catalog=None, *, store=None):
        self.catalog, self.store = catalog, store
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def _backend(self, owner=None):
        if self.store is not None:
            async with self._lock:
                yield self.store
            return
        async with self.catalog.pool.connection() as conn:
            async with conn.transaction():
                if owner is not None:
                    await conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s), 78234)', (str(owner),))
                async with AsyncPostgresStore(conn) as store:
                    yield store

    async def setup(self):
        # Store.setup requires autocommit; schema migration is an explicit deployment operation.
        async with self.catalog.pool.connection() as conn:
            async with AsyncPostgresStore(conn) as store:
                await store.setup()
            async with conn.transaction():
                await conn.execute('SELECT pg_advisory_xact_lock(78234, 1)')
                async with AsyncPostgresStore(conn) as store:
                    if await store.aget(MIGRATION_NAMESPACE, MIGRATION_KEY):
                        return 0
                    exists = await (await conn.execute("SELECT to_regclass('public.user_facts') AS legacy")).fetchone()
                    rows = []
                    if exists['legacy']:
                        rows = await (await conn.execute(
                            'SELECT id,owner_id,access_scope,content,source_thread,created_at FROM user_facts')).fetchall()
                    return await migrate_legacy_facts(store, rows)

    @staticmethod
    def namespace(context):
        if not context or not context.get('user_id'):
            raise PermissionError('Для работы с фактами необходима авторизация.')
        return ('user_facts', str(context['user_id']), scope_key(context))

    async def remember(self, context, thread, content):
        namespace = self.namespace(context)
        content = content.strip()
        if not content or len(content) > 2000:
            raise ValueError('Факт должен содержать от 1 до 2000 символов.')
        if re.search(r'(?i)(?:password|пароль|api[_ -]?key|токен|secret)\s*[:=]|-----BEGIN .*PRIVATE KEY|\bsk-[a-zA-Z0-9]{16,}', content):
            raise ValueError('Пароли, токены и ключи доступа не сохраняются в фактах.')
        async with self._backend(context['user_id']) as store:
            existing = await store.asearch(namespace, filter={'content': content}, limit=1)
            if existing:
                return existing[0].key
            all_facts = await store.asearch(namespace[:2], limit=100)
            if len(all_facts) >= 100:
                raise ValueError('Достигнут лимит 100 фактов. Удалите ненужные командой /forget.')
            identifier = str(uuid.uuid4())
            await store.aput(namespace, identifier, {
                'content': content, 'source_thread': thread,
                'created_at': datetime.now(timezone.utc).isoformat(),
            }, index=False)
            return identifier

    async def list(self, context):
        namespace = self.namespace(context)
        async with self._backend() as store:
            items = await store.asearch(namespace, limit=100)
        rows = [dict(item.value, id=item.key) for item in items]
        return sorted(rows, key=lambda row: row['created_at'], reverse=True)

    async def forget(self, context, identifier):
        namespace = self.namespace(context)
        try:
            identifier = str(uuid.UUID(identifier))
        except (ValueError, TypeError):
            return False
        async with self._backend(context['user_id']) as store:
            # Forget may delete the owner's fact from an earlier scope, never another user's.
            items = await store.asearch(namespace[:2], limit=100)
            found = next((item for item in items if item.key == identifier), None)
            if found is None:
                return False
            await store.adelete(found.namespace, identifier)
            if self.store is None:
                # Same connection/transaction: forgetting is atomic across both storage versions.
                conn = store.conn
                exists = await (await conn.execute("SELECT to_regclass('public.user_facts') AS legacy")).fetchone()
                if exists['legacy']:
                    await conn.execute('DELETE FROM user_facts WHERE id=%s AND owner_id=%s',
                                       (identifier, str(context['user_id'])))
        return True


class MemoryAgent:
    def __init__(self, agent, facts):
        self.agent, self.facts = agent, facts

    @property
    def checkpointer(self):
        return self.agent.checkpointer

    def set_checkpointer(self, saver):
        self.agent.set_checkpointer(saver)

    async def chat(self, message, thread_id=None, user_context=None):
        context = dict(user_context or {})
        context.pop('_user_notes', None)
        thread = thread_id or str(uuid.uuid4())
        command, _, argument = message.strip().partition(' ')
        command = {'запомни': '/remember', 'запомнить': '/remember', '/факты': '/facts', '/забыть': '/forget'}.get(command.lower(), command.lower())
        if command in {'/remember', '/facts', '/forget'}:
            if not context.get('user_id'):
                raise PermissionError('Для работы с фактами необходима авторизация.')
            if command == '/remember':
                identifier = await self.facts.remember(context, thread, argument)
                return f'Сохранён пользовательский факт. ID: {identifier}', thread
            if command == '/forget':
                removed = await self.facts.forget(context, argument.strip())
                return ('Факт удалён.' if removed else 'Факт не найден среди ваших записей.'), thread
            rows = await self.facts.list(context)
            return ('\n'.join(f"{row['id']}: {row['content']}" for row in rows) or 'Сохранённых фактов нет.'), thread
        if context.get('user_id'):
            try:
                rows = await self.facts.list(context)
                context['_user_notes'] = '\n'.join(row['content'] for row in rows[:20])[:4000]
            except Exception:
                # Optional notes must not take down normal chat during a DB outage.
                pass
        return await self.agent.chat(message, thread, context)
