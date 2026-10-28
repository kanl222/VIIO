"""Optional local diagnostics. Vendored into each isolated agent package.

No credentials, model internals or hidden reasoning are captured. Disabled unless
VIIO_TRACE_DIR is set. A failed diagnostic write must never fail a chat request.
"""
import contextvars
import json
import hashlib
import logging
import os
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

_current = contextvars.ContextVar('viio_trace', default=None)
logger = logging.getLogger(__name__)


def enabled():
    return bool(os.environ.get('VIIO_TRACE_DIR'))


def record(stage, **data):
    trace = _current.get()
    if trace is not None and len(trace['events']) < 100:
        trace['events'].append(dict(stage=stage, elapsed_ms=round((time.monotonic()-trace['_start'])*1000), **data))


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
           tool_calls=getattr(response, 'tool_calls', []), duration_ms=round((time.monotonic()-start)*1000))
    return response


@contextmanager
def capture(agent, question, thread_id=None):
    if not enabled():
        yield None
        return
    trace = dict(id=uuid.uuid4().hex, agent=agent, question=question, thread_id=thread_id,
                 configuration={key: os.environ[key] for key in ('LLM_PROVIDER', 'LMSTUDIO_MODEL', 'OLLAMA_MODEL',
                     'CONSULTANT_PROVIDER', 'CONSULTANT_MODEL', 'CONSULTANT_LMSTUDIO_MODEL', 'CONSULTANT_TEMPERATURE',
                     'RAG_CONTEXT_MAX_CHARS', 'CODING_AGENT_MODEL') if key in os.environ},
                 started_at=datetime.now(timezone.utc).isoformat(), events=[], status='ok', _start=time.monotonic())
    token = _current.set(trace)
    try:
        yield trace
    except BaseException as error:
        trace.update(status='error', error=type(error).__name__)
        raise
    finally:
        _current.reset(token)
        trace['duration_ms'] = round((time.monotonic()-trace.pop('_start'))*1000)
        try:
            root = Path(os.environ['VIIO_TRACE_DIR'])
            root.mkdir(parents=True, exist_ok=True)
            # Explicitly cap individual text fields, with visible truncation.
            def bounded(value):
                if isinstance(value, str) and len(value) > 100000:
                    return value[:100000] + '\n[Обрезано журналом после 100000 символов]'
                if isinstance(value, dict): return {k: bounded(v) for k, v in value.items()}
                if isinstance(value, list): return [bounded(v) for v in value]
                return value
            payload = json.dumps(bounded(trace), ensure_ascii=False)
            if len(payload.encode()) > 8_000_000:
                trace['events'] = [dict(stage='capture_limit', detail='Трасса превысила 8 МБ; события не сохранены')]
                payload = json.dumps(trace, ensure_ascii=False)
            target = root / (trace['id'] + '.json')
            temporary = target.with_suffix('.tmp')
            temporary.write_text(payload, encoding='utf-8')
            temporary.replace(target)
            files = sorted(root.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)
            for index, path in enumerate(files):
                if index >= 1000 or path.stat().st_mtime < time.time()-7*86400:
                    path.unlink(missing_ok=True)
        except Exception:
            logger.warning('Local diagnostic write failed', exc_info=False)
