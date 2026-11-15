"""Скрипт очистки и нормализации аномалий в графе знаний Memgraph."""

from __future__ import annotations

import logging
from pathlib import Path
from neo4j import GraphDatabase

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("heal_graph")

STORAGE_DIR = Path("data/public-canonical-storage")


def heal_documents(session):
    """Исправление заголовков 3 повреждённых документов в БД и файловой системе."""
    corrections = {
        "2084c73191b74093b5c0f5b39fab8d6b": (
            "ИС «Аптечный учёт в МО»: Руководство пользователя — Модуль «Отчёты»",
            "https://gosapteka56.ru/files/Farmkomitet/is_lo/docs_apt_uchet/01-instruct-23.05.22.pdf",
        ),
        "3a1dcbc56ba04434bf8fcce2fbe4ce58": (
            "Инструкция по работе в модуле «Медсестра» отделения анестезиологии и реанимации",
            "https://gosapteka56.ru/files/Farmkomitet/is_lo/docs_is_lo/19-22.01.2020.pdf",
        ),
        "b4554a06c27c4682b4f5be4737b75d4f": (
            "Распоряжение Минздрава Оренбургской области № 740 от 08.04.2021 (О внесении изменений в распоряжение № 955: БСК)",
            "https://gosapteka56.ru/files/Farmkomitet/is_lo/docs_is_lo/bsk740-08.04.2021.pdf",
        ),
    }

    for doc_id, (proper_title, source_url) in corrections.items():
        # 1. Обновляем узел в Memgraph
        session.run(
            """
            MATCH (d:Document {id: $doc_id})
            SET d.title = $title, d.source_url = $source_url
            """,
            doc_id=doc_id,
            title=proper_title,
            source_url=source_url,
        )
        logger.info(f"Обновлен Document {doc_id[:8]}: '{proper_title}'")

        # 2. Обновляем заголовок в самом файле .txt на диске
        file_path = STORAGE_DIR / f"{doc_id}.txt"
        if file_path.exists():
            content = file_path.read_text(encoding="utf-8", errors="replace")
            # Проверяем, есть ли уже заголовок markdown
            if not content.startswith("# "):
                header = f"# {proper_title}\n\nИсточник: {source_url}\nСтраница раздела: https://gosapteka56.ru/onas/osmp/is-lekarstvennoe-obespechenie.html\n\n"
                file_path.write_text(header + content, encoding="utf-8")
                logger.info(f"Добавлен markdown-заголовок в файл {file_path.name}")


def clean_systems_and_menus(session):
    """Очистка сущностей System и MenuPath от мусора и переклассификация."""
    # Пути меню, ошибочно помеченные как System
    session.run(
        """
        MATCH (s:System)
        WHERE s.name CONTAINS '>'
        REMOVE s:System
        SET s:MenuPath, s.full_path = s.name, s.type = 'MenuPath';
        """
    )

    # Длинные предложения (описания фич или фрагменты текста), ошибочно попавшие в System
    session.run(
        """
        MATCH (s:System)
        WHERE size(s.name) > 70 OR s.name STARTS WITH 'Реализована' OR s.name STARTS WITH 'О внесении'
        REMOVE s:System
        SET s:Condition, s.type = 'Condition', s.rule_text = s.name;
        """
    )

    # Нормализация систем министерства
    session.run(
        """
        MATCH (s:System)
        WHERE s.name IN ['МИНИСТЕРСТВО ЗДРАВООХРАНЕНИЯ', 'ОРЕНБУРГСКОЙ ОБЛАСТИ', 'РАСПОРЯЖЕНИЕ', 'Г. Оренбург № F40', 'г. Оренбург № 740']
        REMOVE s:System
        SET s:Entity;
        """
    )


def clean_roles(session):
    """Очистка сущностей Role от глаголов-действий и названий систем."""
    # Действия, попавшие в Role
    verbs = ["Провести", "Отпустить ЛС", "Копировать отпуск", "Печать"]
    for v in verbs:
        session.run(
            """
            MATCH (r:Role {name: $name})
            REMOVE r:Role
            SET r:Operation, r.type = 'Operation';
            """,
            name=v,
        )

    # Системы, попавшие в Role
    session.run(
        """
        MATCH (r:Role)
        WHERE r.name CONTAINS '«Лекарственное обеспечение»'
        REMOVE r:Role
        SET r:System, r.type = 'System';
        """
    )


def connect_isolated_documents(session):
    """Связывание изолированных документов с ключевыми системами."""
    session.run(
        """
        MATCH (d:Document {id: 'd51c69e8b9f44cd9834bb1c6c818d1a4'})
        MERGE (s:System:Entity {name: '1С:Предприятие'})
        MERGE (d)-[:MENTIONS]->(s);
        """
    )


def main():
    driver = GraphDatabase.driver("bolt://127.0.0.1:7687", auth=("", ""))
    with driver.session() as s:
        logger.info("Запуск очистки и нормализации...")
        heal_documents(s)
        clean_systems_and_menus(s)
        clean_roles(s)
        connect_isolated_documents(s)

        # Сохранение снапшота в Memgraph
        s.run("CREATE SNAPSHOT;")
        logger.info("Снапшот Memgraph успешно обновлён.")

    driver.close()
    logger.info("Все исправления применены!")


if __name__ == "__main__":
    main()
