"""Loopback-only trace viewer; stdlib runtime, no access to VIIO databases/keys."""
import json
import os
import re
import threading
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

STATIC = Path(__file__).parent / 'dist'
ROOTS = [Path(p) for p in os.getenv('TRACE_ROOTS', '/traces/main,/traces/consultant').split(',')]
REVIEWS = Path(os.getenv('REVIEW_ROOT', '/data/reviews'))
ID = re.compile(r'^[a-f0-9]{32}$')
LOCK = threading.RLock()
CACHE = {}


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def trace_path(identifier):
    if not ID.fullmatch(identifier): return None
    return next((root / (identifier+'.json') for root in ROOTS if (root / (identifier+'.json')).is_file()), None)


def review(identifier):
    try: return read_json(REVIEWS / (identifier+'.json'))
    except (OSError, ValueError): return {}


def summaries():
    with LOCK:
        existing = set()
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
                        summary['searches'] = sum(e['stage'] == 'retrieval' for e in events)
                        summary['model_calls'] = sum(e['stage'] == 'context' for e in events)
                        summary['event_count'] = len(events)
                        CACHE[str(path)] = (stamp, summary)
                except (OSError, ValueError, KeyError, TypeError): continue
        for key in list(CACHE):
            if key not in existing: del CACHE[key]
        return sorted([{**value[1], 'review': review(value[1]['id'])} for value in CACHE.values()], key=lambda row: row['started_at'], reverse=True)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_): pass  # Never put questions or content in access logs.

    def allowed(self, write=False):
        host = self.headers.get('Host', '')
        if host not in {'localhost:3010', '127.0.0.1:3010', '[::1]:3010'}:
            self.send_json(403, {'detail': 'Откройте сайт через localhost:3010'})
            return False
        if write and self.headers.get('Origin') != 'http://'+host:
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
        if url.path == '/api/traces':
            rows = summaries()
            self.send_json(200, {'items': rows, 'retention': '7 дней, до 1000 запросов на агента'})
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
        identifier = urlsplit(self.path).path.removeprefix('/api/reviews/')
        if self.path != '/api/reviews/'+identifier or not trace_path(identifier):
            return self.send_json(404, {'detail': 'Трасса не найдена'})
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 128000: raise ValueError()
            data = json.loads(self.rfile.read(size))
            fields = ('stage', 'expected_answer', 'evidence', 'document_id', 'notes')
            if not isinstance(data, dict) or any(not isinstance(data.get(key, ''), str) or len(data.get(key, '')) > 6000 for key in fields): raise ValueError()
            if data.get('stage', '') not in ('', 'correct', 'route', 'retrieval', 'selection', 'context', 'generation', 'unknown'): raise ValueError()
            if data.get('score') not in (None, 1, 2, 3, 4, 5) or isinstance(data.get('score'), bool): raise ValueError()
            saved = {key: data.get(key, '') for key in fields}
            saved['score'] = data.get('score')
            with LOCK:
                REVIEWS.mkdir(parents=True, exist_ok=True)
                target = REVIEWS / (identifier+'.json')
                temporary = target.with_suffix('.tmp')
                temporary.write_text(json.dumps(saved, ensure_ascii=False), encoding='utf-8')
                temporary.replace(target)
            self.send_json(200, saved)
        except (ValueError, TypeError): self.send_json(400, {'detail': 'Проверьте оценку и размер полей (до 6000 символов)'})
        except OSError: self.send_json(503, {'detail': 'Не удалось сохранить разбор. Повторите попытку.'})


if __name__ == '__main__':
    ThreadingHTTPServer((os.getenv('BIND_HOST', '127.0.0.1'), 3010), Handler).serve_forever()
