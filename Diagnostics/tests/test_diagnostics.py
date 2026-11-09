import asyncio
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from Diagnostics import collector, server


class CollectorTests(unittest.TestCase):
    def test_concurrent_requests_are_isolated_and_do_not_capture_secrets(self):
        async def request(question):
            with collector.capture('main', question):
                await asyncio.sleep(0)
                collector.record('answer', reply=question)

        async def run():
            await asyncio.gather(request('one'), request('two'))

        with tempfile.TemporaryDirectory() as directory, patch.dict('os.environ', {'VIIO_TRACE_DIR': directory, 'LMSTUDIO_API_KEY': 'SECRET'}):
            asyncio.run(run())
            traces = [json.loads(path.read_text(encoding='utf-8')) for path in Path(directory).glob('*.json')]
            self.assertEqual(len(traces), 2)
            for trace in traces:
                self.assertEqual(trace['events'][0]['reply'], trace['question'])
                self.assertNotIn('SECRET', json.dumps(trace))
            self.assertIsNone(collector._current.get())

    def test_error_preserves_events_and_write_failure_does_not_replace_error(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict('os.environ', {'VIIO_TRACE_DIR': directory}):
            with self.assertRaises(TimeoutError):
                with collector.capture('consultant', 'question'):
                    collector.record('route', mode='published')
                    raise TimeoutError('PRIVATE EXCEPTION DETAILS')
            trace = json.loads(next(Path(directory).glob('*.json')).read_text(encoding='utf-8'))
            self.assertEqual(trace['status'], 'error')
            self.assertEqual(trace['error'], 'TimeoutError')
            self.assertNotIn('PRIVATE EXCEPTION DETAILS', json.dumps(trace))
            file = Path(directory)/'not-a-directory'
            file.write_text('x')
            with patch.dict('os.environ', {'VIIO_TRACE_DIR': str(file)}):
                with collector.capture('main', 'question'):
                    pass  # Переполненный или недоступный для записи том трассировки не должен ломать чат.

    def test_model_capture_is_visible_input_output_only_and_keeps_behavior(self):
        class Model:
            model_name = 'test-model'
            async def ainvoke(self, messages):
                return SimpleNamespace(content='answer', tool_calls=[], usage_metadata={'input_tokens': 10, 'output_tokens': 5, 'total_tokens': 15}, response_metadata={'reasoning': 'HIDDEN'})

        with tempfile.TemporaryDirectory() as directory, patch.dict('os.environ', {'VIIO_TRACE_DIR': directory}):
            with collector.capture('main', 'question'):
                result = asyncio.run(collector.model_call(Model(), [SimpleNamespace(type='human', content='question')]))
            self.assertEqual(result.content, 'answer')
            trace = json.loads(next(Path(directory).glob('*.json')).read_text(encoding='utf-8'))
            self.assertEqual([event['stage'] for event in trace['events']], ['context', 'generation'])
            self.assertEqual(trace['events'][0]['model'], 'test-model')
            self.assertEqual(trace['events'][1]['usage'], {'input_tokens': 10, 'output_tokens': 5, 'total_tokens': 15})
            self.assertNotIn('HIDDEN', json.dumps(trace))

    def test_disabled_diagnostics_writes_nothing(self):
        with patch.dict('os.environ', {'VIIO_TRACE_DIR': ''}):
            with collector.capture('main', 'question') as trace:
                self.assertIsNone(trace)
                collector.record('answer', reply='ok')


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.traces = root/'traces'
        self.traces.mkdir()
        self.identifier = 'a'*32
        (self.traces/(self.identifier+'.json')).write_text(json.dumps(dict(
            id=self.identifier, agent='main', question='<script>alert(1)</script>', started_at='2026-10-28T00:00:00Z',
            duration_ms=100, status='ok', events=[dict(stage='answer', reply='answer')])) ,encoding='utf-8')
        self.roots = patch.object(server, 'ROOTS', [self.traces]); self.roots.start()
        self.reviews = patch.object(server, 'REVIEWS', root/'reviews'); self.reviews.start()
        server.CACHE.clear()
        self.http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True); self.thread.start()
        self.base = f'http://127.0.0.1:{self.http.server_port}'

    def tearDown(self):
        self.http.shutdown(); self.http.server_close(); self.thread.join()
        self.roots.stop(); self.reviews.stop(); self.temporary.cleanup()

    def call(self, path, data=None, host='localhost:3010', origin='http://localhost:3010'):
        headers = {'Host': host, 'Origin': origin, 'Content-Type': 'application/json'}
        request = Request(self.base+path, data=json.dumps(data).encode() if data is not None else None, headers=headers)
        return urlopen(request)

    def test_index_omits_content_and_review_roundtrip_is_durable(self):
        with self.call('/api/traces') as response:
            item = json.load(response)['items'][0]
            self.assertNotIn('events', item)
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
        with self.call('/api/reviews/'+self.identifier, {'stage': 'retrieval', 'score': 2, 'notes': 'missing instruction'}) as response:
            self.assertEqual(json.load(response)['score'], 2)
        with self.call('/api/traces/'+self.identifier) as response:
            self.assertEqual(json.load(response)['review']['stage'], 'retrieval')
        self.assertTrue((server.REVIEWS/(self.identifier+'.json')).is_file())

    def test_rebinding_cross_origin_and_path_traversal_are_blocked(self):
        for path, data, headers, status in [
            ('/api/traces', None, {'host': 'attacker.example:3010'}, 403),
            ('/api/reviews/'+self.identifier, {'stage':'correct'}, {'origin':'http://attacker.example'}, 403),
            ('/../server.py', None, {}, 404),
            ('/api/traces/../../server.py', None, {}, 404),
            ('/api/reviews/'+self.identifier, {'stage':'invalid'}, {}, 400),
            ('/api/reviews/'+self.identifier, {'score':True}, {}, 400),
        ]:
            with self.subTest(path=path, headers=headers):
                with self.assertRaises(HTTPError) as result: self.call(path, data, **headers)
                self.assertEqual(result.exception.code, status)

    def test_static_react_assets_are_served_with_csp(self):
        with self.call('/') as response:
            body = response.read().decode()
            self.assertIn('/assets/', body)
            self.assertIn("frame-ancestors 'none'", response.headers['Content-Security-Policy'])


if __name__ == '__main__': unittest.main()
