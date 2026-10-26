"""Start the upstream MCP server with fixed read-only mode and environment credentials."""

import os
import sys

from psycopg.conninfo import make_conninfo


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
    sys.argv = ["postgres-mcp", "--access-mode=restricted", "--transport=sse", "--sse-host=0.0.0.0", "--sse-port=8000"]
    from postgres_mcp import main as run_server
    run_server()


if __name__ == "__main__":
    main()
