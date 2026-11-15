import pytest
from viio_documents.services.graph_sync import GraphSyncService


def test_extract_relations_regulations_and_systems():
    title = "Регламент работы с электронными рецептами и МДЛП"
    content = """
    Настоящий документ разработан в соответствии с Федеральным законом № 323-ФЗ,
    Приказом Минздрава России № 1094н и Постановлением Правительства РФ № 1556.
    Оператором системы ИС ЛО является ГАУЗ ОАС (отдел ОСМП, техподдержка).
    Взаимодействие осуществляется с сервисом Карта жителя Оренбуржья и ГИСЗ ОО.
    """

    rels = GraphSyncService.extract_relations(title, content)
    
    reg_codes = [r["code"] for r in rels["regulations"]]
    assert "FZ-323" in reg_codes
    assert "MZ-RF-1094n" in reg_codes
    assert "PP-1556" in reg_codes

    assert "ИС «Лекарственное обеспечение»" in rels["systems"]
    assert "ГИСЗ Оренбургской области" in rels["systems"]
    assert "ФГИС МДЛП «Честный Знак»" in rels["systems"]
    assert "Сервис «Универсальная карта оренбуржца»" in rels["systems"]

    assert "OAS_OSMP" in rels["departments"]


@pytest.mark.asyncio
async def test_graph_sync_graceful_when_disabled():
    class DummySettings:
        memgraph_enabled = False
        memgraph_uri = "bolt://invalid:9999"

    service = GraphSyncService(settings=DummySettings())
    res = await service.sync_document(
        doc_id="test-doc-disabled",
        title="Тестовый документ",
        content="Текст документа",
    )
    assert res is False
