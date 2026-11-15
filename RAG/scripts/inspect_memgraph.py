import os
from neo4j import GraphDatabase

uri = os.environ.get("MEMGRAPH_URI", "bolt://memgraph:7687")
driver = GraphDatabase.driver(uri, auth=("", ""))

with driver.session() as s:
    res = s.run("MATCH (n) RETURN labels(n) AS lbl, count(n) AS cnt ORDER BY cnt DESC").data()
    print("=== Memgraph Node counts by label ===")
    total_nodes = 0
    for r in res:
        cnt = r["cnt"]
        total_nodes += cnt
        print(f"{r['lbl']}: {cnt}")
    print(f"Total Nodes: {total_nodes}")
    
    rels = s.run("MATCH ()-[r]->() RETURN type(r) AS rel, count(r) AS cnt ORDER BY cnt DESC").data()
    print("\n=== Memgraph Relation counts ===")
    total_rels = 0
    for r in rels:
        cnt = r["cnt"]
        total_rels += cnt
        print(f"{r['rel']}: {cnt}")
    print(f"Total Relations: {total_rels}")

driver.close()
