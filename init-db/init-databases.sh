#!/bin/bash
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" <<-EOSQL
    CREATE DATABASE user_db;
    CREATE DATABASE agent_db;
    GRANT ALL PRIVILEGES ON DATABASE user_db TO $POSTGRES_USER;
    GRANT ALL PRIVILEGES ON DATABASE agent_db TO $POSTGRES_USER;
EOSQL
 
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" -d user_db <<-EOSQL
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
EOSQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" -d agent_db <<-EOSQL
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
EOSQL
