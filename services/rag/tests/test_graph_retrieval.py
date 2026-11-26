from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from langchain_core.documents import Document

from viio_rag.app import create_app
from viio_rag.config import Settings
from viio_rag.infrastructure.graph_retrieval import MemgraphRetriever, extract_keywords

READ_KEY = "r" * 40
PUBLIC_KEY = "p" * 40


def test_extract_keywords():
    keywords = extract_keywords("Что такое ИСЛО и как работает выписка рецепта?")
    assert "исло" in keywords
    assert "рецепта" in keywords
    assert "что" not in keywords
    assert "как" not in keywords
    assert extract_keywords('ИС ЛО') == extract_keywords('ИСЛО') == ['исло']


def test_short_abbreviation_reaches_graph_and_preserves_aliases():
    driver = MagicMock()
    session = driver.session.return_value.__enter__.return_value
    def execute(query, **kwargs):
        result = MagicMock()
        result.data.return_value = ([{'name': 'ЭП', 'full_name': 'Электронная подпись',
            'short_form': 'ЭП', 'aliases': ['цифровая подпись'], 'description': '',
            'category': 'Термин', 'target_name': None}] if 'MATCH (a:Abbreviation)' in query else [])
        return result
    session.run.side_effect = execute
    retriever = MemgraphRetriever()
    with patch.object(retriever, 'get_driver', return_value=driver):
        facts = retriever.search_graph('ЭП')
    assert facts['abbreviations'][0]['aliases'] == ['цифровая подпись']
    assert session.run.call_args_list[0].kwargs['tokens_lower'] == ['эп']


def test_format_graph_context():
    sample_data = {
        "abbreviations": [{
            "name": "ИСЛО",
            "full_name": "Информационная система «Лекарственное обеспечение»",
            "category": "Информационные системы",
            "description": "Выписка электронных рецептов",
            "related": ["ГИСЗ ОО"],
        }],
        "processes": [{
            "process": "Приёмка товара",
            "owner": "Зав. складом",
            "regulations": ["GDP"],
            "steps": [
                {"order": 1, "name": "Входной контроль", "role": "Фармацевт", "system": "1С"},
            ],
        }],
    }
    context = MemgraphRetriever.format_graph_context(sample_data)
    assert "### Факты онтологии и термины (Memgraph Knowledge Graph):" in context
    assert "**ИСЛО**" in context
    assert "Информационная система «Лекарственное обеспечение»" in context
    assert "Приёмка товара" in context
    assert "Входной контроль" in context


@pytest.mark.asyncio
async def test_hybrid_graphrag_merges_graph_facts_and_vector_docs():
    allowed_doc = Document(
        page_content="Пошаговая инструкция по выписке рецепта в модуле.",
        metadata={"document_id": "doc-02", "title": "Регламент выписки"},
    )
    vectors = SimpleNamespace(similarity_search=lambda **kwargs: [allowed_doc])
    data = SimpleNamespace(
        lexical=AsyncMock(return_value=[allowed_doc]),
        public_index=None,
        authorized_candidates=AsyncMock(return_value=[allowed_doc]),
        access_revision=AsyncMock(return_value="a" * 32),
        public_search=AsyncMock(return_value=[]),
        public_ids=AsyncMock(return_value=set()),
        evidence_parents=AsyncMock(return_value={}),
    )
    settings = Settings(
        _env_file=None,
        service_role="rag",
        secure_runtime=True,
        documents_read_key=READ_KEY,
        consultant_source_key=PUBLIC_KEY,
        memgraph_enabled=True,
        reranker_enabled=False,
    )
    app = create_app(settings, data=data, vector_store=vectors)

    mock_graph_data = {
        "abbreviations": [{
            "name": "ИСЛО",
            "full_name": "Информационная система «Лекарственное обеспечение»",
            "category": "Информационные системы",
            "description": "Региональная система",
            "related": [],
        }],
        "processes": [],
        "storage_rules": [],
        "systems": [],
        "regulations": [],
        "employees": [],
    }

    with patch.object(MemgraphRetriever, "search_graph", return_value=mock_graph_data):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://rag") as client:
            payload = {"query": "Что такое ИСЛО?", "diagnostics": True}
            resp = await client.post(
                "/api/v1/knowledge/search",
                json=payload,
                headers={"X-Internal-Token": READ_KEY},
            )
            assert resp.status_code == 200
            data = resp.json()
            context = data["context"]
            assert "### Факты онтологии и термины (Memgraph Knowledge Graph):" in context
            assert "Информационная система «Лекарственное обеспечение»" in context
            assert "### Регламентирующие документы:" in context
            assert "Пошаговая инструкция по выписке рецепта" in context
            assert data["diagnostics"]["graph_facts_count"] == 1
            assert data['graph_definitions'][0]['full_name'] == 'Информационная система «Лекарственное обеспечение»'
            assert 'document_id' not in data['graph_definitions'][0]


@pytest.mark.asyncio
async def test_health_reports_graph_rag_status():
    settings = Settings(
        _env_file=None,
        service_role="rag",
        secure_runtime=True,
        documents_read_key=READ_KEY,
        consultant_source_key=PUBLIC_KEY,
        memgraph_enabled=True,
        memgraph_uri="bolt://memgraph:7687",
    )
    data = SimpleNamespace(
        public_index=None,
        access_revision=AsyncMock(return_value="a" * 32),
    )
    app = create_app(settings, data=data, vector_store=SimpleNamespace())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://rag") as client:
        resp = await client.get("/health")
        assert resp.status_code == 200
        health = resp.json()
        assert health["status"] == "healthy"
        assert health["graph_rag"] == "bolt://memgraph:7687"
