"""Нормализация меток: каждому узлу присваивается ровно ОДНА главная метка онтологии."""

from __future__ import annotations

import logging
from neo4j import GraphDatabase

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("normalize_labels")

SYSTEM_NAMES = {
    "ИС «Лекарственное обеспечение»",
    "ИС «Аптечный учет в МО»",
    "ИС «Аптечный учёт в МО»",
    "ИС «Аптечный учет»",
    "1С:Предприятие",
    "1С:Маркировка. МДЛП",
    "ИС «МДЛП»",
    "ЕГИЗС",
    "Регистр орфанных заболеваний",
    "Программа ведения регистра больных высокозатратными нозологиями",
    "Аптечный учёт в МО",
    "Система маркировки",
}

ROLE_NAMES = {
    "Врач",
    "Медсестра",
    "Старшая медсестра",
    "Старшая медицинская сестра",
    "Администратор",
    "Фармацевт",
    "Координатор НМГ",
    "Сотрудник",
    "Контрагент",
}


def determine_primary_label(name: str, current_labels: set[str], original_type: str | None) -> str:
    # 1. Системы
    if name in SYSTEM_NAMES:
        return "System"

    # 2. Пути меню (содержат >)
    if ">" in name:
        return "MenuPath"

    # 3. Роли
    if name in ROLE_NAMES or name.startswith("Врач-") or name.startswith("Медсестра "):
        return "Role"

    # 4. Если в названии глагол или ключевые слова действий
    action_words = [
        "Создать", "Загрузить", "Выгрузить", "Провести", "Печать", "Удалить", "Сформировать",
        "Заполнить", "Выбрать", "Открыть", "Нажать", "Добавить", "Перенести", "Оформить",
        "Отпустить", "Копировать", "Редактировать", "Показать", "Проверить"
    ]
    if any(name.startswith(w) for w in action_words):
        return "Operation"

    # 5. Если условие
    cond_words = ["Только", "Если", "При наличии", "Не более", "Не менее", "Цена", "Срок", "Статус"]
    if any(name.startswith(w) for w in cond_words):
        return "Condition"

    # 6. Если категория препаратов
    drug_words = ["препараты", "ЖНВЛП", "ПКУ", "лекарственные", "МНН", "вакцины", "серии", "СГТИН"]
    if any(w.lower() in name.lower() for w in drug_words) and "Инструкция" not in name:
        return "DrugCategory"

    # 7. Опора на исходный тип LLM, если он валиден
    valid_ontology = {"Operation", "Condition", "MenuPath", "DrugCategory", "Role", "System"}
    if original_type in valid_ontology:
        return original_type

    # 8. По приоритету из текущих меток
    for priority in ["Operation", "MenuPath", "Condition", "DrugCategory", "Role", "System"]:
        if priority in current_labels:
            return priority

    return "Entity"


def normalize(memgraph_uri: str = "bolt://127.0.0.1:7687"):
    driver = GraphDatabase.driver(memgraph_uri, auth=("", ""))

    with driver.session() as s:
        nodes = s.run(
            """
            MATCH (n)
            WHERE NOT n:Document
            RETURN id(n) AS id, n.name AS name, n.type AS original_type, labels(n) AS l;
            """
        ).data()

        logger.info(f"Всего узлов для нормализации меток: {len(nodes)}")

        updated_count = 0
        all_labels = ["System", "MenuPath", "Operation", "Condition", "Role", "DrugCategory"]

        for row in nodes:
            nid = row["id"]
            name = (row["name"] or "").strip()
            orig_type = row["original_type"]
            current_l = set(row["l"]) - {"Entity"}

            primary = determine_primary_label(name, current_l, orig_type)

            # Удаляем все онтологические метки кроме выбранной
            labels_to_remove = [l for l in all_labels if l != primary and l in current_l]
            if labels_to_remove or primary not in current_l:
                remove_clause = " ".join(f"REMOVE n:{l}" for l in labels_to_remove)
                set_clause = f"SET n:{primary}, n.type = '{primary}'"
                if primary == "MenuPath":
                    set_clause += ", n.full_path = n.name"
                elif primary == "Condition":
                    set_clause += ", n.rule_text = n.name"
                elif primary == "Role":
                    set_clause += ", n.title = n.name"

                query = f"MATCH (n) WHERE id(n) = {nid} {remove_clause} {set_clause};"
                s.run(query)
                updated_count += 1

        logger.info(f"Обновлено узлов: {updated_count}")

        # Сохраняем снапшот
        s.run("CREATE SNAPSHOT;")
        logger.info("Снапшот обновлен на диск.")

    driver.close()


if __name__ == "__main__":
    normalize()
