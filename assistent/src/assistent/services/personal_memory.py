"""Explicit user notes; summaries never silently become confirmed facts."""
import re
import uuid
from psycopg.rows import dict_row
from assistent.services.graph_builder import scope_key

MEMORY_DDL = """
CREATE TABLE IF NOT EXISTS user_facts (
 id uuid PRIMARY KEY, owner_id text NOT NULL, access_scope text NOT NULL,
 content text NOT NULL, source_thread text NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS user_facts_owner_scope ON user_facts(owner_id,access_scope,created_at DESC);
"""


class PersonalFacts:
    def __init__(self, catalog):
        self.catalog = catalog

    async def setup(self):
        async with self.catalog.pool.connection() as conn:
            await conn.execute(MEMORY_DDL)

    async def remember(self, context, thread, content):
        content = content.strip()
        if not content or len(content) > 2000:
            raise ValueError('Факт должен содержать от 1 до 2000 символов.')
        if re.search(r'(?i)(?:password|пароль|api[_ -]?key|токен|secret)\s*[:=]|-----BEGIN .*PRIVATE KEY|\bsk-[a-zA-Z0-9]{16,}', content):
            raise ValueError('Пароли, токены и ключи доступа не сохраняются в фактах.')
        identifier = str(uuid.uuid4())
        async with self.catalog.pool.connection() as conn:
            await conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s), 78234)', (str(context['user_id']),))
            existing = await (await conn.execute('SELECT id FROM user_facts WHERE owner_id=%s AND access_scope=%s AND content=%s',
                (str(context['user_id']), scope_key(context), content))).fetchone()
            if existing:
                return str(existing['id'] if isinstance(existing, dict) else existing[0])
            count = await (await conn.execute('SELECT count(*) FROM user_facts WHERE owner_id=%s', (str(context['user_id']),))).fetchone()
            if (count['count'] if isinstance(count, dict) else count[0]) >= 100:
                raise ValueError('Достигнут лимит 100 фактов. Удалите ненужные командой /forget.')
            await conn.execute('INSERT INTO user_facts(id,owner_id,access_scope,content,source_thread) VALUES (%s,%s,%s,%s,%s)',
                (identifier, str(context['user_id']), scope_key(context), content, thread))
        return identifier

    async def list(self, context):
        async with self.catalog.pool.connection() as conn:
            conn.row_factory = dict_row
            return await (await conn.execute('SELECT id::text,content,source_thread,created_at FROM user_facts WHERE owner_id=%s AND access_scope=%s ORDER BY created_at DESC LIMIT 100',
                (str(context['user_id']), scope_key(context)))).fetchall()

    async def forget(self, context, identifier):
        try:
            identifier = uuid.UUID(identifier)
        except ValueError:
            return False
        async with self.catalog.pool.connection() as conn:
            row = await (await conn.execute('DELETE FROM user_facts WHERE id=%s AND owner_id=%s RETURNING id', (identifier, str(context['user_id'])))).fetchone()
            return bool(row)


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
