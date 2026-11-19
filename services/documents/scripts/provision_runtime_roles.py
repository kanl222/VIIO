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
        for db in ['agent_db', 'user_db']:
            if not conn.execute('SELECT 1 FROM pg_database WHERE datname=%s', (db,)).fetchone():
                conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(db)))
        for role,variable in ROLES.items():
            password=os.environ.get(variable)
            if not password:
                continue
            if len(password)<32:raise ValueError('Runtime passwords must be at least 32 characters')
            if not conn.execute('SELECT 1 FROM pg_roles WHERE rolname=%s',(role,)).fetchone():
                conn.execute(sql.SQL('CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION').format(sql.Identifier(role)))
            conn.execute(sql.SQL('ALTER ROLE {} PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION').format(sql.Identifier(role),sql.Literal(password)))
    for database in ['agent_db','user_db']:
        with connect(database) as conn:
            if database == 'user_db':
                conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    username VARCHAR(64) UNIQUE NOT NULL,
                    password_hash VARCHAR(255) NOT NULL,
                    role VARCHAR(32) NOT NULL DEFAULT 'employee',
                    departments TEXT[] NOT NULL DEFAULT ARRAY['general']::TEXT[],
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    id VARCHAR(64) PRIMARY KEY,
                    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
                    user_agent TEXT,
                    ip_address VARCHAR(45),
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS conversations (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    title VARCHAR(255) NOT NULL DEFAULT 'Новый диалог',
                    agent_thread_id VARCHAR(64) UNIQUE NOT NULL,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    sender VARCHAR(32) NOT NULL,
                    text TEXT NOT NULL,
                    attachments JSONB DEFAULT '[]'::jsonb,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                );
                """)
            elif database == 'agent_db':
                conn.execute("""
                CREATE TABLE IF NOT EXISTS documents (
                    id VARCHAR(64) PRIMARY KEY,
                    title VARCHAR(255) NOT NULL,
                    mime_type VARCHAR(128) NOT NULL DEFAULT 'text/plain',
                    file_size BIGINT,
                    sha256 VARCHAR(64),
                    owner_id VARCHAR(64),
                    version INTEGER NOT NULL DEFAULT 1,
                    parent_id VARCHAR(64) REFERENCES documents(id) ON DELETE SET NULL,
                    status VARCHAR(32) NOT NULL DEFAULT 'indexed',
                    is_public BOOLEAN NOT NULL DEFAULT FALSE,
                    allowed_roles TEXT[] NOT NULL DEFAULT ARRAY['employee']::TEXT[],
                    allowed_departments TEXT[] NOT NULL DEFAULT ARRAY['general']::TEXT[],
                    storage_key VARCHAR(512),
                    chunks_count INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    family_id VARCHAR(64),
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    document_kind TEXT NOT NULL DEFAULT 'document',
                    original_filename VARCHAR(255),
                    catalog_metadata JSONB NOT NULL DEFAULT '{}'::jsonb
                );
                ALTER TABLE documents ADD COLUMN IF NOT EXISTS family_id VARCHAR(64);
                ALTER TABLE documents ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;
                ALTER TABLE documents ADD COLUMN IF NOT EXISTS document_kind TEXT NOT NULL DEFAULT 'document';
                ALTER TABLE documents ADD COLUMN IF NOT EXISTS original_filename VARCHAR(255);
                ALTER TABLE documents ADD COLUMN IF NOT EXISTS catalog_metadata JSONB NOT NULL DEFAULT '{}'::jsonb;
                UPDATE documents SET family_id = id WHERE family_id IS NULL;
                ALTER TABLE documents ALTER COLUMN family_id SET NOT NULL;
                CREATE UNIQUE INDEX IF NOT EXISTS documents_family_version ON documents(family_id, version);
                CREATE UNIQUE INDEX IF NOT EXISTS documents_one_active ON documents(family_id) WHERE is_active;
                CREATE INDEX IF NOT EXISTS idx_documents_owner ON documents(owner_id);
                CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(status);
                CREATE INDEX IF NOT EXISTS idx_documents_created_at ON documents(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_documents_sha256 ON documents(sha256) WHERE sha256 IS NOT NULL;

                CREATE TABLE IF NOT EXISTS document_contents (
                    document_id VARCHAR(64) PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
                    content TEXT NOT NULL,
                    filename TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL DEFAULT '',
                    search_vector tsvector GENERATED ALWAYS AS
                      (to_tsvector('russian',title || ' ' || filename || ' ' || content) || to_tsvector('simple',title || ' ' || filename || ' ' || content)) STORED,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                ALTER TABLE document_contents ADD COLUMN IF NOT EXISTS filename TEXT NOT NULL DEFAULT '';
                ALTER TABLE document_contents ADD COLUMN IF NOT EXISTS title TEXT NOT NULL DEFAULT '';
                CREATE INDEX IF NOT EXISTS document_contents_search ON document_contents USING gin(search_vector);

                CREATE TABLE IF NOT EXISTS document_publications (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    document_id VARCHAR(64) UNIQUE NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    title VARCHAR(255) NOT NULL,
                    content TEXT NOT NULL,
                    publisher_id TEXT NOT NULL DEFAULT 'system',
                    published_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    search_vector tsvector GENERATED ALWAYS AS
                      (to_tsvector('russian',title || ' ' || content) || to_tsvector('simple',title || ' ' || content)) STORED
                );
                ALTER TABLE document_publications ADD COLUMN IF NOT EXISTS id UUID DEFAULT gen_random_uuid();
                ALTER TABLE document_publications ADD COLUMN IF NOT EXISTS publisher_id TEXT NOT NULL DEFAULT 'system';
                CREATE INDEX IF NOT EXISTS document_publications_search ON document_publications USING gin(search_vector);

                CREATE TABLE IF NOT EXISTS document_upload_jobs (
                    id TEXT PRIMARY KEY,
                    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                    status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','processing','needs_review','ready','error','cancelled')),
                    document_id TEXT,
                    chunks_count INTEGER,
                    error TEXT,
                    claim_token TEXT,
                    lease_until TIMESTAMPTZ,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    review JSONB,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                ALTER TABLE document_upload_jobs ADD COLUMN IF NOT EXISTS payload JSONB NOT NULL DEFAULT '{}'::jsonb;
                ALTER TABLE document_upload_jobs ADD COLUMN IF NOT EXISTS document_id TEXT;
                ALTER TABLE document_upload_jobs ADD COLUMN IF NOT EXISTS chunks_count INTEGER;
                ALTER TABLE document_upload_jobs ADD COLUMN IF NOT EXISTS error TEXT;
                ALTER TABLE document_upload_jobs ADD COLUMN IF NOT EXISTS claim_token TEXT;
                ALTER TABLE document_upload_jobs ADD COLUMN IF NOT EXISTS lease_until TIMESTAMPTZ;
                ALTER TABLE document_upload_jobs ADD COLUMN IF NOT EXISTS attempts INTEGER NOT NULL DEFAULT 0;
                ALTER TABLE document_upload_jobs ADD COLUMN IF NOT EXISTS review JSONB;
                ALTER TABLE document_upload_jobs DROP CONSTRAINT IF EXISTS document_upload_jobs_status_check;
                ALTER TABLE document_upload_jobs ADD CONSTRAINT document_upload_jobs_status_check CHECK (status IN ('queued','processing','needs_review','ready','error','cancelled'));
                CREATE INDEX IF NOT EXISTS document_jobs_pending ON document_upload_jobs(created_at) WHERE status IN ('queued','processing');

                CREATE TABLE IF NOT EXISTS user_facts (
                    id UUID PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    access_scope TEXT NOT NULL,
                    content TEXT NOT NULL,
                    source_thread TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS user_facts_owner_scope ON user_facts(owner_id, access_scope, created_at DESC);
                CREATE TABLE IF NOT EXISTS checkpoints (
                    thread_id TEXT NOT NULL,
                    checkpoint_ns TEXT NOT NULL DEFAULT '',
                    checkpoint_id TEXT NOT NULL,
                    parent_checkpoint_id TEXT,
                    type TEXT,
                    checkpoint BYTEA NOT NULL,
                    metadata BYTEA NOT NULL,
                    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
                );
                CREATE TABLE IF NOT EXISTS checkpoint_blobs (
                    thread_id TEXT NOT NULL,
                    checkpoint_ns TEXT NOT NULL DEFAULT '',
                    channel TEXT NOT NULL,
                    version TEXT NOT NULL,
                    type TEXT,
                    blob BYTEA,
                    PRIMARY KEY (thread_id, checkpoint_ns, channel, version)
                );
                CREATE TABLE IF NOT EXISTS checkpoint_writes (
                    thread_id TEXT NOT NULL,
                    checkpoint_ns TEXT NOT NULL DEFAULT '',
                    checkpoint_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    idx INTEGER NOT NULL,
                    channel TEXT NOT NULL,
                    type TEXT,
                    blob BYTEA,
                    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
                );
                CREATE TABLE IF NOT EXISTS checkpoint_migrations (
                    v INTEGER PRIMARY KEY
                );
                """)
            for role in ROLES:
                if not conn.execute('SELECT 1 FROM pg_roles WHERE rolname=%s', (role,)).fetchone():
                    continue
                conn.execute(sql.SQL('REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {}').format(sql.Identifier(role)))
                conn.execute(sql.SQL('REVOKE CREATE ON SCHEMA public FROM {}').format(sql.Identifier(role)))
                if database not in TABLES[role]:continue
                conn.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO {}').format(sql.Identifier(database),sql.Identifier(role)))
                conn.execute(sql.SQL('GRANT USAGE ON SCHEMA public TO {}').format(sql.Identifier(role)))
                for table in TABLES[role][database]:
                    if conn.execute('SELECT to_regclass(%s)',('public.'+table,)).fetchone()[0] is None:
                        raise RuntimeError('Run schema migrations before provisioning runtime permissions: '+table)
                    conn.execute(sql.SQL('GRANT SELECT,INSERT,UPDATE,DELETE ON TABLE {} TO {}').format(sql.Identifier(table),sql.Identifier(role)))
            if database=='agent_db' and conn.execute("SELECT 1 FROM pg_roles WHERE rolname='viio_documents'").fetchone():
                conn.execute('GRANT SELECT ON documents,document_contents,document_publications TO viio_documents')
    print('Runtime roles provisioned; migrations remain under the deployment account')

if __name__=='__main__':main()
