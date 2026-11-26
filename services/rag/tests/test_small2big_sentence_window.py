"""Unit tests for Small2Big / Sentence-Window Chunking and Retrieval."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from langchain_core.documents import Document

from viio_rag.application.chunks import (
    split_into_sentences,
    sentence_window_chunks,
    document_chunks,
    sections,
)
from viio_rag.application.search_tool import format_documents
from viio_rag.application.retrieval import EnhancedRetrieval


def test_split_into_sentences_russian_and_english():
    text = (
        "Каждый сотрудник имеет право на отпуск. Для этого оформляется заявление за 2 недели. "
        "Руководитель утверждает график! Все приказы регистрируются в отделе кадров."
    )
    sentences = split_into_sentences(text)
    assert len(sentences) == 4
    assert sentences[0] == "Каждый сотрудник имеет право на отпуск."
    assert sentences[1] == "Для этого оформляется заявление за 2 недели."
    assert sentences[2] == "Руководитель утверждает график!"
    assert sentences[3] == "Все приказы регистрируются в отделе кадров."


def test_sentence_window_chunks_expands_context():
    content = (
        "# Регламент отпусков\n\n"
        "Каждый сотрудник имеет право на ежегодный отпуск. "
        "Заявление подается минимум за две недели до начала. "
        "Выплата отпускных производится за три дня до начала отпуска. "
        "Перенос отпуска возможен по согласованию сторон."
    )

    chunks = list(sentence_window_chunks(content, window_sentences=1))
    assert len(chunks) == 4

    # Check first chunk (Small focused, Big window has sentence 0 + 1)
    text_0, meta_0 = chunks[0]
    assert "Каждый сотрудник" in text_0
    assert "Заявление подается" in meta_0["window_context"]
    assert meta_0["chunker_strategy"] == "small2big"

    # Check middle chunk (Small has sentence 1, Big window has sentence 0 + 1 + 2)
    text_1, meta_1 = chunks[1]
    assert "Заявление подается" in text_1
    assert "Каждый сотрудник" in meta_1["window_context"]
    assert "Заявление подается" in meta_1["window_context"]
    assert "Выплата отпускных" in meta_1["window_context"]


def test_document_chunks_attaches_window_context():
    content = (
        "# Раздел 1\n\n"
        "Параграф А. Это достаточно длинное первое предложение для проверки чанкера.\n\n"
        "Параграф Б. Это второе предложение раздела, которое должно пойти в отдельный чанк.\n\n"
        "Параграф В. Это третье предложение раздела для полноты контекста."
    )
    chunks = list(document_chunks(content, size=70, overlap=0, window_size=1))
    assert len(chunks) >= 2
    for text, meta in chunks:
        assert "window_context" in meta
        assert len(meta["window_context"]) >= len(text)


def test_format_documents_uses_window_context_when_available():
    doc_standard = Document(
        page_content="Краткое предложение.",
        metadata={"title": "Инструкция", "document_id": "doc1"},
    )
    doc_windowed = Document(
        page_content="Точечный факт.",
        metadata={
            "title": "Регламент",
            "document_id": "doc2",
            "window_context": "Широкий контекст раздела: Дополнительные детали. Точечный факт. Заключение.",
        },
    )

    formatted = format_documents([doc_standard, doc_windowed])
    assert "Краткое предложение." in formatted
    assert "Широкий контекст раздела: Дополнительные детали." in formatted
    assert "Точечный факт." in formatted


@pytest.mark.asyncio
async def test_enhanced_retrieval_records_small2big_telemetry():
    doc = Document(
        page_content="Фрагмент правила отпуска",
        metadata={
            "document_id": "doc1",
            "title": "Правила отпуска",
            "window_context": "Предыстория регламента. Фрагмент правила отпуска. Последствия нарушения порядка.",
        },
    )

    vector = SimpleNamespace(similarity_search=lambda query, k, filter: [doc])
    data = SimpleNamespace(
        lexical=AsyncMock(return_value=[doc]),
        public_index=None,
        authorized_candidates=AsyncMock(return_value=[doc]),
    )

    retrieval = EnhancedRetrieval(vector, data)
    diagnostics: dict = {}

    selected = await retrieval.search("отпуск", {"role": "employee"}, 1, diagnostics=diagnostics)
    assert len(selected) == 1
    assert diagnostics.get("small2big_enabled") is True
    assert diagnostics.get("small2big_ratio", 0) > 1.0
    assert diagnostics.get("selected", [])[0].get("has_window") is True


@pytest.mark.asyncio
async def test_expand_procedural_neighbors_preserves_relevance_order():
    # doc_z is ranked #1 (most relevant), doc_a is ranked #2
    doc_z = Document(
        page_content="Самый релевантный фрагмент",
        metadata={"document_id": "doc_z", "chunk_index": 1, "section_path": ["Раздел Z"]},
    )
    doc_a = Document(
        page_content="Менее релевантный фрагмент",
        metadata={"document_id": "doc_a", "chunk_index": 0, "section_path": ["Раздел A"]},
    )
    neighbor_z = Document(
        page_content="Соседний фрагмент раздела Z",
        metadata={"document_id": "doc_z", "chunk_index": 2, "section_path": ["Раздел Z"]},
    )

    vector = SimpleNamespace(
        get_chunks=lambda pairs: [neighbor_z] if ("doc_z", 2) in pairs else [],
    )
    data = SimpleNamespace(
        authorized_candidates=AsyncMock(return_value=[neighbor_z]),
    )

    retrieval = EnhancedRetrieval(vector, data)
    expanded = await retrieval._expand_procedural_neighbors([doc_z, doc_a], {"role": "admin"})

    # doc_z and its neighbor must precede doc_a (relevance preserved, not alphabetical by doc_id)
    doc_ids = [d.metadata["document_id"] for d in expanded]
    assert doc_ids[0] == "doc_z"
    assert doc_ids[1] == "doc_z"
    assert doc_ids[2] == "doc_a"


def test_markdown_header_splitter_hierarchy_and_code_blocks():
    markdown_content = (
        "# Главный раздел\n\n"
        "Вводный текст раздела.\n\n"
        "```python\n"
        "# Это комментарий в коде, не должен стать заголовком!\n"
        "def example():\n"
        "    return 42\n"
        "```\n\n"
        "## Подраздел А\n\n"
        "Текст подраздела А.\n\n"
        "### Детализация А.1\n\n"
        "Глубокие детали процесса.\n\n"
        "1. Нумерованный пункт\n"
        "Текст пункта с нумерацией."
    )

    sec_list = list(sections(markdown_content))
    assert len(sec_list) >= 3

    # Section 0: # Главный раздел with code block
    h0, b0 = sec_list[0]
    assert h0 == [(1, "Главный раздел")]
    assert "def example():" in b0
    assert "# Это комментарий в коде" in b0

    # Section 1: ## Подраздел А (inherits Главный раздел)
    h1, b1 = sec_list[1]
    assert h1 == [(1, "Главный раздел"), (2, "Подраздел А")]
    assert "Текст подраздела А." in b1

    # Section 2: ### Детализация А.1
    h2, b2 = sec_list[2]
    assert h2 == [(1, "Главный раздел"), (2, "Подраздел А"), (3, "Детализация А.1")]
    assert "Глубокие детали процесса." in b2

