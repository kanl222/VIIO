"""Approve only current publication text contained in user-approved canonical files."""
import hashlib
import json
import pathlib
import subprocess
import unicodedata

ROOT = pathlib.Path(__file__).resolve().parents[2]


def normalized(text):
    return ' '.join(unicodedata.normalize('NFC', text).split())


def main():
    approval = json.loads((ROOT / 'docs/yandex-public-canonical-approval.json').read_text(encoding='utf-8'))
    canonical = []
    for item in approval['files']:
        path = pathlib.Path(item['path'])
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != item['sha256']:
            raise ValueError('Approved file changed: ' + path.name)
        canonical.append((path.name, normalized(raw.decode('utf-8-sig'))))
    sql = """BEGIN READ ONLY;
    SELECT coalesce(json_agg(row_to_json(x)), '[]'::json) FROM (
      SELECT p.id::text,p.document_id::text,p.title,p.content FROM document_publications p
      JOIN documents d ON d.id=p.document_id
      WHERE d.is_active AND d.status IN ('ready','indexed') AND d.document_kind<>'code'
    ) x; ROLLBACK;"""
    run = subprocess.run(['docker', 'exec', 'viio_postgres', 'psql', '-U', 'postgres',
        '-d', 'agent_db', '-Atq', '-c', sql], capture_output=True, check=True)
    rows = json.loads(run.stdout.decode('utf-8'))
    matched, unmatched = [], []
    for row in rows:
        body = normalized(row['content'])
        # Require complete publication text and title in one approved file.
        matches = [name for name, text in canonical if body and body in text and normalized(row['title']) in text]
        if matches:
            matched.append({'publication_id': row['id'], 'document_id': row['document_id'],
                'canonical_files': matches, 'sha256': hashlib.sha256(
                    (row['title'] + '\n' + row['content']).encode()).hexdigest()})
        else:
            unmatched.append({'publication_id': row['id'], 'title': row['title']})
    report = {'matched': matched, 'unmatched': unmatched}
    (ROOT / 'docs/yandex-publication-matches.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'matched': len(matched), 'unmatched': len(unmatched)}))
    if matched:
        sample = next(row for row in rows if row['id'] == matched[0]['publication_id'])
        (ROOT / 'tmp/yandex-approved-publication-test.json').write_text(
            json.dumps({k: sample[k] for k in ('id', 'title', 'content')}, ensure_ascii=False), encoding='utf-8')
        env = ROOT / '.env'
        content = env.read_text(encoding='utf-8')
        key = 'CONSULTANT_CLOUD_PUBLIC_MATERIAL_HASHES='
        replacement = key + json.dumps([row['sha256'] for row in matched])
        lines = content.splitlines()
        lines = [replacement if line.startswith(key) else line for line in lines]
        if not any(line.startswith(key) for line in content.splitlines()):
            lines.append(replacement)
        env.write_text('\n'.join(lines) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
