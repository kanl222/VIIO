"""Search only approved publication snapshots and capture the returned evidence."""
from viio_rag.diagnostics import capture, record


async def search_publications(data, query, diagnostics=False):
    with capture('search', query) as trace:
        diagnostic = {} if diagnostics or trace is not None else None
        try:
            record('search_request', query=query, scope='publication_gateway')
            items = await data.public_search(query, **({'diagnostics': diagnostic} if diagnostic is not None else {}))
        except Exception as error:
            record('retrieval', query=query, scope='publication_gateway', status='error', error=type(error).__name__)
            raise
        record('retrieval', query=query, scope='publication_gateway', status='ok',
               context='\n\n'.join(item['content'] for item in items), **(diagnostic or {}))
        return {'items': items, **({'diagnostics': diagnostic} if diagnostics else {})}
