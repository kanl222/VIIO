"""Canonical searchable text and explicitly approved external snapshots."""
import re
import uuid

from psycopg.rows import dict_row
from langchain_core.documents import Document

KNOWLEDGE_DDL = """
CREATE TABLE IF NOT EXISTS document_contents (
 document_id varchar(64) PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
 content text NOT NULL,
 filename text NOT NULL DEFAULT '',
 title text NOT NULL DEFAULT '',
 search_vector tsvector GENERATED ALWAYS AS
   (to_tsvector('russian',title || ' ' || filename || ' ' || content) || to_tsvector('simple',title || ' ' || filename || ' ' || content)) STORED
);
CREATE INDEX IF NOT EXISTS document_contents_search ON document_contents USING gin(search_vector);
CREATE TABLE IF NOT EXISTS document_publications (
 id uuid PRIMARY KEY,
 document_id varchar(64) UNIQUE NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
 title text NOT NULL, content text NOT NULL, publisher_id text NOT NULL,
 published_at timestamptz NOT NULL DEFAULT now(),
 search_vector tsvector GENERATED ALWAYS AS
   (to_tsvector('russian',title || ' ' || content) || to_tsvector('simple',title || ' ' || content)) STORED
);
CREATE INDEX IF NOT EXISTS document_publications_search ON document_publications USING gin(search_vector);
"""


def query_terms(query):
    stop = {'что', 'как', 'для', 'это', 'про', 'или', 'нужно', 'the', 'and', 'with', 'какие', 'такое'}
    return list(dict.fromkeys(word.lower() for word in re.findall(r'[\w]{3,}', query)
                             if word.lower() not in stop))[:20]


def tsquery(query):
    # Only letters/digits/underscores reach the tsquery grammar, never user syntax.
    return ' | '.join(term + ':*' for term in query_terms(query))


def excerpt(content, query, limit=2400):
    positions = [content.lower().find(term) for term in query_terms(query)]
    start = max(0, min((pos for pos in positions if pos >= 0), default=0) - 300)
    return content[start:start + limit], content[:start].count('\n') + 1


class KnowledgeData:
    def __init__(self, catalog):
        self.catalog = catalog

    async def save_text(self, identifier, content, filename, title=''):
        async with self.catalog.pool.connection() as conn:
            await conn.execute('''INSERT INTO document_contents(document_id,content,filename,title) VALUES (%s,%s,%s,%s)
                ON CONFLICT(document_id) DO UPDATE SET content=EXCLUDED.content,filename=EXCLUDED.filename,title=EXCLUDED.title''',
                (identifier, content, filename, title))

    async def has_text(self, identifier):
        async with self.catalog.pool.connection() as conn:
            return bool(await (await conn.execute('SELECT 1 FROM document_contents WHERE document_id=%s', (identifier,))).fetchone())

    async def lexical(self, query, context, limit=15):
        expression = tsquery(query)
        if not expression:
            return []
        context = context or {}
        role = str(context.get('role', 'guest')).lower()
        roles = [role, 'employee'] if role == 'programmer' else [role]
        async with self.catalog.pool.connection() as conn:
            conn.row_factory = dict_row
            rows = await (await conn.execute('''SELECT d.*,c.content,c.filename FROM document_contents c
                JOIN documents d ON d.id=c.document_id
                WHERE d.is_active AND d.status IN ('ready','indexed')
                AND (d.document_kind<>'code' OR %s)
                AND (%s OR d.is_public OR d.allowed_roles && %s OR d.allowed_departments && %s OR d.owner_id=%s)
                AND c.search_vector @@ to_tsquery('russian',%s)
                ORDER BY ts_rank_cd(c.search_vector,to_tsquery('russian',%s)) DESC LIMIT %s''',
                (role in {'programmer', 'admin'}, role == 'admin', roles, context.get('departments') or [],
                 str(context.get('user_id', '')), expression, expression, limit))).fetchall()
        results = []
        for row in rows:
            text, line = excerpt(row['content'], query)
            results.append(Document(page_content=text, metadata={'document_id': row['id'], 'title': row['title'],
                'kind': row['document_kind'], 'filename': row['filename'], 'line_start': line,
                'line_end': line + text.count('\n'), 'chunk_index': -1}))
        return results

    async def publication(self, identifier):
        async with self.catalog.pool.connection() as conn:
            conn.row_factory = dict_row
            return await (await conn.execute('SELECT id::text,title,content,published_at FROM document_publications WHERE document_id=%s', (identifier,))).fetchone()

    async def publish(self, identifier, title, content, publisher):
        async with self.catalog.pool.connection() as conn:
            conn.row_factory = dict_row
            row = await (await conn.execute('SELECT * FROM documents WHERE id=%s FOR UPDATE', (identifier,))).fetchone()
            if not row or not row['is_active'] or row['status'] not in {'ready', 'indexed'} or row['document_kind'] == 'code':
                raise ValueError('Публиковать можно только актуальный готовый документ, кроме исходного кода.')
            await conn.execute('''INSERT INTO document_publications(id,document_id,title,content,publisher_id)
                VALUES (%s,%s,%s,%s,%s) ON CONFLICT(document_id) DO UPDATE
                SET id=EXCLUDED.id,title=EXCLUDED.title,content=EXCLUDED.content,publisher_id=EXCLUDED.publisher_id,published_at=now()''',
                (uuid.uuid4(), identifier, title, content, publisher))

    async def withdraw(self, identifier):
        async with self.catalog.pool.connection() as conn:
            await conn.execute('DELETE FROM document_publications WHERE document_id=%s', (identifier,))

    async def public_search(self, query):
        expression = tsquery(query)
        if not expression:
            return []
        async with self.catalog.pool.connection() as conn:
            conn.row_factory = dict_row
            return await (await conn.execute('''SELECT p.id::text,p.title,p.content FROM document_publications p
                JOIN documents d ON d.id=p.document_id WHERE d.is_active AND d.status IN ('ready','indexed')
                AND d.document_kind<>'code' AND p.search_vector @@ to_tsquery('russian',%s)
                ORDER BY ts_rank_cd(p.search_vector,to_tsquery('russian',%s)) DESC LIMIT 4''',
                (expression, expression))).fetchall()

    async def public_ids(self, identifiers):
        async with self.catalog.pool.connection() as conn:
            conn.row_factory = dict_row
            rows = await (await conn.execute('''SELECT p.id::text FROM document_publications p JOIN documents d
                ON d.id=p.document_id WHERE p.id::text=ANY(%s) AND d.is_active
                AND d.status IN ('ready','indexed') AND d.document_kind<>'code' ''', (identifiers,))).fetchall()
            return {row['id'] for row in rows}
