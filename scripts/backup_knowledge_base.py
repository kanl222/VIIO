"""Local KB backup: PostgreSQL dumps, Qdrant snapshots, Memgraph JSON, originals.

Run with the project virtualenv. Requires Docker access and local .env.
No credentials are written into the backup manifest.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import urllib.request
from dotenv import dotenv_values
from neo4j import GraphDatabase

ROOT = Path(__file__).resolve().parents[1]

def backup(output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    config = dotenv_values(ROOT / '.env')
    manifest = {'created_at': datetime.now(timezone.utc).isoformat(), 'files': {}, 'qdrant': {}}
    def run_file(args, name):
        with (output / name).open('wb') as handle:
            subprocess.run(args, stdout=handle, check=True)
    with GraphDatabase.driver(config.get('MEMGRAPH_URI_HOST', 'bolt://127.0.0.1:7687'), auth=None) as driver:
        with driver.session() as session, session.begin_transaction() as tx:
            graph = {'nodes': tx.run('MATCH (n) RETURN id(n) AS id, labels(n) AS labels, properties(n) AS properties').data(),
                     'edges': tx.run('MATCH (a)-[r]->(b) RETURN id(a) AS source, id(b) AS target, type(r) AS type, properties(r) AS properties').data()}
    (output / 'memgraph.json').write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding='utf8')
    manifest['graph'] = {'nodes': len(graph['nodes']), 'edges': len(graph['edges'])}
    user = config.get('POSTGRES_USER', 'postgres')
    for db in ('agent_db', 'user_db'):
        run_file(['docker', 'exec', 'viio_postgres', 'pg_dump', '-U', user, '-Fc', db], db + '.dump')
        with (output / (db + '.dump')).open('rb') as handle:
            subprocess.run(['docker', 'exec', '-i', 'viio_postgres', 'pg_restore', '--list'],
                           stdin=handle, stdout=subprocess.DEVNULL, check=True)
    headers = {'api-key': config['QDRANT_API_KEY']}
    base = 'http://127.0.0.1:' + str(config.get('QDRANT_PORT', '6333'))
    def request(path, method='GET'):
        return urllib.request.urlopen(urllib.request.Request(base + path, headers=headers, method=method), timeout=180)
    with request('/collections') as response:
        collections = json.load(response)['result']['collections']
    for collection in collections:
        name = collection['name']
        with request('/collections/' + name) as response:
            manifest['qdrant'][name] = json.load(response)['result']['points_count']
        with request('/collections/' + name + '/snapshots', 'POST') as response:
            snapshot = json.load(response)['result']['name']
        filename = 'qdrant-' + name + '.snapshot'
        with request('/collections/' + name + '/snapshots/' + snapshot) as response, (output / filename).open('wb') as target:
            import shutil
            shutil.copyfileobj(response, target)
    with request('/aliases') as response:
        (output / 'qdrant-aliases.json').write_bytes(response.read())
    run_file(['docker', 'exec', 'viio_documents', 'tar', '-czf', '-', '-C', '/data', 'documents'], 'documents.tar.gz')
    import shutil
    shutil.copytree(ROOT / 'data/knowledge_graph', output / 'knowledge_graph')
    for path in output.rglob('*'):
        if path.is_file():
            digest = hashlib.sha256()
            with path.open('rb') as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b''):
                    digest.update(block)
            manifest['files'][str(path.relative_to(output))] = {'bytes': path.stat().st_size, 'sha256': digest.hexdigest()}
    (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf8')
    return manifest

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = backup(args.output)
    print(json.dumps({'output': str(args.output), 'graph': result['graph'], 'qdrant': result['qdrant'], 'files': len(result['files'])}))
