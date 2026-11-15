"""Bind the approved cloud corpus to exact active VIIO source document versions."""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

root = Path(__file__).resolve().parents[2]
state = json.loads((root / 'RAG/state/yandex-managed-rag-test-state.json').read_text(encoding='utf-8'))
approval = json.loads((root / 'docs/yandex-public-canonical-approval.json').read_text(encoding='utf-8'))
imported = json.loads((root / 'docs/public-canonical-import-2026-11-02.json').read_text(encoding='utf-8'))
by_name = {row['file']: row for row in imported['results']}
files = []
for item in approval['files']:
    path = Path(item['path'])
    assert path.parent.resolve() == (root / 'data/gosapteka56/2026-10-27/canonical').resolve()
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    assert digest == item['sha256'] == by_name[path.name]['sha256'], path.name
    title = next(line.lstrip('# ').strip() for line in raw.decode('utf-8-sig').splitlines() if line.strip())
    files.append({'file_id': state['files'][path.name], 'document_id': by_name[path.name]['document_id'],
                  'sha256': digest, 'title': title})
assert len(files) == len({item['file_id'] for item in files}) == 41
expires = datetime.fromisoformat(state['created_at']) + timedelta(days=state['ttl_days'])
assert expires > datetime.now(timezone.utc), 'The test index has expired'
target = root / 'RAG/state/yandex-rag-runtime.json'
target.write_text(json.dumps({'index_id': state['index_id'], 'expires_at': expires.isoformat(),
    'files': files}, ensure_ascii=False, indent=2), encoding='utf-8')
print('Prepared public runtime manifest for 41 files; index:', state['index_id'])
