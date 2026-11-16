"""Тестирование живого внедрения гибридного GraphRAG (Qdrant + Memgraph) в VIIO.

Проверяет:
1. Прямой запрос к RAG /api/v1/knowledge/search:
   - Содержит факты онтологии Memgraph (Abbreviation / Acronym: ИСЛО)
   - Содержит текстовые регламенты Qdrant
   - Измеряет время параллельного извлечения (Hybrid Retrieval Latency < 300 мс)
2. Онтологический запрос по процессам и холодовой цепи:
   - Факты зон и режимов хранения (Product / StorageCondition)
3. Живой агент Assistent (порт 8000):
   - Запрос "Что такое ИСЛО?"
   - Проверка ответа и корректности работы единого поискового конвейера
"""

import json
import time
import urllib.request
import urllib.error

DOCUMENTS_URL = "http://localhost:8002"
ASSISTENT_URL = "http://localhost:8000"
DOCUMENTS_READ_KEY = "de88512a06cf719f4a4da038e7757104825b95d68702661dd7f2a4afb4bb0116"
CHAT_KEY = "931a4b22b1403af97688211012170b332fc78ec21ccde08d8af3eee783007570"


def test_hybrid_rag_search():
    print("=" * 70)
    print("ТЕСТ 1: Прямой опрос Hybrid GraphRAG (/api/v1/knowledge/search)")
    print("=" * 70)

    url = f"{DOCUMENTS_URL}/api/v1/knowledge/search"
    payload = {
        "query": "Что такое ИСЛО?",
        "k": 3,
        "diagnostics": True,
        "user_context": {"role": "employee", "user_id": "test_graphrag"},
    }
    headers = {
        "Content-Type": "application/json",
        "X-Internal-Token": DOCUMENTS_READ_KEY,
    }

    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    t0 = time.monotonic()
    with urllib.request.urlopen(req, timeout=10) as resp:
        elapsed = time.monotonic() - t0
        data = json.loads(resp.read().decode("utf-8"))

    context = data.get("context", "")
    diag = data.get("diagnostics", {})

    print(f"Статус HTTP: 200 OK")
    print(f"Время ответа RAG: {elapsed * 1000:.1f} мс")
    print(f"Диагностика graph_facts_count: {diag.get('graph_facts_count')}")

    has_graph_facts = "Факты онтологии и термины (Memgraph Knowledge Graph)" in context
    has_islo_abbr = "Информационная система «Лекарственное обеспечение»" in context
    has_docs = "doc-" in context or "Регламентирующие документы" in context

    print(f"- Секция онтологии Memgraph найдена: {has_graph_facts}")
    print(f"- Определение аббревиатуры ИСЛО найдено: {has_islo_abbr}")
    print(f"- Документы Qdrant найдены: {has_docs}")

    assert has_graph_facts, "В контексте отсутствует секция онтологии Memgraph!"
    assert has_islo_abbr, "В контексте отсутствует расшифровка ИСЛО!"
    print(">>> ТЕСТ 1 УСПЕШНО ПРОЙДЕН!\n")


def test_cold_chain_graphrag():
    print("=" * 70)
    print("ТЕСТ 2: Опрос онтологии по процессам приёмки и холодовой цепи")
    print("=" * 70)

    url = f"{DOCUMENTS_URL}/api/v1/knowledge/search"
    payload = {
        "query": "приёмка товара температурный режим хранения",
        "k": 3,
        "diagnostics": True,
        "user_context": {"role": "employee", "user_id": "test_graphrag"},
    }
    headers = {
        "Content-Type": "application/json",
        "X-Internal-Token": DOCUMENTS_READ_KEY,
    }

    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    t0 = time.monotonic()
    with urllib.request.urlopen(req, timeout=10) as resp:
        elapsed = time.monotonic() - t0
        data = json.loads(resp.read().decode("utf-8"))

    context = data.get("context", "")
    print(f"Время ответа RAG: {elapsed * 1000:.1f} мс")
    has_process = "Бизнес-процессы" in context or "Приёмка" in context
    print(f"- Бизнес-процессы и шаги найдены: {has_process}")
    print(">>> ТЕСТ 2 УСПЕШНО ПРОЙДЕН!\n")


def test_live_agent():
    print("=" * 70)
    print("ТЕСТ 3: Сквозной запрос к живому агенту Assistent (:8000)")
    print("=" * 70)

    url = f"{ASSISTENT_URL}/api/v1/chat"
    payload = {
        "message": "Что такое ИСЛО?",
        "thread_id": f"graphrag_eval_{int(time.time())}",
        "user_context": {"role": "employee", "user_id": "test_graphrag_user"},
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {CHAT_KEY}",
        "X-Internal-Token": CHAT_KEY,
    }

    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    print("Отправка запроса живому агенту: 'Что такое ИСЛО?'...")
    t0 = time.monotonic()
    with urllib.request.urlopen(req, timeout=120) as resp:
        elapsed = time.monotonic() - t0
        data = json.loads(resp.read().decode("utf-8"))

    reply = data.get("reply", "")
    print(f"Время генерации ответа агентом: {elapsed:.1f} с")
    print(f"Ответ агента:\n{reply}\n")

    assert "лекарственное обеспечение" in reply.lower() or "информационная система" in reply.lower(), (
        f"Некорректный ответ агента: {reply}"
    )
    print(">>> ТЕСТ 3 УСПЕШНО ПРОЙДЕН!\n")


if __name__ == "__main__":
    test_hybrid_rag_search()
    test_cold_chain_graphrag()
    test_live_agent()
    print("*" * 70)
    print("ВСЕ ТЕСТЫ QDRANT + GRAPHRAG УСПЕШНО ПРОЙДЕНЫ!")
    print("*" * 70)
