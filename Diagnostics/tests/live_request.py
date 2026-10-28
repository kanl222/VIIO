"""Run inside one agent container; print only non-sensitive verification metadata."""
import json
import os
import uuid
from pathlib import Path
import httpx

agent = os.environ.get('DIAGNOSTIC_AGENT', 'main')
question = 'Как оформить начальные остатки в Госаптеке?'
payload = {'message': question}
if agent == 'main':
    route = '/api/v1/chat'
    headers = {'X-Internal-Token': os.environ['CHAT_API_KEY']}
    payload.update(thread_id='diagnostics-check-' + uuid.uuid4().hex,
                   user_context={'user_id': 'diagnostics-check', 'role': 'employee', 'departments': ['general']})
else:
    route = '/api/v1/consultant/chat'
    headers = {'X-Consultant-Key': os.environ['CONSULTANT_API_KEY']}
response = httpx.post('http://127.0.0.1:8000'+route, headers=headers, json=payload, timeout=120, trust_env=False)
rows = [json.loads(path.read_text()) for path in Path('/traces').glob('*.json')]
rows = sorted((row for row in rows if row['question'] == question), key=lambda row: row['started_at'])
trace = rows[-1] if rows else {}
print(json.dumps({'agent':agent, 'http_status':response.status_code, 'trace_id':trace.get('id'),
    'events':[event['stage'] for event in trace.get('events', [])],
    'retrievals':[{ 'candidates':len(event.get('candidates',[])), 'selected':len(event.get('selected',[])) }
                   for event in trace.get('events',[]) if event['stage']=='retrieval']}, ensure_ascii=False))
if response.status_code != 200 or not trace.get('events'):
    raise SystemExit(1)
