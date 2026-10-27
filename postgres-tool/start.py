"""Start the upstream MCP server with fixed read-only mode and environment credentials."""

import os
import sys

from psycopg.conninfo import make_conninfo
import psycopg


def validate_readonly_role(dsn):
    """Do not rely on a changeable session setting to restrict a privileged role."""
    with psycopg.connect(dsn) as conn:
        privileged=conn.execute('SELECT rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication FROM pg_roles WHERE rolname=current_user').fetchone()[0]
        writable=conn.execute("""SELECT EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%'
            AND c.relkind IN ('r','p','v','m','f') AND (
                has_table_privilege(c.oid,'INSERT') OR has_table_privilege(c.oid,'UPDATE') OR
                has_table_privilege(c.oid,'DELETE') OR has_table_privilege(c.oid,'TRUNCATE')))
            OR has_database_privilege(current_database(),'CREATE')
            OR EXISTS(SELECT 1 FROM pg_namespace WHERE nspname NOT LIKE 'pg_%' AND nspname<>'information_schema'
                AND has_schema_privilege(oid,'CREATE'))""").fetchone()[0]
        if privileged or writable:
            raise RuntimeError('PGSQL_USER must be a dedicated read-only role without elevated, schema-create or table-write permissions')


def main():
    database = os.environ.get("PGSQL_DATABASE", "").strip()
    host = os.environ.get("PGSQL_HOST", "").strip()
    if not database or not host:
        raise SystemExit("Configure PGSQL_HOST and PGSQL_DATABASE before starting postgres-tool.")
    # Keep secrets out of command arguments and use libpq escaping for special characters.
    os.environ["DATABASE_URI"] = make_conninfo(
        host=host,
        port=os.environ.get("PGSQL_PORT", "5432"),
        dbname=database,
        user=os.environ.get("PGSQL_USER", ""),
        password=os.environ.get("PGSQL_PASSWORD", ""),
        sslmode=os.environ.get("PGSQL_SSLMODE", "prefer"),
        connect_timeout="5",
        options="-c default_transaction_read_only=on -c statement_timeout=10000 -c lock_timeout=2000",
    )
    validate_readonly_role(os.environ['DATABASE_URI'])
    sys.argv = ["postgres-mcp", "--access-mode=restricted", "--transport=sse", "--sse-host=0.0.0.0", "--sse-port=8000"]
    from postgres_mcp import main as run_server
    run_server()


if __name__ == "__main__":
    main()
