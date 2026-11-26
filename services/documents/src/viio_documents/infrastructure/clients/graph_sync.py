"""Модуль синхронизации документов с онтологическим графом знаний Memgraph (Semantic GraphRAG).

Обеспечивает автоматическое обновление графа при загрузке, обновлении версий
и удалении документов в системе VIIO:
1. Создание/обновление узла (Document) в Memgraph.
2. Автоматическое распознавание и связывание упоминаний нормативных актов (Regulation).
3. Привязка к информационным системам (System), отделам (Department) и процессам (Process).
4. Очистка графа при удалении документов.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

import anyio.to_thread

try:
    from neo4j import GraphDatabase
    NEO4J_AVAILABLE = True
except ImportError:
    NEO4J_AVAILABLE = False

logger = logging.getLogger(__name__)


# Известные регламенты и их шаблоны для извлечения из текста
KNOWN_REGULATIONS: list[tuple[str, str, str]] = [
    ("FZ-323", "Федеральный закон № 323-ФЗ «Об основах охраны здоровья граждан в РФ»", r"(?:323[\s\-]фз|323[\s\-]fz|об основах охраны здоровья)"),
    ("FZ-61", "Федеральный закон № 61-ФЗ «Об обращении лекарственных средств»", r"(?:61[\s\-]фз|61[\s\-]fz|об обращении лекарственных средств)"),
    ("FZ-178", "Федеральный закон № 178-ФЗ «О государственной социальной помощи»", r"(?:178[\s\-]фз|178[\s\-]fz|о государственной социальной помощи|онлп)"),
    ("FZ-3", "Федеральный закон № 3-ФЗ «О наркотических средствах и психотропных веществах»", r"(?:3[\s\-]фз|3[\s\-]fz|о наркотических средствах|психотропных веществ)"),
    ("PP-890", "Постановление Правительства РФ № 890 от 30.07.1994 (льготы)", r"(?:постановление.*890|890\b)"),
    ("PP-1556", "Постановление Правительства РФ № 1556 от 14.12.2018 (МДЛП)", r"(?:постановление.*1556|1556\b)"),
    ("MZ-RF-1094n", "Приказ Минздрава России № 1094н (назначение и выписка)", r"(?:1094н|1094n)"),
    ("MZ-RF-1093n", "Приказ Минздрава России № 1093н (правила отпуска)", r"(?:1093н|1093n)"),
    ("MZ-RF-947n", "Приказ Минздрава России № 947н (электронный документооборот)", r"(?:947н|947n)"),
    ("MZ-RF-459n", "Приказ Минздрава России № 459н (новый перечень ПКУ с 01.09.2024)", r"(?:459н|459n)"),
    ("PP-OO-528-pp", "Постановление Правительства Оренбургской области № 528-пп (ГИСЗ ОО)", r"(?:528[\s\-]пп|528[\s\-]pp)"),
    ("PP-OO-597-pp", "Постановление Правительства Оренбургской области № 597-пп (электронный рецепт)", r"(?:597[\s\-]пп|597[\s\-]pp)"),
    ("MZ-OO-2983", "Распоряжение Минздрава Оренбургской области № 2983 (оператор ИС ЛО)", r"(?:2983|распоряжение.*2983)"),
    ("MZ-OO-740", "Распоряжение Минздрава Оренбургской области № 740 (программа БСК)", r"(?:740\b|распоряжение.*740)"),
    ("MZ-OO-390", "Распоряжение Минздрава Оренбургской области № 390 (программа БСК 2024)", r"(?:390\b|распоряжение.*390)"),
    ("MZ-OO-1627", "Распоряжение Минздрава Оренбургской области № 1627 (программа БСК 2025)", r"(?:1627\b|распоряжение.*1627)"),
    ("MZ-OO-883", "Распоряжение Минздрава Оренбургской области № 883 (коды льгот)", r"(?:883\b|распоряжение.*883)"),
    ("MZ-OO-1119", "Распоряжение Минздрава Оренбургской области № 1119 (мониторинг дефектуры)", r"(?:1119\b|распоряжение.*1119)"),
    ("MZ-OO-2719", "Распоряжение Минздрава Оренбургской области № 2719 (ЦПС ЛЛО)", r"(?:2719\b|распоряжение.*2719)"),
    ("MZ-OO-262", "Распоряжение Минздрава Оренбургской области № 262 (эксплуатация ИС ЛО)", r"(?:262\b|распоряжение.*262)"),
]

# Известные системы и их шаблоны
KNOWN_SYSTEMS: list[tuple[str, str]] = [
    ("ИС «Лекарственное обеспечение»", r"(?:ис[\s\-_]ло|исло|лекарственное обеспечение)"),
    ("ГИСЗ Оренбургской области", r"(?:гисз|гисз[\s\-_]оо)"),
    ("ФГИС МДЛП «Честный Знак»", r"(?:мдлп|честный[\s\-_]знак|datamatrix|регистратор выбытия)"),
    ("Сервис «Универсальная карта оренбуржца»", r"(?:карта оренбуржца|карта жителя|социальн.*карт)"),
    ("ЕГИСЗ (РЭМД)", r"(?:егисз|рэмд|сэмд)"),
    ("1С:Предприятие", r"(?:1с|1c|больничная аптека|аптечный учет)"),
]

# Известные отделы
KNOWN_DEPARTMENTS: list[tuple[str, str]] = [
    ("OAS_OSMP", r"(?:осмп|сопровождения медицинских программ|техподдержк|islo@oas56)"),
    ("OAS_IT", r"(?:оит|информационных технологий|it@oas56)"),
    ("OAS_WAREHOUSE", r"(?:склад|складск|приёмк|хранени|экспедици)"),
    ("OAS_PROCUREMENT", r"(?:закупок|льготного лекарственного обеспечения)"),
    ("OAS_QA", r"(?:фармацевтический контроль|обеспечения качества|гдп|gdp)"),
    ("MZ_OAS_LEK", r"(?:отдел организации лекарственного обеспечения|минздрав|мз оо)"),
    ("MZ_PROGNOZ", r"(?:прогнозирования и анализа)"),
]


class GraphSyncService:
    """Сервис инкрементальной синхронизации документов с Memgraph."""

    def __init__(
        self,
        settings: Any = None,
        uri: str | None = None,
        user: str = "",
        password: str = "",
    ) -> None:
        self.settings = settings
        self.enabled = getattr(settings, "memgraph_enabled", True) if settings else True
        if os.getenv("MEMGRAPH_ENABLED", "").lower() in ("0", "false", "no"):
            self.enabled = False

        self.uri = uri or getattr(settings, "memgraph_uri", None) or os.getenv("MEMGRAPH_URI", "bolt://memgraph:7687")
        self.user = user or getattr(settings, "memgraph_user", None) or os.getenv("MEMGRAPH_USER", "")
        self.password = password or getattr(settings, "memgraph_password", None) or os.getenv("MEMGRAPH_PASSWORD", "")
        self._driver = None

    def _get_driver(self):
        if not self.enabled or not NEO4J_AVAILABLE:
            return None
        if self._driver is None:
            try:
                self._driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password))
            except Exception as e:
                logger.warning("Не удалось инициализировать подключение к Memgraph (%s): %s", self.uri, e)
                return None
        return self._driver

    def close(self):
        if self._driver:
            try:
                self._driver.close()
            except Exception:
                pass
            self._driver = None

    @staticmethod
    def extract_relations(title: str, content: str) -> dict[str, list[Any]]:
        """Извлекает нормативные акты, системы и отделы из заголовка и содержимого текста."""
        sample_text = f"{title}\n{content[:20000]}".lower()

        matched_regs = []
        for code, reg_title, pattern in KNOWN_REGULATIONS:
            if re.search(pattern, sample_text, re.IGNORECASE):
                matched_regs.append({"code": code, "title": reg_title})

        matched_systems = []
        for sys_name, pattern in KNOWN_SYSTEMS:
            if re.search(pattern, sample_text, re.IGNORECASE):
                matched_systems.append(sys_name)

        matched_depts = []
        for dept_code, pattern in KNOWN_DEPARTMENTS:
            if re.search(pattern, sample_text, re.IGNORECASE):
                matched_depts.append(dept_code)

        return {
            "regulations": matched_regs,
            "systems": matched_systems,
            "departments": matched_depts,
        }

    async def sync_document(
        self,
        doc_id: str,
        title: str,
        filename: str = "",
        content: str = "",
        chunks_count: int = 0,
        is_public: bool = False,
        allowed_roles: list[str] | None = None,
        allowed_departments: list[str] | None = None,
        version: int = 1,
    ) -> bool:
        """Синхронизирует документ и его связи в графе Memgraph."""
        if not self.enabled:
            return False

        def _sync():
            driver = self._get_driver()
            if not driver:
                return False

            rels = self.extract_relations(title, content)
            matched_regs = rels["regulations"]
            matched_systems = rels["systems"]
            matched_depts = rels["departments"]

            try:
                with driver.session() as s:
                    # 1. Создаем или обновляем Document
                    s.run(
                        """
                        MERGE (d:Document {id: $doc_id})
                        SET d.title = $title,
                            d.filename = $filename,
                            d.version = $version,
                            d.chunks_count = $chunks_count,
                            d.is_public = $is_public,
                            d.is_active = true,
                            d.updated_at = timestamp();
                        """,
                        doc_id=doc_id,
                        title=title,
                        filename=filename,
                        version=version,
                        chunks_count=chunks_count,
                        is_public=is_public,
                    )

                    # 2. Связываем с найденными регламентами (Regulation)
                    for r in matched_regs:
                        s.run(
                            """
                            MERGE (reg:Regulation {code: $code})
                            ON CREATE SET reg.title = $title, reg.scope = 'Автоматически извлечено'
                            WITH reg
                            MATCH (d:Document {id: $doc_id})
                            MERGE (d)-[:COMPLIES_WITH]->(reg);
                            """,
                            code=r["code"],
                            title=r["title"],
                            doc_id=doc_id,
                        )

                    # 3. Связываем с информационными системами (System)
                    for sys_name in matched_systems:
                        s.run(
                            """
                            MATCH (d:Document {id: $doc_id})
                            MATCH (sys:System {name: $sys_name})
                            MERGE (d)-[:DESCRIBES]->(sys);
                            """,
                            doc_id=doc_id,
                            sys_name=sys_name,
                        )

                    # 4. Связываем с отделами (Department)
                    for dept_code in matched_depts:
                        s.run(
                            """
                            MATCH (d:Document {id: $doc_id})
                            MATCH (dep:Department {code: $dept_code})
                            MERGE (dep)-[:OWNS]->(d);
                            """,
                            doc_id=doc_id,
                            dept_code=dept_code,
                        )

                logger.info(
                    "Граф Memgraph успешно обновлен для документа '%s' (id: %s): %d регламентов, %d систем, %d отделов",
                    title,
                    doc_id,
                    len(matched_regs),
                    len(matched_systems),
                    len(matched_depts),
                )
                return True
            except Exception as e:
                logger.warning("Ошибка обновления графа Memgraph для '%s' (%s): %s", title, doc_id, e)
                return False

        return await anyio.to_thread.run_sync(_sync)

    async def delete_document(self, doc_id: str) -> bool:
        """Удаляет узел документа и его связи из Memgraph."""
        if not self.enabled:
            return False

        def _del():
            driver = self._get_driver()
            if not driver:
                return False
            try:
                with driver.session() as s:
                    s.run("MATCH (d:Document {id: $doc_id}) DETACH DELETE d;", doc_id=doc_id)
                logger.info("Документ %s удален из графа Memgraph", doc_id)
                return True
            except Exception as e:
                logger.warning("Ошибка удаления документа %s из Memgraph: %s", doc_id, e)
                return False

        return await anyio.to_thread.run_sync(_del)

    async def set_active(self, doc_id: str, is_active: bool) -> bool:
        """Переключает статус активности версии документа в графе."""
        if not self.enabled:
            return False

        def _set():
            driver = self._get_driver()
            if not driver:
                return False
            try:
                with driver.session() as s:
                    s.run("MATCH (d:Document {id: $doc_id}) SET d.is_active = $active;", doc_id=doc_id, active=is_active)
                return True
            except Exception as e:
                logger.warning("Ошибка обновления статуса активности документа %s в Memgraph: %s", doc_id, e)
                return False

        return await anyio.to_thread.run_sync(_set)
