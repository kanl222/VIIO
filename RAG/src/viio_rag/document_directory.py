"""Read document metadata and selected document text with current catalog ACLs."""
from urllib.parse import quote

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from langchain_core.documents import Document

from viio_rag.access import can_read_document
from viio_rag.records import DocumentRecord
from viio_rag.knowledge_data import excerpt


async def list_documents(data, request):
    context = request.user_context.model_dump() if request.user_context else {}
    role = str(context.get('role', 'guest')).lower()
    roles = [role, 'employee'] if role == 'programmer' else [role]
    async with data.catalog.pool.connection() as conn:
        conn.row_factory = dict_row
        row = await (await conn.execute("""
            WITH accessible AS (
                SELECT d.id, d.title, d.storage_key,
                    COALESCE(to_jsonb(d)->'catalog_metadata', '{}'::jsonb) AS metadata
                FROM documents d
                WHERE d.is_active AND d.status IN ('ready','indexed')
                AND (d.document_kind<>'code' OR %s)
                AND (%s OR d.is_public OR d.allowed_roles && %s
                     OR d.allowed_departments && %s OR d.owner_id=%s)
            ), filtered AS (
                SELECT * FROM accessible
                WHERE (%s='' OR strpos(lower(title || ' ' ||
                    COALESCE(metadata->>'description','')), lower(%s))>0)
                AND COALESCE(metadata->'tags', '[]'::jsonb) @> %s
            ), page AS (
                SELECT * FROM filtered ORDER BY lower(title), id LIMIT %s OFFSET %s
            )
            SELECT (SELECT count(*) FROM filtered) AS total,
                (SELECT count(*) FROM accessible
                 WHERE COALESCE(metadata->'tags','[]'::jsonb)='[]'::jsonb) AS untagged_count,
                COALESCE((SELECT jsonb_agg(p ORDER BY lower(p.title),p.id) FROM page p),
                         '[]'::jsonb) AS items
            """, (role in {'admin', 'programmer'}, role == 'admin', roles,
                  context.get('departments') or [], context.get('user_id'),
                  request.query, request.query, Jsonb(sorted({tag.strip().lower() for tag in request.tags if tag.strip()})),
                  request.limit, request.offset))).fetchone()
    items = [
        dict(document_id=item['id'], title=item['title'][:300],
             description=str(item['metadata'].get('description', ''))[:500],
             tags=item['metadata'].get('tags', []),
             url='/api/chat/download/' + quote(item['id'], safe='') if item['storage_key'] else None)
        for item in row['items']
    ]
    following = request.offset + len(items)
    return dict(items=items, total=row['total'], untagged_count=row['untagged_count'],
                next_offset=following if following < row['total'] else None)


async def search_selected_documents(data, query, document_ids, context):
    async with data.catalog.pool.connection() as conn:
        conn.row_factory = dict_row
        rows = await (await conn.execute("""
            SELECT d.*, c.content FROM documents d
            JOIN document_contents c ON c.document_id=d.id
            WHERE d.id=ANY(%s) AND d.is_active AND d.status IN ('ready','indexed')
            ORDER BY d.id
            """, (document_ids,))).fetchall()
    result = []
    for row in rows:
        if can_read_document(DocumentRecord.from_row(row), context):
            text, _ = excerpt(row['content'], query)
            result.append(Document(page_content=text,
                                   metadata={'document_id': row['id'], 'title': row['title']}))
    return result
