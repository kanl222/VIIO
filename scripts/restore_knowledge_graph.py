"""Restore a logical graph backup into an EMPTY Memgraph instance."""
import argparse
import json
from pathlib import Path

from neo4j import GraphDatabase
from knowledge_graph_validation import validate_identifier, validate_properties


def restore(path: Path, uri: str):
    graph = json.loads(path.read_text(encoding='utf8'))
    identities = {node['id'] for node in graph['nodes']}
    if len(identities) != len(graph['nodes']):
        raise ValueError('Duplicate backup node IDs')
    for node in graph['nodes']:
        for label in node['labels']:
            validate_identifier(label)
        validate_properties(node['properties'])
    for edge in graph['edges']:
        validate_identifier(edge['type'])
        validate_properties(edge['properties'])
        if edge['source'] not in identities or edge['target'] not in identities:
            raise ValueError('Missing backup edge endpoint')
    with GraphDatabase.driver(uri, auth=None) as driver, driver.session() as session:
        with session.begin_transaction() as tx:
            if tx.run('MATCH (n) RETURN count(n) AS count').single()['count']:
                raise ValueError('Restore requires an empty graph; existing data was not modified')
            mapping = {}
            for node in graph['nodes']:
                labels = ''.join(':' + label for label in node['labels'])
                mapping[node['id']] = tx.run(
                    f'CREATE (n{labels}) SET n = $props RETURN id(n) AS id',
                    props=node['properties']).single()['id']
            for edge in graph['edges']:
                tx.run(f'MATCH (a),(b) WHERE id(a)=$a AND id(b)=$b '
                       f'CREATE (a)-[r:{edge["type"]}]->(b) SET r = $props',
                       a=mapping[edge['source']], b=mapping[edge['target']],
                       props=edge['properties']).consume()
            tx.commit()
    print(json.dumps({'nodes': len(graph['nodes']), 'edges': len(graph['edges'])}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('backup', type=Path)
    parser.add_argument('--uri', required=True)
    args = parser.parse_args()
    restore(args.backup, args.uri)
