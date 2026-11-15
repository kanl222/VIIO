# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Скрипт очистки базы данных и векторного хранилища (сохраняет пользователей и сессии).

Что очищается:
1. user_db: таблицы conversations, messages (users и sessions сохраняются).
2. agent_db: таблицы documents, document_contents, document_publications, document_upload_jobs,
             а также checkpoints, checkpoint_writes, checkpoint_blobs, store, user_facts.
3. Локальный том документов (/data/documents/* в viio_document_worker).
4. Векторные коллекции Qdrant.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def load_env() -> dict[str, str]:
    env_file = ROOT / ".env"
    env: dict[str, str] = {}
    if not env_file.exists():
        return env
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, val = line.partition("=")
        if sep:
            env[key.strip()] = val.strip().strip("'\"")
    return env


def run_docker(cmd: list[str]) -> str:
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if res.returncode != 0:
        raise RuntimeError(f"Команда завершилась с ошибкой:\n{' '.join(cmd)}\n{res.stderr.strip()}")
    return res.stdout.strip()


def clear_postgres() -> None:
    print("[1/4] Очистка PostgreSQL...")

    # user_db: очистка сообщений и диалогов (users и sessions НЕ трогаем)
    sql_user = "TRUNCATE TABLE messages, conversations CASCADE;"
    run_docker(["docker", "exec", "viio_postgres", "psql", "-U", "postgres", "-d", "user_db", "-c", sql_user])
    print("  ✓ user_db: conversations и messages очищены (users и sessions сохранены)")

    # agent_db: очистка документов и состояния чекпоинтов
    sql_agent = (
        "TRUNCATE TABLE "
        "document_publications, document_contents, document_upload_jobs, documents, "
        "checkpoint_blobs, checkpoint_writes, checkpoints, store, user_facts "
        "CASCADE;"
    )
    run_docker(["docker", "exec", "viio_postgres", "psql", "-U", "postgres", "-d", "agent_db", "-c", sql_agent])
    print("  ✓ agent_db: документы и чекпоинты агента очищены")


def clear_documents_storage() -> None:
    print("[2/4] Очистка локального хранилища документов...")
    run_docker(["docker", "exec", "viio_document_worker", "sh", "-c", "rm -rf /data/documents/*"])
    print("  ✓ /data/documents/* в viio_document_worker очищены")


def clear_qdrant(env: dict[str, str]) -> None:
    print("[3/4] Очистка коллекций Qdrant...")
    port = env.get("QDRANT_PORT", "6333")
    api_key = env.get("QDRANT_API_KEY", "")
    url = f"http://localhost:{port}"

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["api-key"] = api_key

    try:
        req = urllib.request.Request(f"{url}/collections", headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            collections = [c["name"] for c in data.get("result", {}).get("collections", [])]

        if not collections:
            print("  ✓ Коллекций в Qdrant нет (уже чистые)")
            return

        for name in collections:
            del_req = urllib.request.Request(f"{url}/collections/{name}", headers=headers, method="DELETE")
            with urllib.request.urlopen(del_req, timeout=10) as del_resp:
                res_data = json.loads(del_resp.read().decode("utf-8"))
                print(f"  ✓ Удалена коллекция Qdrant: {name} (статус: {res_data.get('status')})")
    except Exception as exc:
        print(f"  ! Внимание при очистке Qdrant: {exc}", file=sys.stderr)


def verify() -> None:
    print("[4/4] Проверка результатов...")
    chk_user = run_docker([
        "docker", "exec", "viio_postgres", "psql", "-U", "postgres", "-d", "user_db", "-c",
        "SELECT 'users' as tbl, count(*) FROM users UNION ALL "
        "SELECT 'sessions', count(*) FROM sessions UNION ALL "
        "SELECT 'conversations', count(*) FROM conversations UNION ALL "
        "SELECT 'messages', count(*) FROM messages;"
    ])
    print("\nСостояние user_db:")
    print(chk_user)

    chk_agent = run_docker([
        "docker", "exec", "viio_postgres", "psql", "-U", "postgres", "-d", "agent_db", "-c",
        "SELECT 'documents' as tbl, count(*) FROM documents UNION ALL "
        "SELECT 'checkpoints', count(*) FROM checkpoints;"
    ])
    print("\nСостояние agent_db:")
    print(chk_agent)


def main() -> None:
    env = load_env()
    clear_postgres()
    clear_documents_storage()
    clear_qdrant(env)
    verify()
    print("\nОчистка завершена успешно!")


if __name__ == "__main__":
    main()
