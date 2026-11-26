import copy
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'scripts'))
from knowledge_graph_validation import load_branches, validate_branches
import attach_knowledge_to_graph as loader


def system(name):
    return {'label': 'System', 'match_key': 'name',
            'properties': {'name': name, 'source_uri': 'fixture://source'}}


def fixture_branches():
    return [{'nodes': [system('first')], 'relations': [{
        'type': 'INTEGRATES_WITH',
        'from': {'label': 'System', 'key': 'name', 'value': 'first'},
        'to': {'label': 'System', 'key': 'name', 'value': 'second'},
    }]}, {'nodes': [system('second')], 'relations': []}]


def test_real_graph_sources_validate():
    if not list((ROOT / 'data/knowledge_graph').glob('*.json')):
        pytest.skip('Private local graph sources are not available')
    branches = load_branches(ROOT / 'data/knowledge_graph')
    nodes = [node for branch in branches for node in branch['nodes']]
    assert any(node['label'] == 'Abbreviation' and len(node['properties']['aliases']) > 1 for node in nodes)
    assert any(node['label'] == 'Step' for node in nodes)
    assert all(node['properties'].get('source_uri') or node['properties'].get('source_document_id') for node in nodes)


def test_invalid_endpoint_is_rejected():
    branches = fixture_branches()
    branches[0]['relations'][0]['to']['value'] = 'missing'
    with pytest.raises(ValueError, match='endpoint'):
        validate_branches(branches)


def test_cypher_identifier_injection_is_rejected():
    branches = fixture_branches()
    branches[0]['nodes'][0]['label'] = 'System) DETACH DELETE n //'
    with pytest.raises(ValueError, match='identifier'):
        validate_branches(branches)


def test_source_catalog_mismatch_cannot_be_marked_verified():
    branches = fixture_branches()
    branches[0]['nodes'][0]['properties'].update(source_matches_catalog=False, verification_status='verified')
    with pytest.raises(ValueError, match='hash mismatch'):
        validate_branches(branches)


def test_conflicting_aliases_are_rejected():
    nodes = [{'label': 'Abbreviation', 'match_key': 'name', 'properties': {
        'name': name, 'full_name': name, 'aliases': ['Same phrase'], 'source_uri': 'fixture://source'}}
        for name in ('one', 'two')]
    with pytest.raises(ValueError, match='Ambiguous'):
        validate_branches([{'nodes': nodes, 'relations': []}])


def test_process_without_steps_requires_explicit_review_status():
    node = {'label': 'Process', 'match_key': 'name',
            'properties': {'name': 'unverified workflow', 'source_uri': 'fixture://source'}}
    with pytest.raises(ValueError, match='review status'):
        validate_branches([{'nodes': [node], 'relations': []}])


def test_loader_validates_every_file_before_connecting(tmp_path, monkeypatch):
    branches = fixture_branches()
    branches[1]['nodes'] = []
    for index, branch in enumerate(branches):
        (tmp_path / f'{index}.json').write_text(json.dumps(branch), encoding='utf8')
    connect = MagicMock()
    monkeypatch.setattr(loader.GraphDatabase, 'driver', connect)
    with pytest.raises(ValueError):
        loader.attach_knowledge_from_dir(tmp_path)
    connect.assert_not_called()


def test_loader_creates_all_nodes_before_cross_branch_edges_in_one_transaction(tmp_path, monkeypatch):
    for index, branch in enumerate(fixture_branches()):
        (tmp_path / f'{index}.json').write_text(json.dumps(branch), encoding='utf8')
    driver = MagicMock()
    session = driver.session.return_value.__enter__.return_value
    transaction = session.begin_transaction.return_value.__enter__.return_value
    session.run.return_value.data.return_value = []
    monkeypatch.setattr(loader.GraphDatabase, 'driver', lambda *args, **kwargs: driver)
    loader.attach_knowledge_from_dir(tmp_path)
    queries = [call.args[0] for call in transaction.run.call_args_list]
    assert len(queries) == 3
    assert all('MERGE (n:' in query for query in queries[:2])
    assert 'MATCH (a:' in queries[-1]
    assert not any('DELETE' in query for query in queries)
    transaction.commit.assert_called_once()


def test_validate_only_never_connects_to_supplied_database(tmp_path):
    for index, branch in enumerate(fixture_branches()):
        (tmp_path / f"{index}.json").write_text(json.dumps(branch), encoding="utf8")
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/attach_knowledge_to_graph.py'),
                             '--data-dir', str(tmp_path), '--validate-only', '--uri', 'bolt://invalid-host.invalid:1'],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['branches'] == 2
