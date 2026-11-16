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

import sys
_diag_dir = Path(__file__).resolve().parent.parent
if str(_diag_dir) not in sys.path:
    sys.path.insert(0, str(_diag_dir))
try:
    from Diagnostics import collector, server
except ImportError:
    import collector, server


class CollectorTests(unittest.TestCase):
    def test_running_snapshot_and_event_limit_are_visible(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict('os.environ', {'VIIO_TRACE_DIR': directory}):
            with collector.capture('main', 'question') as trace:
                path = Path(directory) / (trace['id'] + '.json')
                self.assertEqual(json.loads(path.read_text())['status'], 'running')
                for i in range(102):
                    collector.record('step', index=i)
                saved = json.loads(path.read_text())
                self.assertEqual(len(saved['events']), 100)
                self.assertEqual(saved['dropped_events'], 2)
            self.assertEqual(json.loads(path.read_text())['status'], 'ok')

    def test_cancelled_request_preserves_snapshot(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict('os.environ', {'VIIO_TRACE_DIR': directory}):
            with self.assertRaises(asyncio.CancelledError):
                with collector.capture('main', 'question'):
                    collector.record('tool_start', name='test')
                    raise asyncio.CancelledError()
            trace = json.loads(next(Path(directory).glob('*.json')).read_text())
            self.assertEqual(trace['status'], 'cancelled')
            self.assertEqual(trace['events'][0]['name'], 'test')

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
                collector.record('step')
                @collector.trace_event("Тест отключенной трассировки")
                def sample_fn():
                    return 42
                self.assertEqual(sample_fn(), 42)

    def test_trace_event_decorator_and_context_manager(self):
        @collector.trace_event("БД: Генерация SQL-запроса")
        async def generate_sql_node(query):
            collector.set_event_data(query=query)
            return f"SELECT * FROM ({query})"

        @collector.trace_event("БД: Проверка плана EXPLAIN", stage="db_explain_check")
        def explain_check(sql):
            return {"valid": True, "sql": sql}

        @collector.trace_event("Сбойная нода", stage="failing_stage")
        async def failing_node():
            raise RuntimeError("DB connection dropped")

        with tempfile.TemporaryDirectory() as directory, patch.dict('os.environ', {'VIIO_TRACE_DIR': directory}):
            with collector.capture('main', 'question'):
                asyncio.run(generate_sql_node("users"))
                explain_check("SELECT 1")
                with collector.trace_event("БД: Инспекция схемы", stage="db_inspect_schema") as ev:
                    ev.set(tables=["users", "orders"])
                with self.assertRaises(RuntimeError):
                    asyncio.run(failing_node())

            saved = json.loads(next(Path(directory).glob('*.json')).read_text(encoding='utf-8'))
            events = saved['events']
            self.assertEqual(len(events), 4)

            # Event 1: async decorator with inferred stage from func name
            self.assertEqual(events[0]['stage'], 'generate_sql_node')
            self.assertEqual(events[0]['title'], 'БД: Генерация SQL-запроса')
            self.assertEqual(events[0]['query'], 'users')
            self.assertEqual(events[0]['status'], 'ok')
            self.assertIn('duration_ms', events[0])

            # Event 2: sync decorator with explicit stage
            self.assertEqual(events[1]['stage'], 'db_explain_check')
            self.assertEqual(events[1]['title'], 'БД: Проверка плана EXPLAIN')
            self.assertEqual(events[1]['status'], 'ok')

            # Event 3: context manager
            self.assertEqual(events[2]['stage'], 'db_inspect_schema')
            self.assertEqual(events[2]['title'], 'БД: Инспекция схемы')
            self.assertEqual(events[2]['tables'], ["users", "orders"])

            # Event 4: error capturing
            self.assertEqual(events[3]['stage'], 'failing_stage')
            self.assertEqual(events[3]['title'], 'Сбойная нода')
            self.assertEqual(events[3]['status'], 'error')
            self.assertEqual(events[3]['error'], 'RuntimeError')

    def test_spans_hierarchy_and_distributed_headers(self):
        @collector.trace_event("Вложенный шаг", stage="child_step")
        def child_fn():
            headers = collector.trace_headers()
            return headers

        @collector.trace_event("Родительская нода", stage="parent_node")
        def parent_fn():
            return child_fn()

        with tempfile.TemporaryDirectory() as directory, patch.dict('os.environ', {'VIIO_TRACE_DIR': directory}):
            with collector.capture('main', 'question', trace_id='custom-trace-id-123', parent_trace_id='upstream-parent-456') as trace:
                headers = parent_fn()
                self.assertEqual(headers['X-Trace-ID'], 'custom-trace-id-123')
                self.assertIn('X-Parent-Span-ID', headers)

            saved = json.loads(next(Path(directory).glob('*.json')).read_text(encoding='utf-8'))
            self.assertEqual(saved['id'], 'custom-trace-id-123')
            self.assertEqual(saved['parent_trace_id'], 'upstream-parent-456')
            events = saved['events']
            self.assertEqual(len(events), 2)
            child_event = events[0]
            parent_event = events[1]
            self.assertEqual(child_event['stage'], 'child_step')
            self.assertEqual(parent_event['stage'], 'parent_node')
            # The child's parent_span_id must equal the parent's span_id
            self.assertEqual(child_event['parent_span_id'], parent_event['span_id'])


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

    def test_search_counting_and_safe_sorting_and_new_review_stages(self):
        # 1. New review stages (database, coding, etc.) are accepted
        with self.call('/api/reviews/'+self.identifier, {'stage': 'database', 'score': 4, 'notes': 'sql explain issue'}) as response:
            self.assertEqual(json.load(response)['stage'], 'database')

        # 2. Add second trace with CRAG and tool searches and missing started_at to test safe sorting
        second_id = 'b'*32
        (self.traces/(second_id+'.json')).write_text(json.dumps(dict(
            id=second_id, agent='main', question='CRAG test', started_at=None,
            duration_ms=50, status='ok', events=[
                dict(stage='crag_retrieve', chars=100),
                dict(stage='tool_start', name='search_knowledge_base'),
                dict(stage='tool_end', name='search_knowledge_base'),
                dict(stage='context', model='gpt-4o'),
            ])), encoding='utf-8')

        with self.call('/api/traces') as response:
            items = json.load(response)['items']
            self.assertEqual(len(items), 2)
            crag_item = next(it for it in items if it['id'] == second_id)
            # Both crag_retrieve and tool_start search_knowledge_base are counted as searches
            self.assertEqual(crag_item['searches'], 2)
            self.assertEqual(crag_item['model_calls'], 1)

    def test_golden_set_export_and_fts_search(self):
        # 1. Save a review with expected answer
        self.call('/api/reviews/'+self.identifier, {
            'stage': 'correct',
            'score': 5,
            'expected_answer': 'ИСЛО — это система лекарственного обеспечения',
            'evidence': 'Раздел 1 стр 5',
            'document_id': 'doc-1',
            'notes': 'verified'
        })

        # 2. Test export golden set
        with self.call('/api/reviews/export-golden-set') as response:
            data = json.load(response)
            self.assertEqual(data['count'], 1)
            item = data['items'][0]
            self.assertEqual(item['id'], self.identifier)
            self.assertEqual(item['expected_answer'], 'ИСЛО — это система лекарственного обеспечения')
            self.assertEqual(item['score'], 5)
            self.assertEqual(item['stage'], 'correct')

        # 3. Test FTS search
        with self.call('/api/traces?q=alert') as response:
            items = json.load(response)['items']
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]['id'], self.identifier)

        with self.call('/api/traces?q=nonexistentwordxyz') as response:
            items = json.load(response)['items']
            self.assertEqual(len(items), 0)

    def test_static_react_assets_are_served_with_csp(self):
        with self.call('/') as response:
            body = response.read().decode()
            self.assertIn('/assets/', body)
            self.assertIn("frame-ancestors 'none'", response.headers['Content-Security-Policy'])


if __name__ == '__main__': unittest.main()
