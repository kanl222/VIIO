from neo4j import GraphDatabase
driver = GraphDatabase.driver('bolt://memgraph:7687', auth=('', ''))
with driver.session() as s:
    rows = s.run('MATCH (e:Employee) RETURN e.name, e.position, e.phone').data()
    print('Total employees in DB:', len(rows))
    for r in rows[:5]:
        print(r)
    
    match = s.run("MATCH (e:Employee) WHERE e.name CONTAINS 'Рагуз' RETURN e.name").data()
    print('Match Рагуз:', match)
driver.close()
