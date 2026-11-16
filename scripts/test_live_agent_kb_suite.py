"""Комплексное тестирование живого агента VIIO (:8000) по различным типам вопросов к БЗ.

Направления тестов:
1. Аббревиатуры и системы: МДЛП (онтология Memgraph + документы)
2. Регламент и процедура: добавление врача в ИС ЛО (doc-01)
3. Региональный проект: замена бланка 148-1/у-04(л) Картой оренбуржца (doc-71)
4. Холодовая цепь и хранение: температурный режим Гам-КОВИД-Вак (Memgraph Ontology)
5. Контакты и горячие линии: телефоны поддержки ЛЛО (doc-74)
6. Схемы маркировки: выбытие по льготному рецепту через РВ (схема 10521, doc-73)
7. Защита от галлюцинаций: out-of-domain запрос BGP Cisco -> фраза 'не найдено'
"""

from __future__ import annotations

import json
import time
import urllib.request
import urllib.error
from typing import Any

ASSISTENT_URL = "http://localhost:8000"
CHAT_KEY = "931a4b22b1403af97688211012170b332fc78ec21ccde08d8af3eee783007570"


def ask_agent(query: str, thread_id: str, timeout: int = 150) -> tuple[int, str, float]:
    url = f"{ASSISTENT_URL}/api/v1/chat"
    payload = {
        "message": query,
        "thread_id": thread_id,
        "user_context": {"role": "employee", "user_id": "kb_suite_tester"},
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {CHAT_KEY}",
        "X-Internal-Token": CHAT_KEY,
    }

    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            elapsed = time.monotonic() - t0
            data = json.loads(resp.read().decode("utf-8"))
            return resp.status, data.get("reply", ""), elapsed
    except urllib.error.HTTPError as e:
        elapsed = time.monotonic() - t0
        err_body = e.read().decode("utf-8")
        return e.code, f"HTTPError {e.code}: {err_body}", elapsed
    except Exception as e:
        elapsed = time.monotonic() - t0
        return 500, f"Exception: {str(e)}", elapsed


def run_suite():
    tests = [
        {
            "id": 1,
            "category": "Термины и системы (МДЛП)",
            "query": "Что такое МДЛП и для чего она применяется?",
            "check": lambda reply: any(w in reply.lower() for w in ["мониторинг движения", "лекарствен", "честный знак", "маркировк"]),
            "expected": "Расшифровка МДЛП и назначение мониторинга движения лекарственных препаратов",
        },
        {
            "id": 2,
            "category": "Регламент добавления врача (doc-01)",
            "query": "Как добавить нового врача в ИС ЛО согласно регламенту?",
            "check": lambda reply: any(w in reply.lower() for w in ["управление сотрудниками", "снилс", "ецп", "добавить", "сотрудник"]),
            "expected": "Пошаговый регламент через модуль «Управление сотрудниками» (поиск по СНИЛС / ЕЦП)",
        },
        {
            "id": 3,
            "category": "Региональный проект: Карта оренбуржца (doc-71)",
            "query": "Какая форма рецептурного бланка заменяется Картой оренбуржца при льготном отпуске?",
            "check": lambda reply: "148" in reply or "148-1/у-04" in reply,
            "expected": "Упоминание рецептурного бланка формы № 148-1/у-04(л)",
        },
        {
            "id": 4,
            "category": "Холодовая цепь и хранение (Онтология склада)",
            "query": "Какой температурный режим хранения требуется для Гам-КОВИД-Вак и в какую камеру склада он размещается?",
            "check": lambda reply: any(w in reply for w in ["-18", "2-8", "2–8", "CHAMBER", "камер", "холод"]),
            "expected": "Температурный режим (заморозка не выше -18 °C или 2-8 °C) и зона хранения",
        },
        {
            "id": 5,
            "category": "Контакты и горячие линии (doc-74)",
            "query": "По каким телефонам горячей линии можно обратиться по вопросам льготного лекарственного обеспечения в Оренбургской области?",
            "check": lambda reply: "122" in reply or "44-89-38" in reply or "507-507" in reply,
            "expected": "Телефоны: 122 (доб. 3), +7 (3532) 44-89-38 или 507-507",
        },
        {
            "id": 6,
            "category": "Схемы маркировки МДЛП (doc-73)",
            "query": "По какой схеме выполняется выбытие лекарственных препаратов по льготным рецептам через регистратор выбытия?",
            "check": lambda reply: "10521" in reply or "выбыти" in reply.lower(),
            "expected": "Упоминание схемы 10521 МДЛП",
        },
        {
            "id": 7,
            "category": "Защита от галлюцинаций (Out-of-domain)",
            "query": "Какая конфигурация протокола BGP используется для маршрутизаторов Cisco на складе?",
            "check": lambda reply: "не найдено" in reply.lower() or "не найден" in reply.lower(),
            "expected": "Фраза 'Сведений по этому вопросу в базе знаний не найдено.' без выдуманных сведений",
        },
    ]

    print("=" * 80)
    print("КОМПЛЕКСНОЕ ТЕСТИРОВАНИЕ ЖИВОГО АГЕНТА VIIO ПО БАЗЕ ЗНАНИЙ (7 ТЕСТОВ)")
    print("=" * 80)

    passed_count = 0
    total_count = len(tests)

    for test in tests:
        tid = test["id"]
        cat = test["category"]
        q = test["query"]
        print(f"\n--- [Тест {tid}/{total_count}] {cat} ---")
        print(f"Вопрос: {q}")

        thread_id = f"suite_t{tid}_{int(time.time())}"
        status, reply, elapsed = ask_agent(q, thread_id)

        print(f"Статус: HTTP {status} | Время ответа: {elapsed:.1f} с")
        print(f"Ответ агента:\n{reply}\n")

        if status == 200 and test["check"](reply):
            print(f"РЕЗУЛЬТАТ: [ПРОЙДЕН]")
            passed_count += 1
        else:
            print(f"РЕЗУЛЬТАТ: [НЕ ПРОЙДЕН] (Ожидалось: {test['expected']})")

    print("\n" + "=" * 80)
    print(f"ИТОГ: Успешно {passed_count} из {total_count} ({passed_count/total_count*100:.1f}%)")
    print("=" * 80)

    assert passed_count >= 6, f"Слишком много сбоев: {passed_count}/{total_count}"


if __name__ == "__main__":
    run_suite()
