"""Arize Phoenix OpenTelemetry tracing integration for LangChain and LangGraph."""

import contextvars
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_PHOENIX_INITIALIZED = False
_EXCLUDED_PATTERNS = ("health", "readyz", "metrics", "healthz", "ping")
_current_root_span: contextvars.ContextVar[Any] = contextvars.ContextVar("current_root_span", default=None)
_active_root_spans: dict[int, Any] = {}


class HealthFilterSampler:
    """OpenTelemetry Sampler that drops health check, readiness, and metrics traces."""

    def __init__(self, fallback_sampler: Any = None) -> None:
        self._fallback = fallback_sampler

    def should_sample(
        self,
        parent_context: Any,
        trace_id: int,
        name: str,
        kind: Any = None,
        attributes: dict[str, Any] | None = None,
        links: Any = None,
    ) -> Any:
        from opentelemetry.sdk.trace.sampling import Decision, SamplingResult

        name_lower = (name or "").lower()
        if any(pat in name_lower for pat in _EXCLUDED_PATTERNS):
            return SamplingResult(Decision.DROP)
        if attributes:
            route = str(
                attributes.get("http.route")
                or attributes.get("url.path")
                or attributes.get("http.target")
                or ""
            ).lower()
            if any(pat in route for pat in _EXCLUDED_PATTERNS):
                return SamplingResult(Decision.DROP)
            func = str(attributes.get("code.function.name") or "").lower()
            if any(pat in func for pat in _EXCLUDED_PATTERNS):
                return SamplingResult(Decision.DROP)
        if self._fallback:
            return self._fallback.should_sample(parent_context, trace_id, name, kind, attributes, links)
        return SamplingResult(Decision.RECORD_AND_SAMPLE)

    def get_description(self) -> str:
        return "HealthFilterSampler"


class ExcludeHealthSpanProcessor:
    """SpanProcessor wrapper that drops health check spans at end time."""

    def __init__(self, wrapped: Any) -> None:
        self._wrapped = wrapped

    def __getattr__(self, name: str) -> Any:
        return getattr(self._wrapped, name)

    def _on_ending(self, span: Any) -> None:
        if hasattr(self._wrapped, "_on_ending"):
            self._wrapped._on_ending(span)

    def on_start(self, span: Any, parent_context: Any = None) -> None:
        if getattr(span, "parent", None) is None:
            _current_root_span.set(span)
            ctx = getattr(span, "context", None)
            if ctx and getattr(ctx, "trace_id", None):
                _active_root_spans[ctx.trace_id] = span
        self._wrapped.on_start(span, parent_context)

    def on_end(self, span: Any) -> None:
        name = (getattr(span, "name", "") or "").lower()
        if any(pat in name for pat in _EXCLUDED_PATTERNS):
            return
        attrs = getattr(span, "attributes", None) or {}
        route = str(
            attrs.get("http.route")
            or attrs.get("url.path")
            or attrs.get("http.target")
            or ""
        ).lower()
        if any(pat in route for pat in _EXCLUDED_PATTERNS):
            return
        func = str(attrs.get("code.function.name") or "").lower()
        if any(pat in func for pat in _EXCLUDED_PATTERNS):
            return

        ctx = getattr(span, "context", None)
        trace_id = getattr(ctx, "trace_id", None) if ctx else None
        root = (_active_root_spans.get(trace_id) if trace_id else None) or _current_root_span.get()

        # Auto-propagate input/output/session to root span if root span is missing them
        if root and root is not span and getattr(root, "is_recording", lambda: False)():
            root_attrs = getattr(root, "attributes", None) or {}
            for k in ("input.value", "output.value", "session.id", "user.id"):
                if k not in root_attrs and k in attrs:
                    root.set_attribute(k, attrs[k])
            if "openinference.span.kind" not in root_attrs:
                root.set_attribute("openinference.span.kind", "CHAIN")

        if getattr(span, "parent", None) is None and trace_id:
            _active_root_spans.pop(trace_id, None)

        self._wrapped.on_end(span)

    def shutdown(self) -> None:
        self._wrapped.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self._wrapped.force_flush(timeout_millis)


def record_telemetry_turn_start(
    message: str,
    thread_id: str | None = None,
    user_id: str | None = None,
) -> None:
    """Record turn user input and session context on root span and active span for Phoenix."""
    from opentelemetry import trace

    curr = trace.get_current_span()
    trace_id = None
    if curr:
        sctx = curr.get_span_context()
        if sctx and getattr(sctx, "trace_id", None):
            trace_id = sctx.trace_id
    root = (_active_root_spans.get(trace_id) if trace_id else None) or _current_root_span.get()

    targets = []
    if root and getattr(root, "is_recording", lambda: False)():
        targets.append(root)
    if curr and getattr(curr, "is_recording", lambda: False)() and curr not in targets:
        targets.append(curr)

    for sp in targets:
        sp.set_attribute("openinference.span.kind", "CHAIN")
        sp.set_attribute("input.value", message)
        sp.set_attribute("input.mime_type", "text/plain")
        if thread_id:
            sp.set_attribute("session.id", str(thread_id))
        if user_id:
            sp.set_attribute("user.id", str(user_id))


def record_telemetry_turn_end(
    reply: str,
    thread_id: str | None = None,
) -> None:
    """Record turn assistant output on root span and active span for Phoenix."""
    from opentelemetry import trace

    curr = trace.get_current_span()
    trace_id = None
    if curr:
        sctx = curr.get_span_context()
        if sctx and getattr(sctx, "trace_id", None):
            trace_id = sctx.trace_id
    root = (_active_root_spans.get(trace_id) if trace_id else None) or _current_root_span.get()

    targets = []
    if root and getattr(root, "is_recording", lambda: False)():
        targets.append(root)
    if curr and getattr(curr, "is_recording", lambda: False)() and curr not in targets:
        targets.append(curr)

    for sp in targets:
        sp.set_attribute("output.value", reply)
        sp.set_attribute("output.mime_type", "text/plain")
        if thread_id:
            sp.set_attribute("session.id", str(thread_id))


def record_rag_retrieval_telemetry(
    query: str,
    context: str,
    diagnostics: dict[str, Any] | None = None,
    document_ids: Any = None,
    duration_ms: float | None = None,
    status: str = "ok",
) -> None:
    """Record RAG hybrid retrieval, Qdrant vector search, and Memgraph facts in Phoenix."""
    import json
    from opentelemetry import trace

    tracer = trace.get_tracer("viio.rag")
    diag = diagnostics or {}
    selected = diag.get("selected", [])
    graph_facts_count = int(diag.get("graph_facts_count", 0))
    graph_data = diag.get("graph_data", {})
    fusion = diag.get("fusion", {})
    reranker_info = diag.get("reranker_info", {})
    candidates = diag.get("candidates", [])

    # Enrich active tool span (e.g. search_knowledge_base)
    active_span = trace.get_current_span()
    if active_span and getattr(active_span, "is_recording", lambda: False)():
        active_span.set_attribute("rag.query", query)
        active_span.set_attribute("qdrant.selected_chunks_count", len(selected))
        active_span.set_attribute("memgraph.facts_count", graph_facts_count)
        if fusion:
            active_span.set_attribute("qdrant.hybrid_enabled", True)

    # 1. Main RAG RETRIEVER span
    with tracer.start_as_current_span("rag.hybrid_retrieval") as rag_span:
        rag_span.set_attribute("openinference.span.kind", "RETRIEVER")
        rag_span.set_attribute("input.value", query)
        rag_span.set_attribute("output.value", context[:12000] if context else "")
        rag_span.set_attribute("rag.status", status)
        if duration_ms is not None:
            rag_span.set_attribute("rag.duration_ms", duration_ms)
        if document_ids:
            rag_span.set_attribute("rag.filter.document_ids", json.dumps(list(document_ids), ensure_ascii=False))

        rag_span.set_attribute("qdrant.selected_chunks_count", len(selected))
        rag_span.set_attribute("qdrant.candidates_count", len(candidates))
        rag_span.set_attribute("memgraph.facts_count", graph_facts_count)

        # Attach retrieval documents with scores and metadata
        for i, doc in enumerate(selected[:15]):
            doc_id = str(doc.get("document_id") or doc.get("chunk_id") or f"doc_{i}")
            content = str(doc.get("text") or doc.get("content") or "")
            score = float(doc.get("score") or doc.get("rrf_score") or doc.get("term_score") or 1.0)
            meta = {k: v for k, v in doc.items() if k not in ("text", "content")}
            rag_span.set_attribute(f"retrieval.documents.{i}.document.id", doc_id)
            rag_span.set_attribute(f"retrieval.documents.{i}.document.content", content[:4000])
            rag_span.set_attribute(f"retrieval.documents.{i}.document.score", score)
            rag_span.set_attribute(f"retrieval.documents.{i}.document.metadata", json.dumps(meta, ensure_ascii=False))

        # 2. Qdrant Vector Search Child Span
        with tracer.start_as_current_span("qdrant.vector_search") as qdrant_span:
            qdrant_span.set_attribute("openinference.span.kind", "RETRIEVER")
            qdrant_span.set_attribute("db.system", "qdrant")
            qdrant_span.set_attribute("db.operation", "query_points")
            qdrant_span.set_attribute("db.name", "knowledge_base")
            qdrant_span.set_attribute("input.value", query)
            qdrant_span.set_attribute("qdrant.collection", "knowledge_base")
            qdrant_span.set_attribute("qdrant.selected_chunks_count", len(selected))
            qdrant_span.set_attribute("qdrant.candidates_count", len(candidates))
            if fusion:
                qdrant_span.set_attribute("qdrant.hybrid_enabled", True)
                qdrant_span.set_attribute("qdrant.fusion_branches", json.dumps(fusion.get("branches", [])))
                qdrant_span.set_attribute("qdrant.fused_candidates_count", fusion.get("fused_candidates", 0))
            if reranker_info:
                qdrant_span.set_attribute("qdrant.reranker_status", reranker_info.get("reranker_status", "none"))
                if "reranker_model" in reranker_info:
                    qdrant_span.set_attribute("qdrant.reranker_model", str(reranker_info["reranker_model"]))
            # Qdrant chunks
            for j, doc in enumerate(selected[:15]):
                doc_id = str(doc.get("document_id") or doc.get("chunk_id") or f"qdrant_{j}")
                content = str(doc.get("text") or doc.get("content") or "")
                score = float(doc.get("score") or doc.get("rrf_score") or doc.get("term_score") or 1.0)
                meta = {k: v for k, v in doc.items() if k not in ("text", "content")}
                qdrant_span.set_attribute(f"retrieval.documents.{j}.document.id", doc_id)
                qdrant_span.set_attribute(f"retrieval.documents.{j}.document.content", content[:4000])
                qdrant_span.set_attribute(f"retrieval.documents.{j}.document.score", score)
                qdrant_span.set_attribute(f"retrieval.documents.{j}.document.metadata", json.dumps(meta, ensure_ascii=False))

        # 3. Memgraph Knowledge Graph Child Span
        with tracer.start_as_current_span("memgraph.graph_rag") as memgraph_span:
            memgraph_span.set_attribute("openinference.span.kind", "RETRIEVER")
            memgraph_span.set_attribute("db.system", "memgraph")
            memgraph_span.set_attribute("db.operation", "search_graph")
            memgraph_span.set_attribute("input.value", query)
            memgraph_span.set_attribute("memgraph.facts_count", graph_facts_count)
            if graph_data:
                for entity_type, items in graph_data.items():
                    if isinstance(items, list):
                        memgraph_span.set_attribute(f"memgraph.{entity_type}_count", len(items))
                g_idx = 0
                for entity_type, items in graph_data.items():
                    if isinstance(items, list):
                        for item in items[:5]:
                            name = item.get("name") or item.get("title") or item.get("prepara_name") or f"{entity_type}_{g_idx}"
                            item_json = json.dumps(item, ensure_ascii=False)
                            memgraph_span.set_attribute(f"retrieval.documents.{g_idx}.document.id", f"memgraph:{entity_type}:{name}")
                            memgraph_span.set_attribute(f"retrieval.documents.{g_idx}.document.content", item_json[:4000])
                            memgraph_span.set_attribute(f"retrieval.documents.{g_idx}.document.score", 1.0)
                            memgraph_span.set_attribute(f"retrieval.documents.{g_idx}.document.metadata", json.dumps({"entity_type": entity_type, "name": name}, ensure_ascii=False))
                            g_idx += 1


def record_memgraph_telemetry(
    query: str,
    context: str,
    data: dict[str, Any] | None = None,
    duration_ms: float | None = None,
    status: str = "ok",
    error: str | None = None,
    uri: str | None = None,
) -> None:
    """Record standalone Memgraph knowledge graph query in Phoenix."""
    import json
    from opentelemetry import trace

    tracer = trace.get_tracer("viio.memgraph")
    data_dict = data or {}
    total_facts = sum(len(v) for v in data_dict.values() if isinstance(v, list))

    # Enrich active tool span if one exists
    active_span = trace.get_current_span()
    if active_span and getattr(active_span, "is_recording", lambda: False)():
        active_span.set_attribute("db.system", "memgraph")
        active_span.set_attribute("memgraph.facts_count", total_facts)

    with tracer.start_as_current_span("memgraph.search_knowledge_graph") as span:
        span.set_attribute("openinference.span.kind", "RETRIEVER")
        span.set_attribute("db.system", "memgraph")
        span.set_attribute("db.operation", "search_graph")
        span.set_attribute("input.value", query)
        span.set_attribute("output.value", context[:12000] if context else "")
        span.set_attribute("memgraph.status", status)
        if uri:
            span.set_attribute("memgraph.uri", uri)
        if duration_ms is not None:
            span.set_attribute("memgraph.duration_ms", duration_ms)
        if error:
            span.set_attribute("error", error)
        span.set_attribute("memgraph.facts_count", total_facts)

        for cat, items in data_dict.items():
            if isinstance(items, list):
                span.set_attribute(f"memgraph.{cat}_count", len(items))

        g_idx = 0
        for cat, items in data_dict.items():
            if isinstance(items, list):
                for item in items[:8]:
                    name = item.get("name") or item.get("title") or item.get("prepara_name") or f"{cat}_{g_idx}"
                    item_json = json.dumps(item, ensure_ascii=False)
                    span.set_attribute(f"retrieval.documents.{g_idx}.document.id", f"memgraph:{cat}:{name}")
                    span.set_attribute(f"retrieval.documents.{g_idx}.document.content", item_json[:4000])
                    span.set_attribute(f"retrieval.documents.{g_idx}.document.score", 1.0)
                    span.set_attribute(f"retrieval.documents.{g_idx}.document.metadata", json.dumps({"category": cat, "name": name}, ensure_ascii=False))
                    g_idx += 1


def init_phoenix_tracing(
    endpoint: str | None = None,
    project_name: str | None = None,
    enabled: bool = True,
    batch: bool = True,
) -> bool:
    """Initialize Arize Phoenix OpenTelemetry instrumentation for LangChain/LangGraph.

    Sends spans for LLM calls, chain runs, tool calls, and LangGraph node executions
    to a local or remote Arize Phoenix collector via OTLP (OpenTelemetry Protocol).
    Health check endpoints (/health, /metrics, etc.) are filtered out.

    Args:
        endpoint: Phoenix OTLP collector URL (default: http://localhost:6006/v1/traces
                  or PHOENIX_COLLECTOR_ENDPOINT env var).
        project_name: Project name in Phoenix (default: viio or PHOENIX_PROJECT_NAME env var).
        enabled: Whether Phoenix tracing is enabled.
        batch: Whether to use BatchSpanProcessor for async non-blocking span export.

    Returns:
        bool: True if instrumentation was initialized, False otherwise.
    """
    global _PHOENIX_INITIALIZED
    if not enabled:
        return False
    if _PHOENIX_INITIALIZED:
        return True

    ep = (
        endpoint
        or os.getenv("PHOENIX_COLLECTOR_ENDPOINT")
        or os.getenv("PHOENIX_ENDPOINT")
        or "http://localhost:6006/v1/traces"
    )
    proj = project_name or os.getenv("PHOENIX_PROJECT_NAME", "viio")

    try:
        from phoenix.otel import register
        from openinference.instrumentation.langchain import LangChainInstrumentor

        tracer_provider = register(
            project_name=proj,
            endpoint=ep,
            batch=batch,
            verbose=False,
            sampler=HealthFilterSampler(),
        )

        raw_processor = getattr(tracer_provider, "_default_processor", None)
        if raw_processor:
            wrapped = ExcludeHealthSpanProcessor(raw_processor)
            tracer_provider._default_processor = wrapped
            if hasattr(tracer_provider, "_active_span_processor") and hasattr(
                tracer_provider._active_span_processor, "_span_processors"
            ):
                procs = list(tracer_provider._active_span_processor._span_processors)
                for i, p in enumerate(procs):
                    if p is raw_processor:
                        procs[i] = wrapped
                tracer_provider._active_span_processor._span_processors = tuple(procs)

        LangChainInstrumentor().instrument(tracer_provider=tracer_provider)
        _PHOENIX_INITIALIZED = True
        logger.info(
            "Arize Phoenix OpenTelemetry instrumentation active (project: %s, endpoint: %s, health filter enabled)",
            proj,
            ep,
        )
        return True
    except Exception as exc:
        logger.warning("Failed to initialize Arize Phoenix tracing: %s", exc)
        return False


def is_phoenix_active() -> bool:
    """Return whether Phoenix tracing has been initialized in this process."""
    return _PHOENIX_INITIALIZED
