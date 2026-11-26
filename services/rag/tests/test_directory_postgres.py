from test_document_pipeline_integration import pipeline, pytestmark
from viio_documents.api.router import DocumentCreateRequest
from viio_rag.application.document_directory import list_documents, search_selected_documents
from viio_rag.contracts.schemas import DocumentListRequest, UserContext


async def test_catalog_filters_acl_before_count_and_paging_and_updates_tags(pipeline):
    client, _, catalog, _, ingestion, _ = pipeline
    ids = []
    for title, public in [('A Private', False), ('B Guide', True), ('C Guide', True)]:
        result = await ingestion.index_text(DocumentCreateRequest(
            title=title, content='Content of ' + title, is_public=public,
            allowed_roles=['admin'], allowed_departments=[],
            metadata={'tags': [' Инструкция ', 'ИС ЛО'], 'description': 'Guide description'}))
        ids.append(result.document_id)
    request = DocumentListRequest(tags=['ИНСТРУКЦИЯ'], limit=1,
                                  user_context=UserContext(role='employee', departments=[]))
    first = await list_documents(ingestion.data, request)
    assert first['total'] == 2
    assert first['items'][0]['title'] == 'B Guide'
    assert first['next_offset'] == 1
    request.offset = 1
    second = await list_documents(ingestion.data, request)
    assert second['items'][0]['title'] == 'C Guide' and second['next_offset'] is None
    response = await client.post('/api/v1/documents/catalog/' + ids[1] + '/metadata',
                                 json={'tags': ['Регламент']})
    assert response.status_code == 200
    assert response.json()['catalog_metadata'] == {
        'tags': ['регламент'], 'description': 'Guide description'}
    request.offset = 0
    assert (await list_documents(ingestion.data, request))['total'] == 1
    docs = await search_selected_documents(ingestion.data, 'Content', ids, {'role': 'employee'})
    assert {doc.metadata['document_id'] for doc in docs} == set(ids[1:])
