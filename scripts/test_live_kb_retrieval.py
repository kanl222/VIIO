"""Тестирование нахождения данных в базе знаний (БЗ) на живом агенте VIIO (порт 8000).

Проверяет:
1. Каталог документов (list_documents): поиск доступных инструкций по теме НМГ
2. Точный RAG-поиск по регламентам МДЛП: схема выбытия 10521 по льготному рецепту
3. Точный RAG-поиск по нормативным актам региона: обеспечение детей системами НМГ (Постановление № 58-п)
4. Пошаговые инструкции ИС ЛО: добавление врача через модуль «Управление сотрудниками» (поиск по СНИЛС / ЕЦП)
5. Поиск по проекту региональной интеграции: замена бланка 148-1/у-04(л) картой оренбуржца
6. Защита от галлюцинаций (Out-of-domain): запрос отсутствующей темы (BGP Cisco) -> корректный отказ
"""

from __future__ import annotations

import json
import time
import urllib.request
import urllib.error
from typing import Any

ASSISTENT_URL = "http://localhost:8000"
CHAT_KEY = "931a4b22b1403af97688211012170b332fc78ec21ccde08d8af3eee783007570"


def query_agent(
    message: str,
    role: str = "employee",
    thread_id: str | None = None,
    timeout: int = 150,
) -> tuple[int, dict[str, Any], float]:
    url = f"{ASSISTENT_URL}/api/v1/chat"
    payload = {
        "message": message,
        "thread_id": thread_id or f"kb_eval_{int(time.time()*1000)}",
        "user_context": {"role": role, "user_id": f"kb_tester_{role}"},
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {CHAT_KEY}",
        "X-Internal-Token": CHAT_KEY,
    }
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers)
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            elapsed = time.monotonic() - t0
            data = json.loads(resp.read().decode("utf-8"))
            return resp.status, data, elapsed
    except urllib.error.HTTPError as e:
        elapsed = time.monotonic() - t0
        err_body = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(err_body), elapsed
        except Exception:
            return e.code, {"error": err_body}, elapsed
    except Exception as e:
        elapsed = time.monotonic() - t0
        return 500, {"error": str(e)}, elapsed


def run_kb_tests():
    print("\n" + "=" * 80)
    print("      ТЕСТИРОВАНИЕ НАХОЖДЕНИЯ ДАННЫХ В БАЗЕ ЗНАНИЙ (БЗ) НА ЖИВОМ АГЕНТЕ       ")
    print("=" * 80)

    test_cases = [
        {
            "id": "KB-01",
            "name": "Каталог БЗ (поиск документов по НМГ)",
            "query": "Какие документы и инструкции есть в каталоге базы знаний со словом НМГ в названии?",
            "role": "employee",
            "validator": lambda r, s: (
                r.get("status") == 200
                and "нмг" in r.get("reply", "").lower()
                and ("инструкция" in r.get("reply", "").lower() or len(r.get("sources", [])) > 0)
            ),
            "expected_hint": "Инструкции для врачей и координаторов по НМГ с ссылками скачивания",
        },
        {
            "id": "KB-02",
            "name": "Факты МДЛП (Схема 10521 льготного отпуска)",
            "query": "По регламенту МДЛП «Честный Знак», какая схема отпуска лекарств по льготному рецепту через Регистратор выбытия описана в базе знаний?",
            "role": "employee",
            "validator": lambda r, s: (
                r.get("status") == 200
                and "10521" in r.get("reply", "")
            ),
            "expected_hint": "Схема 10521 отпуска по льготному рецепту",
        },
        {
            "id": "KB-03",
            "name": "Региональная нормативная база (Постановление № 58-п по НМГ)",
            "query": "Каким постановлением Правительства Оренбургской области регулируется обеспечение детей системами непрерывного мониторирования уровня глюкозы (НМГ)?",
            "role": "employee",
            "validator": lambda r, s: (
                r.get("status") == 200
                and ("58-п" in r.get("reply", "") or "58" in r.get("reply", ""))
            ),
            "expected_hint": "Постановление Правительства Оренбургской области № 58-п",
        },
        {
            "id": "KB-04",
            "name": "Пошаговый регламент ИС ЛО (добавление врача из ЕЦП)",
            "query": "Как в модуле «Управление сотрудниками» ИС ЛО добавить нового врача с правом выписки рецептов?",
            "role": "employee",
            "validator": lambda r, s: (
                r.get("status") == 200
                and ("снилс" in r.get("reply", "").lower() or "ецп" in r.get("reply", "").lower())
                and ("сотрудник" in r.get("reply", "").lower() or "администратор" in r.get("reply", "").lower())
            ),
            "expected_hint": "Поиск по СНИЛС / добавление из ЕЦП",
        },
        {
            "id": "KB-05",
            "name": "Интеграция сервисов (Замена бланка 148-1/у-04(л) картой оренбуржца)",
            "query": "Какую форму бумажного рецептурного бланка позволяет не печатать Универсальная карта оренбуржца при льготном отпуске?",
            "role": "employee",
            "validator": lambda r, s: (
                r.get("status") == 200
                and ("148" in r.get("reply", "") or "148-1" in r.get("reply", ""))
            ),
            "expected_hint": "Бланк формы № 148-1/у-04(л)",
        },
        {
            "id": "KB-06",
            "name": "Защита от галлюцинаций (Out-of-domain: протокол BGP Cisco)",
            "query": "Как настроить маршрутизацию BGP и номер автономной системы на маршрутизаторе Cisco для складской сети VIIO?",
            "role": "employee",
            "validator": lambda r, s: (
                r.get("status") == 200
                and (
                    "не найдено" in r.get("reply", "").lower()
                    or "нет сведений" in r.get("reply", "").lower()
                    or "отсутствует" in r.get("reply", "").lower()
                    or "базе знаний" in r.get("reply", "").lower()
                )
            ),
            "expected_hint": "Корректное сообщение об отсутствии сведений в БЗ",
        },
    ]

    results = []

    for tc in test_cases:
        print(f"\n[{tc['id']}] {tc['name']}")
        print(f"  Вопрос: «{tc['query']}»")
        status, data, elapsed = query_agent(tc["query"], role=tc["role"])
        reply = data.get("reply", "")
        sources = data.get("sources", [])
        
        passed = tc["validator"]({"status": status, "reply": reply, "sources": sources}, status)
        status_tag = "[✓ PASS]" if passed else "[X FAIL]"
        results.append((tc["id"], tc["name"], passed, elapsed, reply, sources))

        print(f"  Результат: {status_tag} ({elapsed:.2f}с, HTTP {status})")
        print(f"  Ожидалось: {tc['expected_hint']}")
        clean_preview = reply.replace("\n", " ").strip()[:140]
        print(f"  Ответ: {clean_preview}...")
        if sources:
            src_titles = [s.get("title", s.get("document_id", "")) for s in sources[:3]]
            print(f"  Источники ({len(sources)}): {src_titles}")

    print("\n" + "=" * 80)
    print("                    ИТОГОВЫЙ ОТЧЕТ ТЕСТИРОВАНИЯ БЗ                     ")
    print("=" * 80)
    passed_count = sum(1 for _, _, ok, _, _, _ in results)
    total_count = len(results)

    for tid, name, ok, t, _, _ in results:
        status_str = "[✓ PASS]" if ok else "[X FAIL]"
        print(f"  {status_str} {tid} | {name:<50} | {t:>5.2f}с")

    print("-" * 75)
    print(f"Всего тестов БЗ: {total_count} | Успешно: {passed_count}/{total_count} ({passed_count/total_count*100:.1f}%)")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    run_kb_tests()
