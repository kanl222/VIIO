"""LangGraph StateGraph for document ingestion and processing pipeline.

Stages:
Validate -> Extract -> Normalize -> Chunk -> Embed -> Persist
Each stage operates with step-level retry policies for transient I/O, vector, or database errors.
"""

from __future__ import annotations

import logging
from functools import partial
from typing import Any, Literal, TypedDict

import anyio.to_thread
import httpx
from langchain_core.documents import Document
from langgraph.graph import StateGraph, START, END
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import RetryPolicy

from viio_documents.services.code_documents import split_code
from viio_documents.services.office_extraction import OfficeExtractionService, OFFICE_MIMES
from viio_documents.services.pdf_extraction import PdfExtractionService
from viio_documents.services.text_normalization import normalize_document_text
from viio_rag.chunks import document_chunks

logger = logging.getLogger(__name__)


class IngestionState(TypedDict, total=False):
    # Inputs
    raw_data: bytes
    filename: str
    title: str
    mime_type: str
    kind: str
    is_public: bool
    allowed_roles: list[str]
    allowed_departments: list[str]
    owner_id: str | None
    metadata: dict[str, Any]
    storage_key: str | None
    document_id: str
    reviewed_content: str | None
    version: int
    parent: Any | None
    digest: str

    # Pipeline State
    is_valid: bool
    validation_error: str | None
    extracted_text: str
    extraction_engine: str | None
    normalized_text: str
    chunks: list[str]
    chunk_metadatas: list[dict[str, Any]]
    chunks_count: int
    embedded_documents: list[Document]
    embeddings_ready: bool
    persisted: bool
    status: str
    error: str | None


def build_document_ingestion_graph(
    document_service: Any,
    catalog: Any = None,
    store: Any = None,
    settings: Any = None,
    knowledge_data: Any = None,
    chunk_size: int = 500,
    chunk_overlap: int = 50,
    chunk_unit: Literal["characters", "words"] = "characters",
) -> CompiledStateGraph:
    """Builds a compiled LangGraph for validating, extracting, normalizing, chunking, embedding, and persisting documents."""

    # 1. Validate Node
    def validate_node(state: IngestionState) -> dict[str, Any]:
        title = (state.get("title") or "").strip()
        data = state.get("raw_data") or b""
        reviewed = state.get("reviewed_content")
        mime = state.get("mime_type") or "text/plain"
        max_chars = getattr(settings, "pdf_max_chars", 100000)

        if not title:
            return {"is_valid": False, "validation_error": "Заголовок документа не может быть пустым", "status": "failed"}
        if len(title) > 255:
            return {"is_valid": False, "validation_error": "Заголовок превышает лимит 255 символов", "status": "failed"}
        if not data and not reviewed:
            return {"is_valid": False, "validation_error": "Содержимое документа не может быть пустым", "status": "failed"}
        if "\x00" in title:
            return {"is_valid": False, "validation_error": "Заголовок содержит недопустимые нулевые байты", "status": "failed"}

        return {"is_valid": True, "status": "validated"}

    def validate_condition(state: IngestionState) -> Literal["extract", "__end__"]:
        if state.get("is_valid"):
            return "extract"
        return "__end__"

    # 2. Extract Node (with RetryPolicy)
    async def extract_node(state: IngestionState) -> dict[str, Any]:
        reviewed = state.get("reviewed_content")
        mime = state.get("mime_type") or "text/plain"
        data = state.get("raw_data") or b""
        filename = state.get("filename") or "document.txt"
        meta = dict(state.get("metadata") or {})

        if reviewed is not None:
            content = reviewed
            engine = meta.get("extraction_engine", "reviewed")
        elif mime == "application/pdf":
            if not settings:
                raise ValueError("PDF требует проверки текста перед индексацией")
            extracted = await PdfExtractionService(settings, filename=filename, job_id=state.get("document_id")).extract(data)
            content = extracted["content"]
            engine = extracted["engine"]
            meta["extraction_engine"] = engine
        elif mime in OFFICE_MIMES.values():
            if not settings:
                raise ValueError("Office сервис не настроен")
            extracted = await OfficeExtractionService(settings).extract(data, filename)
            content = extracted["content"]
            engine = extracted["engine"]
            meta["extraction_engine"] = engine
        else:
            content = data.decode("utf-8-sig")
            if "\x00" in content:
                raise ValueError("Двоичный файл нельзя индексировать как текст")
            engine = "text"

        return {
            "extracted_text": content,
            "extraction_engine": engine,
            "metadata": meta,
            "status": "extracted",
        }

    # 3. Normalize Node
    def normalize_node(state: IngestionState) -> dict[str, Any]:
        content = state.get("extracted_text") or ""
        kind = state.get("kind", "prose")
        mime = state.get("mime_type", "")
        meta = dict(state.get("metadata") or {})
        max_chars = getattr(settings, "pdf_max_chars", 100000)

        if (
            kind != "code"
            and mime != "text/markdown"
            and meta.get("input_format") != "manual-text"
            and meta.get("extraction_engine") != "docling-cpu"
        ):
            content = normalize_document_text(content)
            meta["text_normalization"] = "prose-v1"

        clean = content.strip()
        if not clean or "\x00" in clean or len(clean) > max_chars:
            raise ValueError("Пустой, двоичный или превышающий лимит символов текст после нормализации")

        return {
            "normalized_text": content,
            "metadata": meta,
            "status": "normalized",
        }

    # 4. Chunk Node
    def chunk_node(state: IngestionState) -> dict[str, Any]:
        content = state.get("normalized_text") or ""
        kind = state.get("kind", "prose")
        filename = state.get("filename") or "document.txt"
        doc_id = state.get("document_id") or "doc-unknown"
        title = state.get("title") or "Документ"
        meta = state.get("metadata") or {}
        parent = state.get("parent")
        version = state.get("version", 1)

        c_size = getattr(document_service, "_chunk_size", chunk_size)
        c_overlap = getattr(document_service, "_chunk_overlap", chunk_overlap)
        c_unit = getattr(document_service, "_chunk_unit", chunk_unit)

        if kind == "code":
            code_parts = split_code(content, filename)
            chunks = [part[0] for part in code_parts] if code_parts else [content]
            line_parts = code_parts if code_parts else [(content, 1, 1)]
            chunk_metas = [
                {
                    **meta,
                    "document_id": doc_id,
                    "title": title,
                    "filename": filename,
                    "kind": "code",
                    "chunk_index": idx,
                    "chunk_unit": c_unit,
                    "chunk_size": c_size,
                    "chunk_overlap": c_overlap,
                    "line_start": part[1],
                    "line_end": part[2],
                    "is_public": False,
                    "allowed_roles": ["programmer", "admin"],
                    "allowed_departments": [],
                    "owner_id": state.get("owner_id"),
                    "version": version,
                    "is_active": parent is None,
                }
                for idx, part in enumerate(line_parts)
            ]
        else:
            doc_parts = list(document_chunks(content, c_size, c_overlap, c_unit))
            chunks = [part[0] for part in doc_parts] if doc_parts else [content]
            extra_info = [part[1] for part in doc_parts] if doc_parts else [{}]
            roles = state.get("allowed_roles") if state.get("allowed_roles") is not None else ["employee"]
            depts = state.get("allowed_departments") if state.get("allowed_departments") is not None else ["general"]
            chunk_metas = [
                {
                    **meta,
                    **(extra_info[idx] if idx < len(extra_info) else {}),
                    "document_id": doc_id,
                    "title": title,
                    "filename": filename,
                    "kind": kind,
                    "chunk_index": idx,
                    "chunk_unit": c_unit,
                    "chunk_size": c_size,
                    "chunk_overlap": c_overlap,
                    "is_public": state.get("is_public", False),
                    "allowed_roles": roles,
                    "allowed_departments": depts,
                    "owner_id": state.get("owner_id"),
                    "version": version,
                    "is_active": parent is None,
                }
                for idx in range(len(chunks))
            ]

        return {
            "chunks": chunks,
            "chunk_metadatas": chunk_metas,
            "chunks_count": len(chunks),
            "status": "chunked",
        }

    # 5. Embed Node (with RetryPolicy)
    async def embed_node(state: IngestionState) -> dict[str, Any]:
        chunks = state.get("chunks") or []
        metas = state.get("chunk_metadatas") or []
        docs = [Document(page_content=text, metadata=meta) for text, meta in zip(chunks, metas)]
        return {
            "embedded_documents": docs,
            "embeddings_ready": True,
            "status": "embedded",
        }

    # 6. Persist Node (with RetryPolicy)
    async def persist_node(state: IngestionState) -> dict[str, Any]:
        doc_id = state.get("document_id") or "doc-unknown"
        title = state.get("title") or ""
        content = state.get("normalized_text") or ""
        chunks = state.get("chunks") or []
        chunks_count = len(chunks)
        digest = state.get("digest") or ""
        storage_key = state.get("storage_key")
        parent = state.get("parent")
        version = state.get("version", 1)
        kind = state.get("kind", "prose")
        filename = state.get("filename") or "document.txt"
        meta = dict(state.get("metadata") or {})
        if kind == "code":
            roles = ["programmer", "admin"]
            depts = []
            is_public = False
        else:
            roles = state.get("allowed_roles") if state.get("allowed_roles") is not None else ["employee"]
            depts = state.get("allowed_departments") if state.get("allowed_departments") is not None else ["general"]
            is_public = state.get("is_public", False)
        owner_id = state.get("owner_id")
        mime = state.get("mime_type", "text/plain")

        # 1. Clean previous index if any
        if hasattr(document_service, "delete_document"):
            try:
                await anyio.to_thread.run_sync(document_service.delete_document, doc_id)
            except (AttributeError, NotImplementedError):
                pass

        # 2. Index in vector store / document service
        if hasattr(document_service, "index_document"):
            doc_meta = {
                **meta,
                "kind": kind,
                "filename": filename,
                "ingestion_ready": False,
                "is_active": parent is None,
                "version": version,
            }
            res = await anyio.to_thread.run_sync(
                partial(
                    document_service.index_document,
                    title,
                    content,
                    doc_meta,
                    is_public,
                    roles,
                    depts,
                    owner_id,
                    document_id=doc_id,
                )
            )
            chunks_count = getattr(res, "chunks_count", chunks_count)
        elif hasattr(document_service, "_vector_store") and hasattr(document_service._vector_store, "add_documents"):
            embedded_docs = state.get("embedded_documents") or []
            await anyio.to_thread.run_sync(document_service._vector_store.add_documents, embedded_docs)

        # 3. Register in catalog if present
        if catalog is not None:
            raw_len = len(state.get("raw_data") or b"")
            family_id = (parent.family_id or parent.id) if parent else doc_id
            await catalog.create(
                doc_id=doc_id,
                title=title,
                chunks_count=chunks_count,
                mime_type=mime,
                file_size=raw_len,
                sha256=digest,
                owner_id=owner_id,
                is_public=is_public,
                allowed_roles=roles,
                allowed_departments=depts,
                storage_key=storage_key,
                status="processing",
                family_id=family_id,
                version=version,
                parent_id=parent.id if parent else None,
                is_active=parent is None,
                document_kind=kind,
                original_filename=filename[:255],
                catalog_metadata=meta,
            )

        # 4. Save raw text in knowledge_data if present
        if knowledge_data is not None and hasattr(knowledge_data, "save_text"):
            await knowledge_data.save_text(doc_id, content, filename, title)

        # 5. Publish and activate document
        if hasattr(document_service, "publish_document"):
            try:
                await anyio.to_thread.run_sync(document_service.publish_document, doc_id)
            except (AttributeError, NotImplementedError):
                pass

        if parent and hasattr(document_service, "set_document_active"):
            await anyio.to_thread.run_sync(document_service.set_document_active, parent.id, False)
            await anyio.to_thread.run_sync(document_service.set_document_active, doc_id, True)
            if catalog is not None and hasattr(catalog, "activate_version"):
                await catalog.activate_version(doc_id, parent.id)
        elif catalog is not None and hasattr(catalog, "update_status"):
            await catalog.update_status(doc_id, "ready")

        return {
            "persisted": True,
            "chunks_count": chunks_count,
            "status": "ready",
        }

    # Graph construction with node-level retry policies
    retry_io = RetryPolicy(max_attempts=3, retry_on=(IOError, TimeoutError, httpx.TransportError, ConnectionError))
    retry_persist = RetryPolicy(max_attempts=3, retry_on=(IOError, TimeoutError, httpx.TransportError, ConnectionError))

    builder = StateGraph(IngestionState)
    builder.add_node("validate", validate_node)
    builder.add_node("extract", extract_node, retry_policy=retry_io)
    builder.add_node("normalize", normalize_node)
    builder.add_node("chunk", chunk_node)
    builder.add_node("embed", embed_node, retry_policy=retry_io)
    builder.add_node("persist", persist_node, retry_policy=retry_persist)

    builder.add_edge(START, "validate")
    builder.add_conditional_edges("validate", validate_condition, {"extract": "extract", "__end__": END})
    builder.add_edge("extract", "normalize")
    builder.add_edge("normalize", "chunk")
    builder.add_edge("chunk", "embed")
    builder.add_edge("embed", "persist")
    builder.add_edge("persist", END)

    return builder.compile()
