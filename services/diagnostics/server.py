"""Loopback-only trace viewer; stdlib runtime, no access to VIIO databases/keys."""
import io
import json
import os
import re
import threading
import mimetypes
import queue
import sqlite3
import zipfile
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

STATIC = Path(__file__).parent / 'dist'

def _get_repo_root() -> Path:
    current = Path(__file__).resolve()
    if current.parent.parent.name == 'services':
        return current.parent.parent.parent
    return current.parent.parent

def _resolve_roots():
    if os.getenv('TRACE_ROOTS'):
        return [Path(p) for p in os.getenv('TRACE_ROOTS').split(',')]
    if Path('/traces').is_dir():
        return [Path(p) for p in ('/traces/main', '/traces/consultant', '/traces/documents', '/traces/document-worker', '/traces/rag')]
    local_base = _get_repo_root() / 'tmp' / 'traces'
    subdirs = [local_base / name for name in ('main', 'consultant', 'documents', 'document-worker', 'rag')]
    return [local_base] + subdirs

def _resolve_reviews():
    if os.getenv('REVIEW_ROOT'):
        return Path(os.getenv('REVIEW_ROOT'))
    if Path('/data/reviews').parent.is_dir() and os.name != 'nt':
        return Path('/data/reviews')
    return _get_repo_root() / 'tmp' / 'reviews'

ROOTS = _resolve_roots()
REVIEWS = _resolve_reviews()
ID = re.compile(r'^[a-f0-9]{32}$')
LOCK = threading.RLock()
CACHE = {}

SUBSCRIBERS = []
SUBSCRIBERS_LOCK = threading.Lock()

SEARCH_STAGES = {'retrieval', 'crag_retrieve', 'search_request', 'graph_search'}
SEARCH_TOOL_NAMES = {'search_knowledge_base', 'list_documents', 'search_document_content', 'search_knowledge_graph'}


def notify_subscribers(event_type: str, data: dict):
    with SUBSCRIBERS_LOCK:
        dead = []
        for q in list(SUBSCRIBERS):
            try:
                q.put_nowait({'type': event_type, 'data': data})
            except Exception:
                dead.append(q)
        for q in dead:
            if q in SUBSCRIBERS:
                SUBSCRIBERS.remove(q)


def get_db():
    db_path = REVIEWS.parent / 'traces_index.db'
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.execute('PRAGMA journal_mode = WAL;')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS traces (
            id TEXT PRIMARY KEY,
            agent TEXT,
            question TEXT,
            thread_id TEXT,
            started_at TEXT,
            duration_ms INTEGER,
            status TEXT,
            error TEXT,
            searches INTEGER,
            model_calls INTEGER,
            event_count INTEGER,
            total_tokens INTEGER,
            mtime_ns INTEGER
        );
    ''')
    conn.execute('''
        CREATE VIRTUAL TABLE IF NOT EXISTS traces_fts USING fts5(
            id UNINDEXED,
            question,
            error,
            content='traces',
            content_rowid='rowid'
        );
    ''')
    return conn


def search_fts(query_str: str) -> set[str]:
    conn = None
    try:
        conn = get_db()
        cleaned = re.sub(r'[^a-zA-Z0-9\u0400-\u04FF]+', ' ', query_str).strip()
        if not cleaned:
            return set()
        cur = conn.execute("SELECT id FROM traces_fts WHERE traces_fts MATCH ?", (f"{cleaned}*",))
        return {row[0] for row in cur.fetchall()}
    except Exception:
        return set()
    finally:
        if conn:
            conn.close()


def is_search_event(event):
    stage = event.get('stage')
    if stage in SEARCH_STAGES:
        return True
    if stage == 'tool_start' and event.get('name') in SEARCH_TOOL_NAMES:
        return True
    return False


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def trace_path(identifier):
    if not ID.fullmatch(identifier): return None
    return next((root / (identifier+'.json') for root in ROOTS if (root / (identifier+'.json')).is_file()), None)


def review(identifier):
    try: return read_json(REVIEWS / (identifier+'.json'))
    except (OSError, ValueError): return {}


def export_golden_set():
    cases = []
    with LOCK:
        for rev_path in sorted(REVIEWS.glob('*.json')):
            if not ID.fullmatch(rev_path.stem):
                continue
            identifier = rev_path.stem
            try:
                rev_data = read_json(rev_path)
            except Exception:
                continue

            t_path = trace_path(identifier)
            t_data = {}
            if t_path and t_path.is_file():
                try:
                    t_data = read_json(t_path)
                except Exception:
                    pass

            if not rev_data.get('expected_answer') and rev_data.get('score') is None and not rev_data.get('stage'):
                continue

            events = t_data.get('events', [])
            actual_answer = next((e.get('reply') for e in reversed(events) if e.get('stage') == 'answer'), None)
            if not actual_answer and events:
                actual_answer = next((e.get('content') for e in reversed(events) if e.get('stage') == 'generation' and e.get('status') == 'ok'), None)

            cases.append({
                'id': identifier,
                'agent': t_data.get('agent', 'main'),
                'question': t_data.get('question', ''),
                'expected_answer': rev_data.get('expected_answer', ''),
                'evidence': rev_data.get('evidence', ''),
                'document_id': rev_data.get('document_id', ''),
                'score': rev_data.get('score'),
                'stage': rev_data.get('stage', ''),
                'notes': rev_data.get('notes', ''),
                'actual_answer': actual_answer,
                'thread_id': t_data.get('thread_id'),
                'started_at': t_data.get('started_at'),
            })
    return sorted(cases, key=lambda c: c.get('started_at') or '', reverse=True)


def create_traces_zip() -> bytes:
    """Упаковывает все файлы трасс и рецензий в ZIP-архив."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        seen = set()
        with LOCK:
            for root in ROOTS:
                if not root.is_dir():
                    continue
                for path in root.glob('*.json'):
                    if not ID.fullmatch(path.stem) or path.stem in seen:
                        continue
                    seen.add(path.stem)
                    try:
                        agent_sub = path.parent.name if path.parent != root else 'main'
                        archive_path = f"traces/{agent_sub}/{path.name}"
                        zf.write(path, arcname=archive_path)
                    except Exception:
                        pass
            if REVIEWS.is_dir():
                for rev_path in REVIEWS.glob('*.json'):
                    if not ID.fullmatch(rev_path.stem):
                        continue
                    try:
                        zf.write(rev_path, arcname=f"reviews/{rev_path.name}")
                    except Exception:
                        pass
        if not seen:
            zf.writestr('README.txt', 'В архиве пока нет сохраненных трасс диагностики.\n')
    return buf.getvalue()


def import_traces_from_bytes(data: bytes) -> dict:
    """Импортирует трассы и рецензии из архива (ZIP) или JSON в каталог трасс и базу SQLite."""
    imported_traces = 0
    imported_reviews = 0
    errors = []

    local_base = _get_repo_root() / 'tmp' / 'traces'
    local_base.mkdir(parents=True, exist_ok=True)
    REVIEWS.mkdir(parents=True, exist_ok=True)

    def process_single_item(item: dict, source_name: str):
        nonlocal imported_traces, imported_reviews
        identifier = str(item.get('id') or '')
        if not ID.fullmatch(identifier):
            return

        if 'stage' in item and ('expected_answer' in item or 'evidence' in item) and 'events' not in item:
            target = REVIEWS / f"{identifier}.json"
            target.write_text(json.dumps(item, ensure_ascii=False), encoding='utf-8')
            imported_reviews += 1
            return

        agent = str(item.get('agent') or 'main')
        target_dir = local_base / agent
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{identifier}.json"
        target.write_text(json.dumps(item, ensure_ascii=False), encoding='utf-8')

        if isinstance(item.get('review'), dict) and any(item['review'].values()):
            rev_target = REVIEWS / f"{identifier}.json"
            rev_target.write_text(json.dumps(item['review'], ensure_ascii=False), encoding='utf-8')
            imported_reviews += 1

        imported_traces += 1

    def process_trace_json(raw_text: str, source_name: str):
        try:
            parsed = json.loads(raw_text)
        except Exception as e:
            errors.append(f"Ошибка парсинга {source_name}: {e}")
            return

        if isinstance(parsed, list):
            for idx, el in enumerate(parsed):
                if isinstance(el, dict):
                    process_single_item(el, f"{source_name}[{idx}]")
        elif isinstance(parsed, dict) and 'items' in parsed and isinstance(parsed['items'], list):
            for idx, el in enumerate(parsed['items']):
                if isinstance(el, dict):
                    process_single_item(el, f"{source_name}.items[{idx}]")
        elif isinstance(parsed, dict):
            process_single_item(parsed, source_name)

    if data.startswith(b'PK\x03\x04') or data.startswith(b'PK\x05\x06'):
        try:
            with zipfile.ZipFile(io.BytesIO(data), 'r') as zf:
                for member in zf.infolist():
                    if member.is_dir():
                        continue
                    name = member.filename
                    if '..' in name or name.startswith('/') or name.startswith('\\'):
                        continue
                    if not name.endswith('.json'):
                        continue
                    try:
                        content_str = zf.read(member).decode('utf-8')
                        process_trace_json(content_str, name)
                    except Exception as e:
                        errors.append(f"Ошибка извлечения {name}: {e}")
        except Exception as e:
            return {'status': 'error', 'detail': f'Некорректный ZIP-архив: {e}'}
    else:
        try:
            text = data.decode('utf-8')
            process_trace_json(text, 'upload.json')
        except Exception as e:
            return {'status': 'error', 'detail': f'Некорректный формат архива (ожидался ZIP или JSON): {e}'}

    if imported_traces > 0 or imported_reviews > 0:
        with LOCK:
            CACHE.clear()
            summaries()
        notify_subscribers('traces_imported', {
            'imported_traces': imported_traces,
            'imported_reviews': imported_reviews,
        })

    return {
        'status': 'ok',
        'imported_traces': imported_traces,
        'imported_reviews': imported_reviews,
        'errors_count': len(errors),
        'errors': errors[:10] if errors else [],
    }


def summaries(search_query=None, full=False):
    with LOCK:
        existing = set()
        changed = []
        for root in ROOTS:
            for path in root.glob('*.json'):
                if not ID.fullmatch(path.stem): continue
                try:
                    existing.add(str(path))
                    stamp = path.stat().st_mtime_ns
                    if str(path) not in CACHE or CACHE[str(path)][0] != stamp:
                        item = read_json(path)
                        events = item.get('events', [])
                        summary = {k: item.get(k) for k in ('id', 'agent', 'question', 'thread_id', 'started_at', 'duration_ms', 'status', 'error')}
                        summary['configuration'] = item.get('configuration', {})
                        summary['answer'] = next((e.get('reply') for e in reversed(events) if e.get('stage') == 'answer'), None)
                        summary['sources'] = next((e.get('sources') for e in reversed(events) if e.get('stage') == 'answer'), [])
                        summary['searches'] = sum(1 for e in events if is_search_event(e))
                        summary['model_calls'] = sum(e.get('stage') == 'context' for e in events)
                        summary['event_count'] = len(events)
                        total_tokens = 0
                        has_usage = False
                        for e in events:
                            usage = e.get('usage')
                            if isinstance(usage, dict):
                                tokens = usage.get('total_tokens') or (
                                    (usage.get('input_tokens') or usage.get('prompt_tokens') or 0) +
                                    (usage.get('output_tokens') or usage.get('completion_tokens') or 0)
                                )
                                if tokens:
                                    total_tokens += tokens
                                    has_usage = True
                        summary['total_tokens'] = total_tokens if has_usage else None
                        summary['mtime_ns'] = stamp
                        CACHE[str(path)] = (stamp, summary)
                        changed.append(summary)
                except (OSError, ValueError, KeyError, TypeError): continue
        for key in list(CACHE):
            if key not in existing: del CACHE[key]

        if changed:
            conn = None
            try:
                conn = get_db()
                for s in changed:
                    conn.execute('''
                        INSERT OR REPLACE INTO traces (
                            id, agent, question, thread_id, started_at, duration_ms,
                            status, error, searches, model_calls, event_count, total_tokens, mtime_ns
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        s.get('id'), s.get('agent'), s.get('question'), s.get('thread_id'),
                        s.get('started_at'), s.get('duration_ms'), s.get('status'), s.get('error'),
                        s.get('searches'), s.get('model_calls'), s.get('event_count'),
                        s.get('total_tokens'), s.get('mtime_ns')
                    ))
                conn.execute("INSERT INTO traces_fts(traces_fts) VALUES ('rebuild');")
                conn.commit()
            except Exception:
                pass
            finally:
                if conn:
                    conn.close()

        if full:
            rows = []
            for path_str, (stamp, summ) in CACHE.items():
                try:
                    full_item = read_json(Path(path_str))
                    full_item['review'] = review(full_item['id'])
                    rows.append(full_item)
                except Exception:
                    rows.append({**summ, 'review': review(summ['id'])})
        else:
            rows = [{**value[1], 'review': review(value[1]['id'])} for value in CACHE.values()]

        if search_query:
            matched_ids = search_fts(search_query)
            if matched_ids:
                rows = [r for r in rows if r['id'] in matched_ids]
            else:
                q_lower = search_query.lower()
                rows = [r for r in rows if q_lower in (r.get('question') or '').lower() or q_lower in (r.get('error') or '').lower()]

        return sorted(rows, key=lambda row: row.get('started_at') or '', reverse=True)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_): pass  # Никогда не помещать вопросы или контент в логи доступа.

    def allowed(self, write=False):
        host = self.headers.get('Host', '')
        port = os.getenv('DIAGNOSTICS_PORT', '3010')
        valid_hosts = {'localhost:3010', '127.0.0.1:3010', '[::1]:3010', f'localhost:{port}', f'127.0.0.1:{port}', f'[::1]:{port}'}
        if host not in valid_hosts:
            self.send_json(403, {'detail': f'Откройте сайт через localhost:{port}'})
            return False
        if write and self.headers.get('Origin') and self.headers.get('Origin') != 'http://'+host:
            self.send_json(403, {'detail': 'Запись разрешена только с локальной страницы'})
            return False
        return True

    def send_data(self, status, body, kind):
        self.send_response(status)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, status, value):
        self.send_data(status, json.dumps(value, ensure_ascii=False).encode(), 'application/json; charset=utf-8')

    def do_GET(self):
        if not self.allowed(): return
        url = urlsplit(self.path)
        params = parse_qs(url.query)

        if url.path == '/api/traces/stream':
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
            self.send_header('Cache-Control', 'no-cache, no-transform')
            self.send_header('Connection', 'keep-alive')
            self.send_header('X-Accel-Buffering', 'no')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()

            client_queue = queue.Queue(maxsize=100)
            with SUBSCRIBERS_LOCK:
                SUBSCRIBERS.append(client_queue)
            try:
                self.wfile.write(b'event: ping\ndata: {"status": "connected"}\n\n')
                self.wfile.flush()
                while not getattr(self.server, '_is_shut_down', threading.Event()).is_set():
                    try:
                        msg = client_queue.get(timeout=15.0)
                        self.wfile.write(f'data: {json.dumps(msg, ensure_ascii=False)}\n\n'.encode('utf-8'))
                        self.wfile.flush()
                    except queue.Empty:
                        self.wfile.write(b': heartbeat\n\n')
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                with SUBSCRIBERS_LOCK:
                    if client_queue in SUBSCRIBERS:
                        SUBSCRIBERS.remove(client_queue)
            return

        if url.path == '/api/traces':
            q = params.get('q', [None])[0]
            full_flag = params.get('full', ['0'])[0].lower() in ('1', 'true', 'yes')
            rows = summaries(search_query=q, full=full_flag)
            self.send_json(200, {'items': rows, 'retention': '7 дней, до 1000 запросов на сервис'})
        elif url.path == '/api/traces/export':
            rows = summaries(search_query=params.get('q', [None])[0], full=True)
            self.send_json(200, {
                'count': len(rows),
                'items': rows,
                'exported_at': datetime.now(timezone.utc).isoformat()
            })
        elif url.path in ('/api/traces/archive', '/api/traces/download-zip'):
            zip_bytes = create_traces_zip()
            timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
            filename = f"viio_traces_{timestamp}.zip"
            self.send_response(200)
            self.send_header('Content-Type', 'application/zip')
            self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
            self.send_header('Content-Length', str(len(zip_bytes)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(zip_bytes)
            return
        elif url.path == '/api/reviews/export-golden-set':
            dataset = export_golden_set()
            self.send_json(200, {
                'count': len(dataset),
                'items': dataset,
                'exported_at': datetime.now(timezone.utc).isoformat()
            })
        elif url.path.startswith('/api/traces/'):
            identifier = url.path.rsplit('/', 1)[-1]
            path = trace_path(identifier)
            if not path: return self.send_json(404, {'detail': 'Трасса не найдена или срок хранения истёк'})
            try: self.send_json(200, {**read_json(path), 'review': review(identifier)})
            except (OSError, ValueError): self.send_json(503, {'detail': 'Не удалось прочитать трассу. Обновите журнал.'})
        elif url.path == '/health': self.send_json(200, {'status': 'ok'})
        else:
            path = (STATIC / ('index.html' if url.path == '/' else url.path.lstrip('/'))).resolve()
            if not path.is_relative_to(STATIC.resolve()) or not path.is_file():
                return self.send_json(404, {'detail': 'Страница не найдена'})
            kind = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
            self.send_data(200, path.read_bytes(), kind+'; charset=utf-8')

    def do_POST(self):
        if not self.allowed(write=True): return
        url = urlsplit(self.path)

        if url.path == '/api/traces/import-archive':
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if size <= 0 or size > 250_000_000:
                    return self.send_json(400, {'detail': 'Размер архива должен быть до 250 МБ'})
                body = self.rfile.read(size)
                result = import_traces_from_bytes(body)
                status_code = 200 if result.get('status') == 'ok' else 400
                return self.send_json(status_code, result)
            except Exception as e:
                return self.send_json(500, {'detail': f'Сбой импорта архива: {e}'})

        identifier = url.path.removeprefix('/api/reviews/')
        if self.path != '/api/reviews/'+identifier or not trace_path(identifier):
            return self.send_json(404, {'detail': 'Трасса не найдена'})
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 128000: raise ValueError()
            data = json.loads(self.rfile.read(size))
            fields = ('stage', 'expected_answer', 'evidence', 'document_id', 'notes')
            if not isinstance(data, dict) or any(not isinstance(data.get(key, ''), str) or len(data.get(key, '')) > 6000 for key in fields): raise ValueError()
            allowed_stages = (
                '', 'correct', 'route', 'retrieval', 'graph', 'selection', 'context',
                'generation', 'database', 'coding', 'ocr', 'guard', 'unknown'
            )
            if data.get('stage', '') not in allowed_stages: raise ValueError()
            if data.get('score') not in (None, 1, 2, 3, 4, 5) or isinstance(data.get('score'), bool): raise ValueError()
            saved = {key: data.get(key, '') for key in fields}
            saved['score'] = data.get('score')
            with LOCK:
                REVIEWS.mkdir(parents=True, exist_ok=True)
                target = REVIEWS / (identifier+'.json')
                temporary = target.with_suffix('.tmp')
                temporary.write_text(json.dumps(saved, ensure_ascii=False), encoding='utf-8')
                temporary.replace(target)
            notify_subscribers('review_saved', {'id': identifier, 'review': saved})
            self.send_json(200, saved)
        except (ValueError, TypeError): self.send_json(400, {'detail': 'Проверьте оценку и размер полей (до 6000 символов)'})
        except OSError: self.send_json(503, {'detail': 'Не удалось сохранить разбор. Повторите попытку.'})


if __name__ == '__main__':
    ThreadingHTTPServer((os.getenv('BIND_HOST', '127.0.0.1'), 3010), Handler).serve_forever()
