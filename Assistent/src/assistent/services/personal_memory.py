"""Explicit facts backed by LangGraph Store; summaries never become confirmed facts."""
from collections import defaultdict
import asyncio
import logging
import re
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from langgraph.store.postgres.aio import AsyncPostgresStore
from assistent.services.graph_builder import scope_key
from assistent.services.diagnostics import record

logger = logging.getLogger(__name__)

MIGRATION_NAMESPACE = ('viio_migrations',)
MIGRATION_KEY = 'personal_facts_v1'

SECRET_PATTERN = re.compile(
    r'(?i)(?:password|пароль|api[_ -]?key|токен|secret)\s*[:=]|'
    r'-----BEGIN .*PRIVATE KEY|'
    r'\bsk-[a-zA-Z0-9]{16,}|'
    r'\bBearer\s+[A-Za-z0-9_\-\.]{20,}|'
    r'\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_\-]{10,}|'
    r'[a-zA-Z][a-zA-Z0-9+.-]*://[^:\s]+:[^@\s]+@[^/\s]+|'
    r'\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}|'
    r'\bgithub_pat_[A-Za-z0-9_]{50,}'
)

COMMAND_PATTERN = re.compile(r'(?is)^\s*(/?\w+)[\s,:]*(.*)')
COMMAND_ALIASES = {
    'запомни': '/remember',
    'запомнить': '/remember',
    '/remember': '/remember',
    'факты': '/facts',
    '/факты': '/facts',
    '/facts': '/facts',
    'забудь': '/forget',
    'забыть': '/forget',
    '/забыть': '/forget',
    '/forget': '/forget',
}


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
        self._user_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._global_lock = asyncio.Lock()

    def _get_lock(self, owner: Any) -> asyncio.Lock:
        if owner is None:
            return self._global_lock
        return self._user_locks[str(owner)]

    @asynccontextmanager
    async def _backend(self, owner=None):
        if self.store is not None:
            async with self._get_lock(owner):
                yield self.store
            return
        async with self.catalog.pool.connection() as conn:
            async with conn.transaction():
                if owner is not None:
                    await conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s), 78234)', (str(owner),))
                async with AsyncPostgresStore(conn) as store:
                    yield store

    async def setup(self):
        # Store.setup требует autocommit; миграция схемы — это явная операция развертывания.
        if self.catalog is None:
            if self.store is not None and hasattr(self.store, 'setup'):
                await self.store.setup()
            return 0
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
        if SECRET_PATTERN.search(content):
            raise ValueError('Пароли, токены и ключи доступа не сохраняются в фактах.')
        async with self._backend(context['user_id']) as store:
            existing = await store.asearch(namespace, filter={'content': content}, limit=1)
            if existing:
                return existing[0].key
            # Проверяем реальное количество фактов пользователя по страницам
            count = 0
            offset = 0
            while True:
                batch = await store.asearch(namespace[:2], limit=100, offset=offset)
                count += len(batch)
                if not batch or len(batch) < 100 or count >= 100:
                    break
                offset += len(batch)
            if count >= 100:
                raise ValueError('Достигнут лимит 100 фактов. Удалите ненужные командой /forget.')
            identifier = str(uuid.uuid4())
            await store.aput(namespace, identifier, {
                'content': content, 'source_thread': thread,
                'created_at': datetime.now(timezone.utc).isoformat(),
            }, index=False)
            return identifier

    async def list(self, context):
        namespace = self.namespace(context)
        async with self._backend(context.get('user_id')) as store:
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
            # Забывание может удалить факт владельца из более ранней области видимости, но никогда — факт другого пользователя.
            # Поиск через asearch с пагинацией, чтобы найти факт даже при >= 100 записях
            found = None
            offset = 0
            while True:
                items = await store.asearch(namespace[:2], limit=100, offset=offset)
                if not items:
                    break
                for item in items:
                    if item.key == identifier:
                        found = item
                        break
                if found is not None or len(items) < 100:
                    break
                offset += len(items)

            if found is None:
                return False
            await store.adelete(found.namespace, identifier)
        return True


class MemoryAgent:
    def __init__(self, agent, facts, project_memory=None):
        self.agent, self.facts = agent, facts
        self.project_memory = project_memory

    @property
    def checkpointer(self):
        return self.agent.checkpointer

    def set_checkpointer(self, saver):
        self.agent.set_checkpointer(saver)

    async def chat(self, message, thread_id=None, user_context=None):
        context = dict(user_context or {})
        context.pop('_user_notes', None)
        thread = thread_id or str(uuid.uuid4())
        
        match = COMMAND_PATTERN.match(message)
        command, argument = '', ''
        if match:
            raw_cmd, raw_arg = match.groups()
            command = COMMAND_ALIASES.get(raw_cmd.lower(), raw_cmd.lower())
            argument = raw_arg.strip()

        if self.project_memory and command in self.project_memory.commands:
            return await self.project_memory.handle(command, argument, context), thread
        if command in {'/remember', '/facts', '/forget'}:
            if not context.get('user_id'):
                raise PermissionError('Для работы с фактами необходима авторизация.')
            if command == '/remember':
                identifier = await self.facts.remember(context, thread, argument)
                return f'Сохранён пользовательский факт. ID: {identifier}', thread
            if command == '/forget':
                removed = await self.facts.forget(context, argument)
                return ('Факт удалён.' if removed else 'Факт не найден среди ваших записей.'), thread
            rows = await self.facts.list(context)
            return ('\n'.join(f"{row['id']}: {row['content']}" for row in rows) or 'Сохранённых фактов нет.'), thread
        if context.get('user_id'):
            try:
                rows = await self.facts.list(context)
                context['_user_notes'] = '\n'.join(row['content'] for row in rows[:20])[:4000]
            except Exception as error:
                # Необязательные заметки не должны ломать обычный чат при сбое БД, но ошибка фиксируется в телеметрии
                logger.warning("Facts retrieval failed: %s", error)
                record('facts_unavailable', error=type(error).__name__)
        return await self.agent.chat(message, thread, context)

