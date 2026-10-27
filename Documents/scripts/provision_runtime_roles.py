"""Run once with migration credentials, never in application startup.

Passwords come from environment and are never logged. Existing volumes/data stay.
"""
import os
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

ROLES={'viio_assistent':'ASSISTENT_DB_PASSWORD','viio_documents':'DOCUMENTS_DB_PASSWORD',
       'viio_worker':'WORKER_DB_PASSWORD','viio_webchat':'WEBCHAT_DB_PASSWORD'}
TABLES={
    'viio_assistent':{'agent_db':['checkpoints','checkpoint_blobs','checkpoint_writes','checkpoint_migrations','user_facts']},
    'viio_documents':{'agent_db':['document_upload_jobs']},
    'viio_worker':{'agent_db':['documents','document_contents','document_publications','document_upload_jobs']},
    'viio_webchat':{'user_db':['users','sessions','conversations','messages']},
}

def connect(database):
    return psycopg.connect(make_conninfo(host=os.getenv('POSTGRES_HOST','postgres'),port=os.getenv('POSTGRES_PORT','5432'),
        dbname=database,user=os.getenv('POSTGRES_USER','postgres'),password=os.getenv('POSTGRES_PASSWORD','postgres')),autocommit=True)

def main():
    with connect('postgres') as conn:
        for role,variable in ROLES.items():
            password=os.environ[variable]
            if len(password)<32:raise ValueError('Runtime passwords must be at least 32 characters')
            if not conn.execute('SELECT 1 FROM pg_roles WHERE rolname=%s',(role,)).fetchone():
                conn.execute(sql.SQL('CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION').format(sql.Identifier(role)))
            conn.execute(sql.SQL('ALTER ROLE {} PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION').format(sql.Identifier(role),sql.Literal(password)))
    for database in ['agent_db','user_db']:
        with connect(database) as conn:
            for role in ROLES:
                conn.execute(sql.SQL('REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {}').format(sql.Identifier(role)))
                conn.execute(sql.SQL('REVOKE CREATE ON SCHEMA public FROM {}').format(sql.Identifier(role)))
                if database not in TABLES[role]:continue
                conn.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO {}').format(sql.Identifier(database),sql.Identifier(role)))
                conn.execute(sql.SQL('GRANT USAGE ON SCHEMA public TO {}').format(sql.Identifier(role)))
                for table in TABLES[role][database]:
                    if conn.execute('SELECT to_regclass(%s)',('public.'+table,)).fetchone()[0] is None:
                        raise RuntimeError('Run schema migrations before provisioning runtime permissions: '+table)
                    conn.execute(sql.SQL('GRANT SELECT,INSERT,UPDATE,DELETE ON TABLE {} TO {}').format(sql.Identifier(table),sql.Identifier(role)))
            if database=='agent_db':
                conn.execute('GRANT SELECT ON documents,document_contents,document_publications TO viio_documents')
    print('Runtime roles provisioned; migrations remain under the deployment account')

if __name__=='__main__':main()
