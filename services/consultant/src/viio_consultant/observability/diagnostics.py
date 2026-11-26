"""Optional local diagnostics. Vendored into each isolated agent package.

No credentials, model internals or hidden reasoning are captured. Disabled unless
VIIO_TRACE_DIR is set. A failed diagnostic write must never fail a chat request.
"""
import contextvars
import asyncio
import functools
import inspect
import json
import hashlib
import logging
import os
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, TypeVar, cast

F = TypeVar('F', bound=Callable[..., Any])

_current = contextvars.ContextVar('viio_trace', default=None)
_current_event: contextvars.ContextVar[Optional[dict[str, Any]]] = contextvars.ContextVar(
    'current_event_payload', default=None
)
_current_span_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    'current_span_id', default=None
)
logger = logging.getLogger(__name__)


def enabled():
    return bool(os.environ.get('VIIO_TRACE_DIR'))


def trace_headers() -> dict[str, str]:
    """Generates distributed tracing headers for outgoing HTTP requests."""
    trace = _current.get()
    if trace is None:
        return {}
    headers = {'X-Trace-ID': trace['id']}
    span = _current_span_id.get()
    if span:
        headers['X-Parent-Span-ID'] = span
    return headers


def set_event_data(**kwargs: Any) -> None:
    """Enrich active event with additional payload from within the decorated function."""
    ev = _current_event.get()
    if ev is not None:
        ev.update(kwargs)


def record(stage, title=None, **data):
    trace = _current.get()
    if trace is None:
        return
    title_value = title or data.pop('title', None) or stage
    span_id = data.pop('span_id', None) or uuid.uuid4().hex[:12]
    parent_span_id = data.pop('parent_span_id', None) or _current_span_id.get()
    event_dict = dict(
        stage=stage,
        title=title_value,
        span_id=span_id,
        parent_span_id=parent_span_id,
        elapsed_ms=round((time.monotonic()-trace['_start'])*1000),
        **data
    )
    if len(trace['events']) < 1000:
        trace['events'].append(event_dict)
    else:
        trace['dropped_events'] = trace.get('dropped_events', 0) + 1
    persist(trace)


class trace_event:
    """Decorator and context manager for recording trace events with human-readable titles.

    Supported patterns:
      @trace_event("Human readable title")                      # stage = func.__name__
      @trace_event("stage_key", "Human readable title")         # explicit stage and title
      @trace_event("Human readable title", stage="stage_key")   # named stage
    """

    def __init__(
        self,
        title_or_stage: str,
        title: Optional[str] = None,
        *,
        stage: Optional[str] = None,
        extract: Optional[Callable[[Any], dict[str, Any]]] = None,
        **extra_data: Any,
    ) -> None:
        if title is not None:
            self.stage = title_or_stage
            self.title = title
        elif stage is not None:
            self.stage = stage
            self.title = title_or_stage
        else:
            self.stage = None
            self.title = title_or_stage

        self.extract = extract
        self.extra_data = extra_data
        self.data: dict[str, Any] = dict(extra_data)
        self._start: float = 0.0
        self.span_id: Optional[str] = None
        self.parent_span_id: Optional[str] = None
        self._token: Optional[contextvars.Token] = None
        self._token_span: Optional[contextvars.Token] = None

    def set(self, **kwargs: Any) -> 'trace_event':
        self.data.update(kwargs)
        return self

    def __enter__(self) -> 'trace_event':
        self._start = time.monotonic()
        self.span_id = uuid.uuid4().hex[:12]
        self.parent_span_id = _current_span_id.get()
        self._token_span = _current_span_id.set(self.span_id)
        self._token = _current_event.set(self.data)
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        duration_ms = round((time.monotonic() - self._start) * 1000)
        if self._token_span is not None:
            _current_span_id.reset(self._token_span)
            self._token_span = None
        if self._token is not None:
            _current_event.reset(self._token)
            self._token = None

        stage_name = self.stage or 'step'
        if exc_type is not None:
            record(
                stage_name,
                title=self.title,
                span_id=self.span_id,
                parent_span_id=self.parent_span_id,
                status='error',
                error=exc_type.__name__,
                error_message=str(exc_val) if exc_val else None,
                duration_ms=duration_ms,
                **self.data,
            )
        else:
            status = self.data.pop('status', 'ok')
            record(
                stage_name,
                title=self.title,
                span_id=self.span_id,
                parent_span_id=self.parent_span_id,
                status=status,
                duration_ms=duration_ms,
                **self.data,
            )
        return False

    async def __aenter__(self) -> 'trace_event':
        return self.__enter__()

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        return self.__exit__(exc_type, exc_val, exc_tb)

    def __call__(self, func: F) -> F:
        final_stage = self.stage or func.__name__

        if inspect.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                if not enabled() or _current.get() is None:
                    return await func(*args, **kwargs)
                instance = self.__class__(
                    self.title,
                    stage=final_stage,
                    extract=self.extract,
                    **self.extra_data,
                )
                async with instance:
                    result = await func(*args, **kwargs)
                    if self.extract:
                        set_event_data(**self.extract(result))
                    return result

            return cast(F, async_wrapper)
        else:
            @functools.wraps(func)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                if not enabled() or _current.get() is None:
                    return func(*args, **kwargs)
                instance = self.__class__(
                    self.title,
                    stage=final_stage,
                    extract=self.extract,
                    **self.extra_data,
                )
                with instance:
                    result = func(*args, **kwargs)
                    if self.extract:
                        set_event_data(**self.extract(result))
                    return result

            return cast(F, sync_wrapper)


def persist(trace):
    """Publish an atomic snapshot, including unfinished requests."""
    try:
        payload = {k: v for k, v in trace.items() if not k.startswith('_')}
        payload['duration_ms'] = round((time.monotonic()-trace['_start'])*1000)
        payload['updated_at'] = datetime.now(timezone.utc).isoformat()
        def bounded(value):
            if isinstance(value, str) and len(value) > 250000:
                return value[:250000] + '\n[Обрезано журналом]'
            if isinstance(value, dict):
                return {k: bounded(v) for k, v in value.items()}
            if isinstance(value, list):
                return [bounded(v) for v in value]
            return value
        payload = bounded(payload)
        encoded = json.dumps(payload, ensure_ascii=False)
        if len(encoded.encode()) > 8_000_000:
            payload['payload_truncated'] = True
            payload['events'] = [
                {k: v for k, v in event.items() if k in {
                    'stage', 'title', 'span_id', 'parent_span_id', 'elapsed_ms', 'duration_ms', 'call_id', 'name',
                    'status', 'error', 'usage', 'model'}}
                for event in payload['events']]
            encoded = json.dumps(payload, ensure_ascii=False)
        root = Path(os.environ['VIIO_TRACE_DIR'])
        root.mkdir(parents=True, exist_ok=True)
        target = root / (trace['id'] + '.json')
        temporary = target.with_suffix('.tmp')
        temporary.write_text(encoded, encoding='utf-8')
        temporary.replace(target)
    except Exception:
        logger.warning('Local diagnostic write failed', exc_info=False)


async def model_call(model, messages):
    """Capture actual visible model inputs and outputs, never reasoning metadata."""
    if _current.get() is None:
        return await model.ainvoke(messages)
    call_id = uuid.uuid4().hex
    bound = getattr(model, 'bound', model)
    prompt = '\n'.join(str(m.content) for m in messages if getattr(m, 'type', '') == 'system')
    record('context', call_id=call_id, model=str(getattr(bound, 'model_name', None) or getattr(bound, 'model', None) or 'configured'),
           prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
           messages=[dict(role=getattr(m, 'type', 'unknown'), content=m.content,
                          tool_calls=getattr(m, 'tool_calls', [])) for m in messages])
    start = time.monotonic()
    try:
        response = await model.ainvoke(messages)
    except Exception as error:
        record('generation', call_id=call_id, status='error', error=type(error).__name__, duration_ms=round((time.monotonic()-start)*1000))
        raise
    record('generation', call_id=call_id, status='ok', content=response.content,
           tool_calls=getattr(response, 'tool_calls', []), duration_ms=round((time.monotonic()-start)*1000),
           usage=getattr(response, 'usage_metadata', None) or (getattr(response, 'response_metadata', None) or {}).get('token_usage'))
    return response


@contextmanager
def capture(agent, question, thread_id=None, trace_id=None, parent_trace_id=None):
    if not enabled():
        yield None
        return
    tid = trace_id or uuid.uuid4().hex
    trace = dict(id=tid, parent_trace_id=parent_trace_id, agent=agent, question=question, thread_id=thread_id,
                 configuration={key: os.environ[key] for key in ('LLM_PROVIDER', 'LMSTUDIO_MODEL', 'OLLAMA_MODEL',
                     'CONSULTANT_PROVIDER', 'CONSULTANT_MODEL', 'CONSULTANT_LMSTUDIO_MODEL', 'CONSULTANT_TEMPERATURE',
                     'RAG_CONTEXT_MAX_CHARS', 'CODING_AGENT_MODEL') if key in os.environ},
                 started_at=datetime.now(timezone.utc).isoformat(), events=[], status='running', _start=time.monotonic())
    token = _current.set(trace)
    persist(trace)
    try:
        yield trace
        trace['status'] = 'ok'
    except BaseException as error:
        trace.update(status='cancelled' if isinstance(error, asyncio.CancelledError) else 'error',
                     error=type(error).__name__)
        raise
    finally:
        _current.reset(token)
        persist(trace)
        trace['duration_ms'] = round((time.monotonic()-trace.pop('_start'))*1000)
        try:
            root = Path(os.environ['VIIO_TRACE_DIR'])
            root.mkdir(parents=True, exist_ok=True)
            files = sorted(root.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)
            for index, path in enumerate(files):
                if index >= 1000 or path.stat().st_mtime < time.time()-7*86400:
                    path.unlink(missing_ok=True)
        except Exception:
            logger.warning('Local diagnostic write failed', exc_info=False)
