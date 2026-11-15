"""Natural-language regression suite. Run inside the main agent container.

Credentials stay in the container environment. Reviews are filled after inspection.
"""
import asyncio
import json
import os
import time
import uuid
from pathlib import Path

import httpx

CASES = [
    ("doc1", "Что такое ИСЛО и для чего она нужна?"),
    ("doc2", "Как выписать электронный рецепт?"),
    ("doc3", "Как в аптеке найти уже выписанный электронный рецепт?"),
    ("doc4", "Где посмотреть список доступных мне отчётов в ИСЛО?"),
    ("doc5", "Как оформить начальные остатки в Госаптеке?"),
    ("doc6", "Как подписать электронный рецепт после выбора сертификата?"),
    ("doc7", "Какие руководства по ИС «Лекарственное обеспечение» доступны? Дай названия и ссылки."),
    ("doc8", "Кто руководит проектом «Янтарь-98417» и когда он запускается?"),
    ("code1", "Напиши функцию Python, которая убирает дубликаты из списка чисел, сохраняя порядок."),
    ("code2", "Напиши Python-функцию, которая складывает цены из списка словарей. У пустого списка результат 0."),
    ("code3", "Напиши SQL, который возвращает товары с ценой выше 100 из таблицы products. Нужны id и name."),
    ("code4", "Напиши функцию Python, которая проверяет, что строка — палиндром, игнорируя пробелы и регистр."),
    ("code5", "Какой контроллер отвечает за настройку каталога отчётов SSRS?"),
    ("db1", "Какие таблицы есть в тестовой PostgreSQL?"),
    ("db2", "Какие три таблицы в тестовой базе занимают больше всего места? Укажи размеры."),
    ("db3", "Есть ли в тестовой базе таблицы с давно неиспользуемыми индексами? Пока только диагностика."),
    ("db4", "Проверь, видны ли сейчас долгие запросы в тестовой базе. Ничего не останавливай."),
    ("plain1", "Перепиши вежливо одним предложением: пришлите отчёт сегодня."),
    ("plain2", "Сколько будет (125 + 75) * 3?"),
    ("plain3", "Объясни простыми словами, зачем нужен индекс в базе данных."),
    ("new_doc1", "ИС ЛО и ИСЛО — это одна система? Для чего она применяется?"),
    ("new_doc2", "Мне нужно выписать рецепт пациенту. Какие действия выполнить в программе?"),
    ("new_doc3", "Какая кнопка завершает подписание электронного рецепта?"),
    ("new_doc4", "В какой инструкции описана выписка электронного рецепта врачом? Дай ссылку."),
    ("new_code1", "Напиши функцию Python, которая возвращает второй по величине уникальный элемент списка чисел. Если его нет — None."),
    ("new_code2", "Исправь Python-функцию: def average(xs): return sum(xs) / len(xs). Для пустого списка нужно вернуть None."),
    ("new_db1", "К какой базе PostgreSQL ты сейчас подключён и под каким пользователем?"),
    ("new_db2", "Сколько строк в таблице drug тестовой базы?"),
    ("new_db3", "Какие столбцы у таблицы receipt_items_unindexed в тестовой базе?"),
    ("new_plain1", "Посчитай 15% от 2400 и прибавь результат к 2400."),
]


async def main():
    run = "full-eval-" + uuid.uuid4().hex[:8]
    print(json.dumps({"run": run, "count": len(CASES)}), flush=True)
    async with httpx.AsyncClient(timeout=180, trust_env=False) as client:
        for name, question in CASES:
            role = "programmer" if "code" in name or "db" in name else "employee"
            thread = run + "-" + name
            started = time.monotonic()
            result = {"case": name, "question": question, "thread": thread, "role": role}
            try:
                response = await client.post(
                    "http://127.0.0.1:8000/api/v1/chat",
                    headers={"X-Internal-Token": os.environ["CHAT_API_KEY"]},
                    json={"message": question, "thread_id": thread,
                          "user_context": {"user_id": run, "role": role, "departments": ["general"]}},
                )
                result.update(http_status=response.status_code, body=response.json())
            except Exception as error:
                result["error"] = type(error).__name__
            result["seconds"] = round(time.monotonic() - started, 1)
            for path in Path("/traces").glob("*.json"):
                try:
                    trace = json.loads(path.read_text())
                except (OSError, ValueError):
                    continue
                if trace.get("thread_id") == thread:
                    result["trace_id"] = trace["id"]
                    result["trace_status"] = trace["status"]
                    break
            print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
