"""Комплексный раннер 40 тестов по базе знаний (БЗ) и онтологии VIIO.

Включает 40 репрезентативных вопросов по 7 ключевым темам:
1. ИС «Лекарственное обеспечение» (ИС ЛО) и модули учета (8 вопросов)
2. Программа «Универсальная карта оренбуржца» и электронные рецепты (5 вопросов)
3. ФГИС МДЛП, Честный Знак и интеграция с 1С (7 вопросов)
4. Программы льготного обеспечения (БСК, НМГ, ОНЛП) и региональные акты (6 вопросов)
5. Федеральное законодательство, ПКУ и наркотические средства (6 вопросов)
6. Онтология аптечного склада, холодовая цепь и процессы (Memgraph) (4 вопроса)
7. Горячие линии, контакты и защита от галлюцинаций (4 вопроса)
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any

DOCUMENTS_URL = os.getenv("DOCUMENTS_URL", "http://localhost:8002")
ASSISTENT_URL = os.getenv("ASSISTENT_URL", "http://localhost:8000")
DOCUMENTS_READ_KEY = os.getenv("DOCUMENTS_READ_KEY", "").strip()
CHAT_KEY = (os.getenv("CHAT_KEY") or os.getenv("CHAT_API_KEY", "")).strip()

QUESTIONS_40: list[dict[str, Any]] = [
    # Группа 1: ИС ЛО и модули учета (1-8)
    {
        "id": 1,
        "category": "ИС ЛО и модули учета",
        "query": "Как добавить нового врача в ИС ЛО согласно регламенту модуля Управление сотрудниками?",
        "expected_docs": ["doc-01"],
        "keywords": ["управление сотрудниками", "снилс", "ецп", "добавить", "врач"],
    },
    {
        "id": 2,
        "category": "ИС ЛО и модули учета",
        "query": "Какой пошаговый алгоритм выписки электронного рецепта в ИС ЛО?",
        "expected_docs": ["doc-02"],
        "keywords": ["рецепт", "льгот", "подписан", "эцп", "выписк"],
    },
    {
        "id": 3,
        "category": "ИС ЛО и модули учета",
        "query": "Как медсестре поликлиники оформить списание препаратов в модуле Медсестра амбулатории?",
        "expected_docs": ["doc-05"],
        "keywords": ["медсестра", "списани", "отделени", "амбулатор"],
    },
    {
        "id": 4,
        "category": "ИС ЛО и модули учета",
        "query": "Каков порядок переноса остатков отделений ИС ЛО при реорганизации медицинской организации?",
        "expected_docs": ["doc-07"],
        "keywords": ["перенос", "остатк", "реорганизаци", "мо"],
    },
    {
        "id": 5,
        "category": "ИС ЛО и модули учета",
        "query": "Какие особенности работы установлены для пользователя с ролью Медсестра НС и ПВ в ИС ЛО?",
        "expected_docs": ["doc-08"],
        "keywords": ["медсестра", "нс", "пв", "журнал", "учет"],
    },
    {
        "id": 6,
        "category": "ИС ЛО и модули учета",
        "query": "Как сформировать сводную заявку на лекарственные препараты по совместным закупкам?",
        "expected_docs": ["doc-16"],
        "keywords": ["совместн", "закупк", "заявк", "сводн"],
    },
    {
        "id": 7,
        "category": "ИС ЛО и модули учета",
        "query": "Каков порядок проведения инвентаризации в модуле Учет в отделении?",
        "expected_docs": ["doc-24"],
        "keywords": ["инвентаризаци", "опис", "фактическ", "остатк"],
    },
    {
        "id": 8,
        "category": "ИС ЛО и модули учета",
        "query": "Как осуществляется регистрация и ведение паллиативных пациентов в ИС ЛО?",
        "expected_docs": ["doc-42"],
        "keywords": ["паллиативн", "пациент", "регистраци", "обезболиван"],
    },

    # Группа 2: Карта оренбуржца и электронные рецепты (9-13)
    {
        "id": 9,
        "category": "Карта оренбуржца и электронный рецепт",
        "query": "Какая форма бумажного рецептурного бланка заменяется Картой оренбуржца при льготном отпуске?",
        "expected_docs": ["doc-71"],
        "keywords": ["148", "148-1/у-04", "оренбуржц"],
    },
    {
        "id": 10,
        "category": "Карта оренбуржца и электронный рецепт",
        "query": "Как в аптечной программе найти электронный рецепт по номеру СНИЛС или карте жителя Оренбургской области?",
        "expected_docs": ["doc-29", "doc-71"],
        "keywords": ["снилс", "карт", "поиск", "рецепт"],
    },
    {
        "id": 11,
        "category": "Карта оренбуржца и электронный рецепт",
        "query": "Требуется ли согласие гражданина на получение льготных препаратов с использованием Карты оренбуржца?",
        "expected_docs": ["doc-71"],
        "keywords": ["согласи", "гражданин", "пациент"],
    },
    {
        "id": 12,
        "category": "Карта оренбуржца и электронный рецепт",
        "query": "Каким распоряжением Минздрава Оренбургской области регламентировано внедрение сервиса Электронный рецепт?",
        "expected_docs": ["doc-67", "doc-78"],
        "keywords": ["1355", "распоряжени", "электронн", "рецепт"],
    },
    {
        "id": 13,
        "category": "Карта оренбуржца и электронный рецепт",
        "query": "Разрешена ли выписка платных рецептов в информационной системе Лекарственное обеспечение?",
        "expected_docs": ["doc-63"],
        "keywords": ["платн", "рецепт", "1306"],
    },

    # Группа 3: ФГИС МДЛП, Честный Знак и 1С (14-20)
    {
        "id": 14,
        "category": "МДЛП и маркировка Честный Знак",
        "query": "Что такое МДЛП и на основании каких нормативных актов она функционирует?",
        "expected_docs": ["doc-73", "doc-76"],
        "keywords": ["мдлп", "1556", "мониторинг движения", "маркировк"],
    },
    {
        "id": 15,
        "category": "МДЛП и маркировка Честный Знак",
        "query": "По какой схеме МДЛП выполняется выбытие лекарственных препаратов по льготным рецептам через регистратор выбытия?",
        "expected_docs": ["doc-73"],
        "keywords": ["10521", "регистратор выбытия", "льготн"],
    },
    {
        "id": 16,
        "category": "МДЛП и маркировка Честный Знак",
        "query": "По какой схеме МДЛП фиксируется списание или уничтожение лекарственных средств через регистратор выбытия?",
        "expected_docs": ["doc-73"],
        "keywords": ["10531", "списани", "уничтожени"],
    },
    {
        "id": 17,
        "category": "МДЛП и маркировка Честный Знак",
        "query": "Какой статус в системе Честный Знак присваивается упаковке после успешной фиксации отпуска по схеме 10521?",
        "expected_docs": ["doc-73"],
        "keywords": ["in_discount_prescription_sale", "отпуск", "статус"],
    },
    {
        "id": 18,
        "category": "МДЛП и маркировка Честный Знак",
        "query": "Как настроить типовой обмен между 1С Больничная аптека и 1С Бухгалтерия государственного учреждения?",
        "expected_docs": ["doc-32"],
        "keywords": ["больничн", "аптек", "бухгалтери", "обмен"],
    },
    {
        "id": 19,
        "category": "МДЛП и маркировка Честный Знак",
        "query": "Как передать маркированные остатки лекарственных препаратов из одной базы МО в другую?",
        "expected_docs": ["doc-26"],
        "keywords": ["маркирован", "остатк", "передач", "мо"],
    },
    {
        "id": 20,
        "category": "МДЛП и маркировка Честный Знак",
        "query": "В каких форматах поддерживается загрузка электронных накладных в ИС Аптечный учёт в МО?",
        "expected_docs": ["doc-34"],
        "keywords": ["накладн", "формат", "электронн"],
    },

    # Группа 4: Льготные программы (БСК, НМГ, ОНЛП) и акты МО (21-26)
    {
        "id": 21,
        "category": "Льготные программы и акты региона",
        "query": "Какими нормативными актами регулируется обеспечение детей системами непрерывного мониторирования глюкозы (НМГ) в Оренбургской области?",
        "expected_docs": ["doc-03", "doc-04", "doc-78"],
        "keywords": ["нмг", "58-п", "диабет", "глюкоз"],
    },
    {
        "id": 22,
        "category": "Льготные программы и акты региона",
        "query": "Какой порядок действий предусмотрен для врачей при первичном назначении систем НМГ ребенку с диабетом 1-го типа?",
        "expected_docs": ["doc-03"],
        "keywords": ["нмг", "врач", "комисси", "протокол"],
    },
    {
        "id": 23,
        "category": "Льготные программы и акты региона",
        "query": "Какие категории пациентов включаются в региональную программу обеспечения лекарствами при болезнях системы кровообращения (БСК)?",
        "expected_docs": ["doc-09", "doc-10"],
        "keywords": ["бск", "740", "кровообращени", "инфаркт"],
    },
    {
        "id": 24,
        "category": "Льготные программы и акты региона",
        "query": "Каким документом утверждены региональные коды категорий граждан для получения социальной помощи в Оренбургской области?",
        "expected_docs": ["doc-64", "doc-78"],
        "keywords": ["883", "код", "категори", "льгот"],
    },
    {
        "id": 25,
        "category": "Льготные программы и акты региона",
        "query": "Какая организация определена единым оператором региональной информационной системы Лекарственное обеспечение?",
        "expected_docs": ["doc-69"],
        "keywords": ["оас", "аптечные склады", "оператор", "2983"],
    },
    {
        "id": 26,
        "category": "Льготные программы и акты региона",
        "query": "Для чего создан Центр персонализированного сопровождения льготного лекарственного обеспечения (ЦПС ЛЛО)?",
        "expected_docs": ["doc-68"],
        "keywords": ["цпс", "лло", "2719", "сопровождени"],
    },

    # Группа 5: Федеральное законодательство, ПКУ и НС/ПВ (27-32)
    {
        "id": 27,
        "category": "Федеральное законодательство и ПКУ",
        "query": "Каким приказом Минздрава России утверждены формы рецептурных бланков и порядок назначения лекарственных препаратов?",
        "expected_docs": ["doc-49", "doc-72"],
        "keywords": ["1094н", "назначени", "рецептурн", "бланк"],
    },
    {
        "id": 28,
        "category": "Федеральное законодательство и ПКУ",
        "query": "Какие основные требования установлены Приказом Минздрава РФ № 1093н к правилам отпуска лекарственных препаратов аптечными организациями?",
        "expected_docs": ["doc-52", "doc-72"],
        "keywords": ["1093н", "отпуск", "правил"],
    },
    {
        "id": 29,
        "category": "Федеральное законодательство и ПКУ",
        "query": "На какой максимальный срок допускается расчет потребности в наркотических средствах при оказании паллиативной помощи взрослым?",
        "expected_docs": ["doc-44"],
        "keywords": ["60", "дней", "наркотическ", "паллиативн"],
    },
    {
        "id": 30,
        "category": "Федеральное законодательство и ПКУ",
        "query": "Каким приказом Минздрава РФ утвержден перечень лекарственных средств, подлежащих предметно-количественному учету (ПКУ)?",
        "expected_docs": ["doc-51", "doc-43"],
        "keywords": ["183н", "пку", "предметно-количествен"],
    },
    {
        "id": 31,
        "category": "Федеральное законодательство и ПКУ",
        "query": "Какие ключевые права граждан в сфере охраны здоровья закреплены в Федеральном законе № 323-ФЗ?",
        "expected_docs": ["doc-75"],
        "keywords": ["323-фз", "охраны здоровья", "граждан", "помощ"],
    },
    {
        "id": 32,
        "category": "Федеральное законодательство и ПКУ",
        "query": "Какие категории населения имеют право на бесплатное обеспечение лекарствами согласно Постановлению Правительства РФ № 890?",
        "expected_docs": ["doc-76"],
        "keywords": ["890", "постановлени", "бесплатн", "льгот"],
    },

    # Группа 6: Онтология аптечного склада, холодовая цепь и процессы (33-36)
    {
        "id": 33,
        "category": "Онтология склада и холодовая цепь",
        "query": "Какой температурный режим хранения предусмотрен для препарата Гам-КОВИД-Вак и в какой складской зоне он содержится?",
        "expected_docs": ["doc-ontology", "doc-62"],
        "keywords": ["2-8", "2–8", "-18", "камер", "холод"],
    },
    {
        "id": 34,
        "category": "Онтология склада и холодовая цепь",
        "query": "Какие этапы и роли включает сквозной бизнес-процесс приёмки товара на аптечном складе?",
        "expected_docs": ["doc-ontology", "doc-25"],
        "keywords": ["приемк", "входной контроль", "фармацевт", "шаг"],
    },
    {
        "id": 35,
        "category": "Онтология склада и холодовая цепь",
        "query": "Какая система является мастер-системой (Source of Truth) для номенклатуры и складских остатков аптечного склада?",
        "expected_docs": ["doc-ontology"],
        "keywords": ["1с", "erp", "wms", "мастер"],
    },
    {
        "id": 36,
        "category": "Онтология склада и холодовая цепь",
        "query": "Что означает термин ЖНВЛП и каково его практическое значение при ценообразовании?",
        "expected_docs": ["doc-abbreviations", "doc-ontology"],
        "keywords": ["жизненно необходим", "жнвлп", "цен"],
    },

    # Группа 7: Горячие линии, поддержка и Out-of-domain (37-40)
    {
        "id": 37,
        "category": "Контакты и техническая поддержка",
        "query": "По какому единому номеру 122 или телефону колл-центра работает горячая линия по лекарствам в Оренбургской области?",
        "expected_docs": ["doc-74"],
        "keywords": ["122", "507-507", "44-89-38", "горяч", "колл-центр"],
    },
    {
        "id": 38,
        "category": "Контакты и техническая поддержка",
        "query": "По каким контактам доступна техническая поддержка пользователей ИС ЛО от ГАУЗ ОАС?",
        "expected_docs": ["doc-12", "doc-70"],
        "keywords": ["islo@oas56.ru", "поддержк", "оас", "телефон"],
    },
    {
        "id": 39,
        "category": "Защита от галлюцинаций (Out-of-domain)",
        "query": "Какая конфигурация протокола динамической маршрутизации OSPF применяется на коммутаторах ядра сети склада?",
        "expected_docs": [],
        "keywords": ["не найдено", "не найден"],
        "is_out_of_domain": True,
    },
    {
        "id": 40,
        "category": "Защита от галлюцинаций (Out-of-domain)",
        "query": "По какому кулинарному рецепту готовится традиционный оренбургский пирог с рыбой?",
        "expected_docs": [],
        "keywords": ["не найдено", "не найден"],
        "is_out_of_domain": True,
    },
]


def test_retrieval_suite() -> list[dict[str, Any]]:
    """Быстрый аудит гибридного конвейера поиска (Qdrant + Memgraph) для всех 40 вопросов."""
    print("=" * 80)
    print("ЭТАП 1: БЫСТРЫЙ АУДИТ HYBRID RETRIEVAL (QDRANT + MEMGRAPH) ДЛЯ 40 ВОПРОСОВ")
    print("=" * 80)

    url = f"{DOCUMENTS_URL}/api/v1/knowledge/search"
    headers = {
        "Content-Type": "application/json",
        "X-Internal-Token": DOCUMENTS_READ_KEY,
    }

    results = []
    t_start = time.monotonic()

    for item in QUESTIONS_40:
        qid = item["id"]
        q = item["query"]
        cat = item["category"]
        is_ood = item.get("is_out_of_domain", False)

        payload = {
            "query": q,
            "k": 4,
            "diagnostics": True,
            "user_context": {"role": "employee", "user_id": f"eval_{qid}"},
        }

        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
        t0 = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                elapsed_ms = round((time.monotonic() - t0) * 1000, 1)
                data = json.loads(resp.read().decode("utf-8"))
                context = data.get("context", "")
                diag = data.get("diagnostics", {})

                # Проверка нахождения ожидаемых документов или ключевых слов
                found_match = False
                matched_docs = []
                for edoc in item["expected_docs"]:
                    if edoc.lower() in context.lower():
                        found_match = True
                        matched_docs.append(edoc)

                # Проверка ключевых слов
                kw_matches = [kw for kw in item["keywords"] if kw.lower() in context.lower()]
                if len(kw_matches) >= 1 or is_ood:
                    found_match = True

                res = {
                    "id": qid,
                    "category": cat,
                    "query": q,
                    "status": "PASS" if found_match else "FAIL",
                    "latency_ms": elapsed_ms,
                    "graph_facts": diag.get("graph_facts_count", 0),
                    "matched_docs": matched_docs,
                    "kw_matches": kw_matches,
                    "context_len": len(context),
                }
                results.append(res)
                mark = "✓ PASS" if found_match else "✗ FAIL"
                print(f"[{qid:02d}/40] {mark} | {elapsed_ms:5.1f} мс | facts: {res['graph_facts']} | {cat[:28]}: {q[:45]}...")
        except Exception as e:
            res = {
                "id": qid,
                "category": cat,
                "query": q,
                "status": "ERROR",
                "error": str(e),
                "latency_ms": round((time.monotonic() - t0) * 1000, 1),
            }
            results.append(res)
            print(f"[{qid:02d}/40] ERROR: {e}")

    total_time = time.monotonic() - t_start
    passed = sum(1 for r in results if r["status"] == "PASS")
    avg_latency = sum(r.get("latency_ms", 0) for r in results) / len(results)

    print("\n" + "-" * 80)
    print(f"ИТОГ ЭТАПА 1 (RETRIEVAL): {passed}/40 успешно ({passed/40*100:.1f}%) | Время: {total_time:.2f} с | Ср. латентность: {avg_latency:.1f} мс")
    print("-" * 80 + "\n")
    return results


def test_agent_generation(selected_ids: list[int] | None = None) -> list[dict[str, Any]]:
    """Тестирование сквозной генерации живого агента через /api/v1/chat."""
    print("=" * 80)
    print("ЭТАП 2: СКВОЗНОЕ ТЕСТИРОВАНИЕ ЖИВОГО АГЕНТА ASSISTENT (:8000)")
    print("=" * 80)

    url = f"{ASSISTENT_URL}/api/v1/chat"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {CHAT_KEY}",
        "X-Internal-Token": CHAT_KEY,
    }

    target_items = [q for q in QUESTIONS_40 if selected_ids is None or q["id"] in selected_ids]
    results = []

    for idx, item in enumerate(target_items, 1):
        qid = item["id"]
        q = item["query"]
        cat = item["category"]
        is_ood = item.get("is_out_of_domain", False)

        print(f"\n>>> [{idx}/{len(target_items)}] Тест #{qid} ({cat})")
        print(f"Вопрос: {q}")

        payload = {
            "message": q,
            "thread_id": f"kb40_run_{qid}_{int(time.time())}",
            "user_context": {"role": "employee", "user_id": f"user_eval_{qid}"},
        }

        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
        t0 = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                elapsed_s = round(time.monotonic() - t0, 1)
                data = json.loads(resp.read().decode("utf-8"))
                reply = data.get("reply", "")

                # Валидация ответа
                if is_ood:
                    passed = "не найдено" in reply.lower() or "не найден" in reply.lower()
                else:
                    passed = any(kw.lower() in reply.lower() for kw in item["keywords"])

                res = {
                    "id": qid,
                    "category": cat,
                    "query": q,
                    "status": "PASS" if passed else "FAIL",
                    "latency_s": elapsed_s,
                    "reply": reply,
                }
                results.append(res)

                mark = "✓ ПРОЙДЕН" if passed else "✗ НЕ ПРОЙДЕН"
                print(f"Статус: {mark} | Время генерации: {elapsed_s} с")
                print(f"Ответ агента:\n{reply}\n")
        except Exception as e:
            elapsed_s = round(time.monotonic() - t0, 1)
            res = {
                "id": qid,
                "category": cat,
                "query": q,
                "status": "ERROR",
                "error": str(e),
                "latency_s": elapsed_s,
                "reply": "",
            }
            results.append(res)
            print(f"Статус: ✗ ОШИБКА ({e}) | Время: {elapsed_s} с")

    passed_count = sum(1 for r in results if r["status"] == "PASS")
    print("\n" + "=" * 80)
    print(f"ИТОГ ЭТАПА 2: Успешно {passed_count}/{len(target_items)} ({passed_count/len(target_items)*100:.1f}%)")
    print("=" * 80)
    return results


def save_reports(retrieval_res: list[dict], agent_res: list[dict]):
    reports_dir = Path("reports")
    reports_dir.mkdir(exist_ok=True)

    json_path = reports_dir / "kb_40_test_report.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "retrieval": retrieval_res,
                "agent": agent_res,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    md_path = reports_dir / "kb_40_test_report.md"
    passed_retrieval = sum(1 for r in retrieval_res if r["status"] == "PASS")
    passed_agent = sum(1 for r in agent_res if r["status"] == "PASS")

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("# Отчет о тестировании базы знаний VIIO (40 вопросов)\n\n")
        f.write(f"- **Дата запуска**: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}\n")
        f.write(f"- **Поисковый конвейер (Hybrid GraphRAG)**: {passed_retrieval}/40 ({passed_retrieval/40*100:.1f}%)\n")
        f.write(f"- **Генерация живого агента (Assistent)**: {passed_agent}/{len(agent_res)} ({passed_agent/len(agent_res)*100:.1f}%)\n\n")

        f.write("## 1. Сводка результатов по категориям\n\n")
        f.write("| ID | Категория | Вопрос | Поиск (мс) | Агент | Латентность |\n")
        f.write("|:---:|---|---|:---:|:---:|:---:|\n")

        agent_map = {r["id"]: r for r in agent_res}
        for r in retrieval_res:
            qid = r["id"]
            ag = agent_map.get(qid, {})
            ag_stat = ag.get("status", "—")
            ag_lat = f"{ag.get('latency_s', 0):.1f} с" if "latency_s" in ag else "—"
            f.write(f"| {qid} | {r['category']} | {r['query']} | {r.get('latency_ms', 0):.1f} | {ag_stat} | {ag_lat} |\n")

    print(f"Отчеты сохранены: {json_path} и {md_path}")


if __name__ == "__main__":
    # По умолчанию запускаем полный аудит Retrieval (40 вопросов)
    # и сквозную генерацию на представительной выборке ключевых вопросов из всех 7 категорий
    retrieval_results = test_retrieval_suite()

    # Выборка по 1-2 вопроса из каждой категории для сквозного тестирования живого агента
    # (чтобы уложиться в разумное время инференса локальной 8B модели)
    sample_ids = [1, 2, 9, 11, 15, 17, 21, 25, 27, 29, 33, 35, 37, 39, 40]
    agent_results = test_agent_generation(selected_ids=sample_ids)

    save_reports(retrieval_results, agent_results)
