"""Canonical searchable text and explicitly approved external snapshots."""
import re
import math
from bisect import bisect_left
import uuid
import logging

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


def excerpt(content, query, limit=4000):
    """Choose a bounded window covering distinctive terms, not the first match.

    A small Cyrillic prefix tolerance matches список/списка and отчёт/отчета.
    It changes only excerpt selection; PostgreSQL still ranks/access-checks docs.
    """
    text = content.casefold().replace('ё', 'е')
    terms = [term.casefold().replace('ё', 'е') for term in query_terms(query)]
    terms = list(dict.fromkeys(term[:-2] if len(term) > 5 and re.fullmatch('[а-я]+', term) else term for term in terms))
    positions = [list(match.start() for match in re.finditer(re.escape(term), text)) for term in terms]
    weights = [1 / math.sqrt(len(found)) if found else 0 for found in positions]
    # Bound candidate count even for very repetitive source documents.
    starts = {0}
    for found in positions:
        stride = max(1, len(found) // 100)
        starts.update(max(0, position - 300) for position in found[::stride][:100])
    def score(start):
        return sum(weight for weight, found in zip(weights, positions)
                   if bisect_left(found, start) < bisect_left(found, start + limit))
    start = max(sorted(starts), key=score)
    return content[start:start + limit], content[:start].count('\n') + 1


class KnowledgeData:
    def __init__(self, catalog, public_index=None):
        self.catalog = catalog
        self.public_index = public_index

    async def sync_public_index(self):
        """Repair interrupted writes and remove revoked snapshots across workers."""
        if self.public_index is None:
            return
        async with self.catalog.pool.connection() as conn:
            conn.row_factory = dict_row
            await conn.execute('SELECT pg_advisory_xact_lock(76100321)')
            rows = await (await conn.execute('''SELECT p.id::text,p.document_id,p.title,p.content
                FROM document_publications p JOIN documents d ON d.id=p.document_id
                WHERE d.is_active AND d.status IN ('ready','indexed') AND d.document_kind<>'code' ''')).fetchall()
            inventory = await self.public_index.inventory()
            for row in rows:
                chunks = inventory.get(row['id'], [])
                if not chunks or any(m.get('snapshot_hash') != self.public_index.digest(row)
                    or not m.get('ingestion_ready') or m.get('expected_chunks') != len(chunks) for m in chunks):
                    await self.public_index.put(row)
            for identifier in inventory.keys() - {row['id'] for row in rows}:
                if identifier:
                    await self.public_index.remove(identifier)

    async def public_vectors(self, query, k=24):
        if self.public_index is None:
            return []
        docs = await self.public_index.search(query, k)
        valid = await self.public_ids(list({d.metadata.get('publication_id', '') for d in docs}))
        result = []
        for doc in docs:
            if doc.metadata.get('publication_id') in valid:
                doc.metadata['document_id'] = doc.metadata['source_document_id']
                result.append(doc)
        return result

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
            await conn.execute('SELECT pg_advisory_xact_lock(76100321)')
            row = await (await conn.execute('SELECT * FROM documents WHERE id=%s FOR UPDATE', (identifier,))).fetchone()
            if not row or not row['is_active'] or row['status'] not in {'ready', 'indexed'} or row['document_kind'] == 'code':
                raise ValueError('Публиковать можно только актуальный готовый документ, кроме исходного кода.')
            previous = await (await conn.execute('SELECT id::text FROM document_publications WHERE document_id=%s', (identifier,))).fetchone()
            publication_id = str(uuid.uuid4())
            if self.public_index is not None:
                await self.public_index.put(dict(id=publication_id, document_id=identifier, title=title, content=content))
            await conn.execute('''INSERT INTO document_publications(id,document_id,title,content,publisher_id)
                VALUES (%s,%s,%s,%s,%s) ON CONFLICT(document_id) DO UPDATE
                SET id=EXCLUDED.id,title=EXCLUDED.title,content=EXCLUDED.content,publisher_id=EXCLUDED.publisher_id,published_at=now()''',
                (publication_id, identifier, title, content, publisher))
        if previous and self.public_index is not None:
            try:
                await self.public_index.remove(previous['id'])
            except Exception:
                logging.getLogger(__name__).warning('Previous publication cleanup deferred to reconciliation')

    async def withdraw(self, identifier):
        async with self.catalog.pool.connection() as conn:
            conn.row_factory = dict_row
            await conn.execute('SELECT pg_advisory_xact_lock(76100321)')
            row = await (await conn.execute('DELETE FROM document_publications WHERE document_id=%s RETURNING id::text', (identifier,))).fetchone()
        if row and self.public_index is not None:
            try:
                await self.public_index.remove(row['id'])
            except Exception:
                logging.getLogger(__name__).warning('Publication cleanup deferred to reconciliation')

    async def public_search(self, query):
        if self.public_index is not None:
            from apiagent.services.enhanced_retrieval import rank_documents
            docs = rank_documents(query, await self.public_vectors(query, 40), 40)
            selected = []
            for doc in docs:
                identifier = doc.metadata['publication_id']
                if identifier not in selected and len(selected) < 4:
                    selected.append(identifier)
            # Retrieve the approved snapshot, never the private source. The consultant
            # selects a coherent bounded excerpt itself; concatenating vector hits
            # can omit a prerequisite or join unrelated sections of a long manual.
            async with self.catalog.pool.connection() as conn:
                conn.row_factory = dict_row
                rows = await (await conn.execute('''SELECT p.id::text,p.title,p.content
                    FROM document_publications p JOIN documents d ON d.id=p.document_id
                    WHERE p.id::text=ANY(%s) AND d.is_active AND d.status IN ('ready','indexed')
                    AND d.document_kind<>'code' ''', (selected,))).fetchall()
            by_id = {row['id']: row for row in rows}
            return [by_id[identifier] for identifier in selected if identifier in by_id]
        expression = tsquery(query)
        if not expression:
            return []
        async with self.catalog.pool.connection() as conn:
            conn.row_factory = dict_row
            return await (await conn.execute('''SELECT p.id::text,p.title,p.content FROM document_publications p
                JOIN documents d ON d.id=p.document_id WHERE d.is_active AND d.status IN ('ready','indexed')
                AND d.document_kind<>'code' AND p.search_vector @@ to_tsquery('russian',%s)
                ORDER BY (8 * ts_rank_cd(to_tsvector('russian',p.title),to_tsquery('russian',%s),32)
                    + ts_rank_cd(p.search_vector,to_tsquery('russian',%s),32)) DESC LIMIT 4''',
                (expression, expression, expression))).fetchall()

    async def public_ids(self, identifiers):
        async with self.catalog.pool.connection() as conn:
            conn.row_factory = dict_row
            rows = await (await conn.execute('''SELECT p.id::text FROM document_publications p JOIN documents d
                ON d.id=p.document_id WHERE p.id::text=ANY(%s) AND d.is_active
                AND d.status IN ('ready','indexed') AND d.document_kind<>'code' ''', (identifiers,))).fetchall()
            return {row['id'] for row in rows}
