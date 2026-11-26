"""Offline validation of graph source branches before opening a database session."""
import json
import re
from collections import defaultdict
from pathlib import Path

IDENTIFIER = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')


def alias_key(value):
    return ' '.join(re.findall(r'\w+', value.casefold().replace('ё', 'е')))


def validate_identifier(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ValueError(f'Invalid graph identifier: {value!r}')


def validate_properties(properties):
    if not isinstance(properties, dict):
        raise ValueError('Graph properties must be an object')
    for key, value in properties.items():
        validate_identifier(key)
        values = value if isinstance(value, list) else [value]
        if any(item is not None and not isinstance(item, (str, int, float, bool)) for item in values):
            raise ValueError(f'Unsupported graph property: {key}')


def load_branches(directory: Path):
    paths = sorted(directory.glob('*.json'))
    if not paths:
        raise ValueError(f'No graph branches in {directory}')
    branches = []
    for path in paths:
        branch = json.loads(path.read_text(encoding='utf8'))
        if not isinstance(branch.get('nodes'), list) or not isinstance(branch.get('relations'), list):
            raise ValueError(f'{path.name}: nodes and relations must be arrays')
        branches.append(branch)
    validate_branches(branches)
    return branches


def validate_branches(branches):
    nodes = [node for branch in branches for node in branch['nodes']]
    relations = [rel for branch in branches for rel in branch['relations']]
    unique, aliases = set(), {}
    for node in nodes:
        label, key, props = node.get('label'), node.get('match_key'), node.get('properties')
        validate_identifier(label)
        validate_identifier(key)
        validate_properties(props)
        if props.get('source_matches_catalog') is False and props.get('verification_status') != 'needs_review':
            raise ValueError('A source/catalog hash mismatch requires review')
        value = props.get(key)
        if not isinstance(value, (str, int)) or value == '':
            raise ValueError(f'{label}: missing match key {key}')
        identity = (label, key, value)
        if identity in unique:
            raise ValueError(f'Duplicate node: {identity}')
        unique.add(identity)
        if not props.get('source_uri') and not props.get('source_document_id'):
            raise ValueError(f'{identity}: missing provenance')
        if label == 'Abbreviation':
            if not isinstance(props.get('full_name'), str) or not props['full_name'].strip():
                raise ValueError(f'{identity}: missing definition')
            expected = sorted({alias_key(value) for value in [props['name'], props.get('short_form', ''),
                               *props.get('aliases', [])] if value})
            if 'search_aliases' in props and props['search_aliases'] != expected:
                raise ValueError(f'{identity}: stale normalized aliases')
            for name in [props['name'], props.get('short_form', ''), *props.get('aliases', [])]:
                normalized = alias_key(name)
                if normalized and normalized in aliases and aliases[normalized] != identity:
                    raise ValueError(f'Ambiguous abbreviation alias: {name}')
                aliases[normalized] = identity

    def resolve(ref):
        for key in ('label', 'key'):
            validate_identifier(ref.get(key))
        found = [node for node in nodes if node['label'] == ref['label']
                 and node['properties'].get(ref['key']) == ref.get('value')]
        if len(found) != 1:
            raise ValueError(f'Relation endpoint matches {len(found)} nodes: {ref}')
        return found[0]

    step_orders, next_steps, modelled, owners = defaultdict(set), {}, set(), {}
    document_ids = {node['properties']['id'] for node in nodes if node['label'] == 'Document'}
    for node in nodes:
        source_id = node['properties'].get('source_document_id')
        if source_id and source_id not in document_ids:
            raise ValueError(f'Unknown provenance document: {source_id}')
    for relation in relations:
        validate_identifier(relation.get('type'))
        validate_properties(relation.get('properties', {}))
        source, target = resolve(relation['from']), resolve(relation['to'])
        if relation['type'] == 'HAS_STEP':
            if source['label'] != 'Process' or target['label'] != 'Step':
                raise ValueError('HAS_STEP must connect Process to Step')
            proc = source['properties']['name']
            step = target['properties']
            if not isinstance(step.get('order'), int) or step['order'] <= 0:
                raise ValueError(f'{proc}: invalid step order')
            if step['order'] in step_orders[proc]:
                raise ValueError(f'{proc}: duplicate step order')
            step_orders[proc].add(step['order'])
            if step['id'] in owners:
                raise ValueError('A step must belong to exactly one process')
            owners[step['id']] = proc
            modelled.add(proc)
        if relation['type'] == 'NEXT':
            a, b = source['properties'], target['properties']
            if source['label'] != 'Step' or target['label'] != 'Step' or a.get('process_name') != b.get('process_name'):
                raise ValueError('NEXT crosses process boundaries')
            if a.get('order', 0) + 1 != b.get('order'):
                raise ValueError('NEXT must connect consecutive steps')
            if a['id'] in next_steps and next_steps[a['id']] != b['id']:
                raise ValueError('A step has multiple successors')
            next_steps[a['id']] = b['id']
    for node in nodes:
        if node['label'] == 'Step' and node['properties']['id'] not in owners:
            raise ValueError('Orphan procedure step')
        if node['label'] == 'Process' and node['properties']['name'] not in modelled:
            if node['properties'].get('procedure_status') != 'needs_primary_source_review':
                raise ValueError('A process needs sourced steps or an explicit review status')
    for orders in step_orders.values():
        if orders != set(range(1, len(orders) + 1)):
            raise ValueError('Procedure step orders must be contiguous')
    return {'branches': len(branches), 'nodes': len(nodes), 'relations': len(relations)}
