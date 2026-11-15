# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "neo4j>=6.4.1",
# ]
# ///
"""Скрипт инициализации новой структуры графа знаний аптечных складов в Memgraph.

Создает:
1. Ограничения уникальности и индексы Memgraph.
2. Ядро онтологии: организации, отделы, роли, системы, режимы хранения, локации,
   категории препаратов, нормативные акты (GDP, МДЛП, ФЗ-61) и базовые процессы.
"""

from __future__ import annotations

import logging
from neo4j import GraphDatabase

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("init_graph")


CONSTRAINTS = [
    ("Department", "code"),
    ("Product", "id"),
    ("Batch", "id"),
    ("Document", "id"),
    ("Location", "code"),
    ("System", "name"),
    ("DataEntity", "name"),
    ("Regulation", "code"),
    ("Abbreviation", "name"),
]

INDEXES = [
    ("Process", "name"),
    ("Step", "order"),
    ("Role", "name"),
    ("Employee", "name"),
    ("StorageCondition", "name"),
    ("Category", "name"),
    ("Module", "name"),
    ("Supplier", "name"),
    ("Abbreviation", "full_name"),
    ("Abbreviation", "short_form"),
]


def exec_queries(session, queries: list[str]):
    for q in queries:
        clean = q.strip().rstrip(";")
        if clean:
            session.run(clean)


def setup_schema(uri: str = "bolt://127.0.0.1:7687"):
    driver = GraphDatabase.driver(uri, auth=("", ""))
    with driver.session() as s:
        logger.info("--- 1. Создание ограничений уникальности ---")
        for label, prop in CONSTRAINTS:
            try:
                s.run(f"CREATE CONSTRAINT ON (n:{label}) ASSERT n.{prop} IS UNIQUE;")
                logger.info(f"  [OK] Констрейнт: ({label}).{prop} IS UNIQUE")
            except Exception as e:
                msg = str(e)
                if "already exists" in msg.lower() or "exists" in msg.lower():
                    logger.info(f"  [EXISTS] ({label}).{prop}")
                else:
                    logger.warning(f"  [ERR] ({label}).{prop}: {e}")

        logger.info("--- 2. Создание поисковых индексов ---")
        for label, prop in INDEXES:
            try:
                s.run(f"CREATE INDEX ON :{label}({prop});")
                logger.info(f"  [OK] Индекс: :{label}({prop})")
            except Exception as e:
                logger.info(f"  [INFO] :{label}({prop}) - {e}")

        logger.info("--- 3. Загрузка ядра онтологии аптечного склада ---")

        # 3.1 Организация и отделы
        exec_queries(s, [
            "MERGE (org:Organization {name: 'ГАУЗ «Оренбургские областные аптеки» (ГАУЗ «ОАС»)'}) SET org.inn = '5610001234'",
            "MERGE (d_wh:Department {code: 'WH'}) SET d_wh.name = 'Складской комплекс'",
            "MERGE (d_qa:Department {code: 'QA'}) SET d_qa.name = 'Отдел обеспечения качества'",
            "MERGE (d_proc:Department {code: 'PROC'}) SET d_proc.name = 'Отдел закупок'",
            "MERGE (d_log:Department {code: 'LOG'}) SET d_log.name = 'Транспортная логистика'",
            "MERGE (d_acc:Department {code: 'ACC'}) SET d_acc.name = 'Бухгалтерия и финансовый учет'",
            "MERGE (d_it:Department {code: 'IT'}) SET d_it.name = 'ИТ и автоматизация'",
        ])

        # 3.2 Роли сотрудников
        exec_queries(s, [
            "MERGE (r1:Role {name: 'Заведующий складом'}) SET r1.code = 'WH_HEAD'",
            "MERGE (r2:Role {name: 'Кладовщик приёмки'}) SET r2.code = 'WH_RECEIVER'",
            "MERGE (r3:Role {name: 'Кладовщик отбора'}) SET r3.code = 'WH_PICKER'",
            "MERGE (r4:Role {name: 'Уполномоченное лицо по качеству (GDP)'}) SET r4.code = 'QA_RESPONSIBLE'",
            "MERGE (r5:Role {name: 'Фармацевт-контролёр'}) SET r5.code = 'QA_CONTROLLER'",
            "MERGE (r6:Role {name: 'Системный администратор WMS'}) SET r6.code = 'IT_ADMIN'",
            """
            MATCH (d:Department {code: 'WH'}), (r:Role {name: 'Заведующий складом'})
            MERGE (d)-[:USES]->(r)
            """,
        ])

        # 3.3 Нормативная документация (Regulation)
        exec_queries(s, [
            """
            MERGE (gdp:Regulation {code: 'GDP'})
            SET gdp.title = 'Правила надлежащей дистрибьюторской практики ЕАЭС',
                gdp.source = 'Решение Совета ЕЭК № 80',
                gdp.edition = '2023'
            """,
            """
            MERGE (fz61:Regulation {code: 'FZ-61'})
            SET fz61.title = 'Об обращении лекарственных средств',
                fz61.source = 'Федеральный закон № 61-ФЗ',
                fz61.edition = '2024'
            """,
            """
            MERGE (mdlp_reg:Regulation {code: 'PP-1556'})
            SET mdlp_reg.title = 'Положение о системе мониторинга движения лекарственных препаратов (МДЛП)',
                mdlp_reg.source = 'Постановление Правительства РФ № 1556',
                mdlp_reg.edition = '2024'
            """,
            """
            MERGE (cold_reg:Regulation {code: 'SP-3.3.2'})
            SET cold_reg.title = 'Условия транспортирования и хранения иммунобиологических лекарственных препаратов',
                cold_reg.source = 'СП 3.3.2.3332-16',
                cold_reg.edition = '2020'
            """,
        ])

        # 3.4 Информационные системы и интеграции
        exec_queries(s, [
            "MERGE (erp:System {name: '1С:ERP Предприятие'}) SET erp.type = 'ERP', erp.vendor = '1С', erp.version = '2.5', erp.criticality = 'High'",
            "MERGE (wms:System {name: 'WMS'}) SET wms.type = 'WMS', wms.vendor = 'WMS', wms.version = '3.0', wms.criticality = 'Critical'",
            "MERGE (mdlp:System {name: 'ИС «МДЛП»'}) SET mdlp.type = 'GovReporting', mdlp.vendor = 'ЦРПТ', mdlp.criticality = 'Critical'",
            "MERGE (mon:System {name: 'Система термомониторинга'}) SET mon.type = 'Monitoring', mon.vendor = 'TermoSensor', mon.criticality = 'High'",
            "MERGE (edo:System {name: 'СЭД Диадок / Директум'}) SET edo.type = 'EDO', edo.vendor = 'Контур/Директум', edo.criticality = 'Medium'",
            """
            MATCH (wms:System {name: 'WMS'}), (erp:System {name: '1С:ERP Предприятие'})
            MERGE (wms)-[:INTEGRATES_WITH]->(erp)
            """,
            """
            MATCH (wms:System {name: 'WMS'}), (mdlp:System {name: 'ИС «МДЛП»'})
            MERGE (wms)-[:INTEGRATES_WITH]->(mdlp)
            """,
            """
            MATCH (d:Department {code: 'WH'}), (w:System {name: 'WMS'})
            MERGE (d)-[:USES_SYSTEM {frequency: 'continuous'}]->(w)
            MERGE (d)-[:OWNS_SYSTEM]->(w)
            """,
            """
            MATCH (d:Department {code: 'QA'}), (m:System {name: 'Система термомониторинга'})
            MERGE (d)-[:USES_SYSTEM {frequency: 'continuous'}]->(m)
            """,
        ])

        # 3.5 Сущности данных (DataEntity) и мастер-системы
        exec_queries(s, [
            "MERGE (e_nom:DataEntity {name: 'Номенклатурный справочник препаратов'})",
            "MERGE (e_batch:DataEntity {name: 'Остатки и статусы серий'})",
            "MERGE (e_pack:DataEntity {name: 'КМ DataMatrix упаковок'})",
            "MERGE (e_loc:DataEntity {name: 'Топология склада и ячеек'})",
            "MERGE (e_temp:DataEntity {name: 'Журнал температурных измерений'})",
            """
            MATCH (erp:System {name: '1С:ERP Предприятие'}), (e_nom:DataEntity {name: 'Номенклатурный справочник препаратов'})
            MERGE (erp)-[:SOURCE_OF_TRUTH_FOR]->(e_nom)
            """,
            """
            MATCH (wms:System {name: 'WMS'}), (e_batch:DataEntity {name: 'Остатки и статусы серий'})
            MERGE (wms)-[:SOURCE_OF_TRUTH_FOR]->(e_batch)
            """,
            """
            MATCH (wms:System {name: 'WMS'}), (e_loc:DataEntity {name: 'Топология склада и ячеек'})
            MERGE (wms)-[:SOURCE_OF_TRUTH_FOR]->(e_loc)
            """,
            """
            MATCH (mdlp:System {name: 'ИС «МДЛП»'}), (e_pack:DataEntity {name: 'КМ DataMatrix упаковок'})
            MERGE (mdlp)-[:SOURCE_OF_TRUTH_FOR]->(e_pack)
            """,
            """
            MATCH (mon:System {name: 'Система термомониторинга'}), (e_temp:DataEntity {name: 'Журнал температурных измерений'})
            MERGE (mon)-[:SOURCE_OF_TRUTH_FOR]->(e_temp)
            """,
        ])

        # 3.6 Режимы хранения (StorageCondition)
        exec_queries(s, [
            "MERGE (sc1:StorageCondition {name: 'Холодовая цепь 2–8 °C'}) SET sc1.temp_min = 2.0, sc1.temp_max = 8.0, sc1.light_protected = true",
            "MERGE (sc2:StorageCondition {name: 'Умеренный режим 15–25 °C'}) SET sc2.temp_min = 15.0, sc2.temp_max = 25.0, sc2.humidity_max = 60.0",
            "MERGE (sc3:StorageCondition {name: 'Сухое темное место 8–15 °C'}) SET sc3.temp_min = 8.0, sc3.temp_max = 15.0, sc3.light_protected = true",
            "MERGE (sc4:StorageCondition {name: 'Глубокая заморозка <-20 °C'}) SET sc4.temp_min = -40.0, sc4.temp_max = -20.0",
        ])

        # 3.7 Категории товаров (Category)
        exec_queries(s, [
            "MERGE (c1:Category {name: 'ЖНВЛП'})",
            "MERGE (c2:Category {name: 'НС и ПВ (Наркотические и психотропные)'})",
            "MERGE (c3:Category {name: 'Прекурсоры'})",
            "MERGE (c4:Category {name: 'Термолабильные препараты'})",
            "MERGE (c5:Category {name: 'Иммунобиологические (ИЛП)'})",
            "MERGE (c6:Category {name: 'Маркированные ЛП (Честный Знак)'})",
            """
            MATCH (c4:Category {name: 'Термолабильные препараты'}), (r:Regulation {code: 'SP-3.3.2'})
            MERGE (c4)-[:COMPLIES_WITH]->(r)
            """,
            """
            MATCH (c6:Category {name: 'Маркированные ЛП (Честный Знак)'}), (r:Regulation {code: 'PP-1556'})
            MERGE (c6)-[:COMPLIES_WITH]->(r)
            """,
        ])

        # 3.8 Складские локации (Location)
        exec_queries(s, [
            "MERGE (loc_rec:Location {code: 'ZONE-REC-01'}) SET loc_rec.type = 'зона приёмки', loc_rec.temp_min = 15.0, loc_rec.temp_max = 25.0",
            "MERGE (loc_cold:Location {code: 'CHAMBER-COLD-01'}) SET loc_cold.type = 'холодильная камера', loc_cold.temp_min = 2.0, loc_cold.temp_max = 8.0",
            "MERGE (loc_shelf:Location {code: 'RACK-DRY-A1'}) SET loc_shelf.type = 'стеллаж основного хранения', loc_shelf.temp_min = 15.0, loc_shelf.temp_max = 25.0",
            "MERGE (loc_quar:Location {code: 'ZONE-QUAR-01'}) SET loc_quar.type = 'зона карантинного хранения', loc_quar.temp_min = 15.0, loc_quar.temp_max = 25.0",
            """
            MATCH (loc_cold:Location {code: 'CHAMBER-COLD-01'}), (sc:StorageCondition {name: 'Холодовая цепь 2–8 °C'})
            MERGE (loc_cold)-[:PROVIDES]->(sc)
            """,
            """
            MATCH (loc_shelf:Location {code: 'RACK-DRY-A1'}), (sc:StorageCondition {name: 'Умеренный режим 15–25 °C'})
            MERGE (loc_shelf)-[:PROVIDES]->(sc)
            """,
            """
            MATCH (loc_rec:Location {code: 'ZONE-REC-01'}), (sc:StorageCondition {name: 'Умеренный режим 15–25 °C'})
            MERGE (loc_rec)-[:PROVIDES]->(sc)
            """,
        ])

        # 3.9 Базовые процессы (Process) и шаги (Step)
        exec_queries(s, [
            "MERGE (p_rec:Process {name: 'Приёмка лекарственных препаратов'}) SET p_rec.version = '2.1', p_rec.owner = 'WH'",
            "MERGE (s1:Step {order: 1, description: 'Проверка сопроводительных документов, лицензии поставщика и паспорта серии'}) SET s1.name = 'Входной документарный контроль'",
            "MERGE (s2:Step {order: 2, description: 'Считывание данных термоиндикаторов и термологгеров из термоконтейнеров'}) SET s2.name = 'Контроль соблюдения холодовой цепи при доставке'",
            "MERGE (s3:Step {order: 3, description: 'Сканирование кодов агрегации и DataMatrix потребительских упаковок через ТСД в WMS'}) SET s3.name = 'Поэкземплярное сканирование маркировки МДЛП'",
            "MERGE (s4:Step {order: 4, description: 'Размещение серии в зону приемки или карантина до подтверждения приёмки в МДЛП'}) SET s4.name = 'Перемещение на адресное хранение'",
            """
            MATCH (p_rec:Process {name: 'Приёмка лекарственных препаратов'}), (s1:Step {order: 1})
            MERGE (p_rec)-[:HAS_STEP]->(s1)
            """,
            """
            MATCH (p_rec:Process {name: 'Приёмка лекарственных препаратов'}), (s2:Step {order: 2})
            MERGE (p_rec)-[:HAS_STEP]->(s2)
            """,
            """
            MATCH (p_rec:Process {name: 'Приёмка лекарственных препаратов'}), (s3:Step {order: 3})
            MERGE (p_rec)-[:HAS_STEP]->(s3)
            """,
            """
            MATCH (p_rec:Process {name: 'Приёмка лекарственных препаратов'}), (s4:Step {order: 4})
            MERGE (p_rec)-[:HAS_STEP]->(s4)
            """,
            """
            MATCH (s1:Step {order: 1}), (s2:Step {order: 2})
            MERGE (s1)-[:NEXT]->(s2)
            """,
            """
            MATCH (s2:Step {order: 2}), (s3:Step {order: 3})
            MERGE (s2)-[:NEXT]->(s3)
            """,
            """
            MATCH (s3:Step {order: 3}), (s4:Step {order: 4})
            MERGE (s3)-[:NEXT]->(s4)
            """,
            """
            MATCH (r_qa:Role {name: 'Фармацевт-контролёр'}), (s1:Step {order: 1})
            MERGE (r_qa)-[:RESPONSIBLE_FOR]->(s1)
            """,
            """
            MATCH (r_rec:Role {name: 'Кладовщик приёмки'}), (s3:Step {order: 3})
            MERGE (r_rec)-[:RESPONSIBLE_FOR]->(s3)
            """,
            """
            MATCH (s3:Step {order: 3}), (wms:System {name: 'WMS'})
            MERGE (s3)-[:EXECUTED_IN]->(wms)
            """,
            """
            MATCH (p_rec:Process {name: 'Приёмка лекарственных препаратов'}), (gdp:Regulation {code: 'GDP'})
            MERGE (p_rec)-[:COMPLIES_WITH]->(gdp)
            """,
        ])

        # 3.10 Демонстрационный товар, серия и упаковка для проверки режимов
        exec_queries(s, [
            """
            MERGE (p1:Product {id: 'PROD-001'})
            SET p1.name = 'Вакцина Гам-КОВИД-Вак (Спутник V)',
                p1.mnn = 'Вакцина для профилактики COVID-19',
                p1.form = 'раствор для инъекций',
                p1.dosage = '0.5 мл'
            """,
            """
            MATCH (p1:Product {id: 'PROD-001'}), (sc:StorageCondition {name: 'Холодовая цепь 2–8 °C'})
            MERGE (p1)-[:REQUIRES]->(sc)
            """,
            """
            MATCH (p1:Product {id: 'PROD-001'}), (c:Category {name: 'Термолабильные препараты'})
            MERGE (p1)-[:BELONGS_TO]->(c)
            """,
            """
            MATCH (p1:Product {id: 'PROD-001'}), (c:Category {name: 'Иммунобиологические (ИЛП)'})
            MERGE (p1)-[:BELONGS_TO]->(c)
            """,
            """
            MERGE (b1:Batch {id: 'BATCH-2026-001'})
            SET b1.number = 'SERIA-2026-01',
                b1.expiry_date = '2026-12-31',
                b1.status = 'годен'
            """,
            """
            MATCH (b1:Batch {id: 'BATCH-2026-001'}), (p1:Product {id: 'PROD-001'})
            MERGE (b1)-[:OF_PRODUCT]->(p1)
            """,
            """
            MATCH (b1:Batch {id: 'BATCH-2026-001'}), (loc:Location {code: 'CHAMBER-COLD-01'})
            MERGE (b1)-[:STORED_IN]->(loc)
            """,
        ])

        # 3.11 Ядро аббревиатур и сокращений (Core Abbreviations)
        logger.info("--- 3.11 Загрузка ядра аббревиатур и терминов ---")
        exec_queries(s, [
            # 1. ИСЛО
            """
            MERGE (a:Abbreviation {name: 'ИСЛО'})
            SET a.full_name = 'Информационная система «Лекарственное обеспечение»',
                a.short_form = 'ИС ЛО',
                a.category = 'Информационные системы',
                a.description = 'Региональная медицинская и фармацевтическая система выписки электронных рецептов и учета льготного лекарственного обеспечения'
            """,
            """
            MATCH (a:Abbreviation {name: 'ИСЛО'}), (sys:System {name: 'ИС «Лекарственное обеспечение»'})
            MERGE (a)-[:EXPANDS_TO]->(sys)
            """,
            """
            MATCH (wms:System {name: 'WMS'}), (islo:System {name: 'ИС «Лекарственное обеспечение»'})
            MERGE (wms)-[:INTEGRATES_WITH]->(islo)
            """,
            # 2. МДЛП
            """
            MERGE (a:Abbreviation {name: 'МДЛП'})
            SET a.full_name = 'Мониторинг движения лекарственных препаратов',
                a.short_form = 'ИС МДЛП',
                a.category = 'Госсистемы и маркировка',
                a.description = 'Федеральная государственная информационная система прослеживания лекарственных средств от производителя до потребителя'
            """,
            """
            MATCH (a:Abbreviation {name: 'МДЛП'}), (sys:System {name: 'ИС «МДЛП»'})
            MERGE (a)-[:EXPANDS_TO]->(sys)
            """,
            """
            MATCH (a:Abbreviation {name: 'МДЛП'}), (reg:Regulation {code: 'PP-1556'})
            MERGE (a)-[:REFERS_TO]->(reg)
            """,
            # 3. GDP
            """
            MERGE (a:Abbreviation {name: 'GDP'})
            SET a.full_name = 'Good Distribution Practice (Надлежащая дистрибьюторская практика)',
                a.short_form = 'НАДП',
                a.category = 'Нормативная база и качество',
                a.description = 'Международный стандарт правил надлежащей дистрибьюторской практики ЕАЭС в сфере обращения лекарственных средств'
            """,
            """
            MATCH (a:Abbreviation {name: 'GDP'}), (reg:Regulation {code: 'GDP'})
            MERGE (a)-[:EXPANDS_TO]->(reg)
            """,
            # 4. ЖНВЛП
            """
            MERGE (a:Abbreviation {name: 'ЖНВЛП'})
            SET a.full_name = 'Жизненно необходимые и важнейшие лекарственные препараты',
                a.short_form = 'ЖНВЛС',
                a.category = 'Категории препаратов',
                a.description = 'Перечень медикаментов, утверждаемый Правительством РФ для регулирования цен и гарантированного лекобеспечения'
            """,
            """
            MATCH (a:Abbreviation {name: 'ЖНВЛП'}), (cat:Category {name: 'ЖНВЛП'})
            MERGE (a)-[:EXPANDS_TO]->(cat)
            """,
            # 5. НС и ПВ
            """
            MERGE (a:Abbreviation {name: 'НС и ПВ'})
            SET a.full_name = 'Наркотические средства и психотропные вещества',
                a.short_form = 'НС/ПВ',
                a.category = 'Категории препаратов',
                a.description = 'Препараты предметно-количественного учета, требующие хранения в сейфах 3-4 класса устойчивости и особого контроля доступа'
            """,
            """
            MATCH (a:Abbreviation {name: 'НС и ПВ'}), (cat:Category {name: 'НС и ПВ (Наркотические и психотропные)'})
            MERGE (a)-[:EXPANDS_TO]->(cat)
            """,
            # 6. WMS
            """
            MERGE (a:Abbreviation {name: 'WMS'})
            SET a.full_name = 'Warehouse Management System',
                a.short_form = 'СУС',
                a.category = 'Информационные системы',
                a.description = 'Система управления складом: адресное хранение, топология, приёмка, размещение, комплектация и инвентаризация'
            """,
            """
            MATCH (a:Abbreviation {name: 'WMS'}), (sys:System {name: 'WMS'})
            MERGE (a)-[:EXPANDS_TO]->(sys)
            """,
            # 7. ERP
            """
            MERGE (a:Abbreviation {name: 'ERP'})
            SET a.full_name = 'Enterprise Resource Planning',
                a.short_form = 'ERP',
                a.category = 'Информационные системы',
                a.description = 'Система планирования ресурсов предприятия, взаиморасчетов, бухгалтерского и управленческого учета'
            """,
            """
            MATCH (a:Abbreviation {name: 'ERP'}), (sys:System {name: '1С:ERP Предприятие'})
            MERGE (a)-[:EXPANDS_TO]->(sys)
            """,
            # 8. TMS
            """
            MERGE (a:Abbreviation {name: 'TMS'})
            SET a.full_name = 'Transportation Management System',
                a.short_form = 'СУТ',
                a.category = 'Информационные системы',
                a.description = 'Система управления транспортной логистикой, планированием рейсов и доставкой термолабильных грузов'
            """,
            """
            MATCH (a:Abbreviation {name: 'TMS'}), (dep:Department {code: 'LOG'})
            MERGE (a)-[:REFERS_TO]->(dep)
            """,
            # 9. QMS
            """
            MERGE (a:Abbreviation {name: 'QMS'})
            SET a.full_name = 'Quality Management System',
                a.short_form = 'СМК',
                a.category = 'Нормативная база и качество',
                a.description = 'Система менеджмента качества: регламенты, контроль отклонений, валидация процессов и аудит'
            """,
            """
            MATCH (a:Abbreviation {name: 'QMS'}), (dep:Department {code: 'QA'})
            MERGE (a)-[:REFERS_TO]->(dep)
            """,
            # 10. КМ
            """
            MERGE (a:Abbreviation {name: 'КМ'})
            SET a.full_name = 'Код маркировки',
                a.short_form = 'DataMatrix',
                a.category = 'Складской учет',
                a.description = 'Уникальный двумерный матричный код (DataMatrix), наносимый на вторичную упаковку лекарственного препарата'
            """,
            """
            MATCH (a:Abbreviation {name: 'КМ'}), (de:DataEntity {name: 'КМ DataMatrix упаковок'})
            MERGE (a)-[:REFERS_TO]->(de)
            """,
            # 11. СОП
            """
            MERGE (a:Abbreviation {name: 'СОП'})
            SET a.full_name = 'Стандартная операционная процедура',
                a.short_form = 'SOP',
                a.category = 'Процессы и регламенты',
                a.description = 'Документально утвержденная пошаговая инструкция по выполнению конкретной технологической операции на складе'
            """,
            """
            MATCH (a:Abbreviation {name: 'СОП'}), (reg:Regulation {code: 'GDP'})
            MERGE (a)-[:REFERS_TO]->(reg)
            """,
            # 12. ИЛП
            """
            MERGE (a:Abbreviation {name: 'ИЛП'})
            SET a.full_name = 'Иммунобиологические лекарственные препараты',
                a.short_form = 'ИЛП',
                a.category = 'Категории препаратов',
                a.description = 'Вакцины, анатоксины, токсины, сыворотки, требующие непрерывной холодовой цепи (2-8 °C)'
            """,
            """
            MATCH (a:Abbreviation {name: 'ИЛП'}), (cat:Category {name: 'Иммунобиологические (ИЛП)'})
            MERGE (a)-[:EXPANDS_TO]->(cat)
            """,
            """
            MATCH (a:Abbreviation {name: 'ИЛП'}), (reg:Regulation {code: 'SP-3.3.2'})
            MERGE (a)-[:REFERS_TO]->(reg)
            """,
            # 13. СЭД
            """
            MERGE (a:Abbreviation {name: 'СЭД'})
            SET a.full_name = 'Система электронного документооборота',
                a.short_form = 'ЭДО',
                a.category = 'Информационные системы',
                a.description = 'Сервис юридически значимого электронного обмена накладными (УПД), счетами-фактурами и сертификатами'
            """,
            """
            MATCH (a:Abbreviation {name: 'СЭД'}), (sys:System {name: 'СЭД Диадок / Директум'})
            MERGE (a)-[:EXPANDS_TO]->(sys)
            """,
            # 14. СНИЛС
            """
            MERGE (a:Abbreviation {name: 'СНИЛС'})
            SET a.full_name = 'Страховой номер индивидуального лицевого счёта',
                a.short_form = 'СНИЛС',
                a.category = 'Документы и данные',
                a.description = 'Уникальный номер лицевого счёта гражданина в системе пенсионного и социального страхования РФ'
            """,
            # 15. ФЗ-61
            """
            MERGE (a:Abbreviation {name: 'ФЗ-61'})
            SET a.full_name = 'Федеральный закон «Об обращении лекарственных средств» № 61-ФЗ',
                a.short_form = '61-ФЗ',
                a.category = 'Нормативная база и качество',
                a.description = 'Основной законодательный акт, регулирующий сферу обращения лекарственных средств на территории РФ'
            """,
            """
            MATCH (a:Abbreviation {name: 'ФЗ-61'}), (reg:Regulation {code: 'FZ-61'})
            MERGE (a)-[:EXPANDS_TO]->(reg)
            """,
            # 16. ЦРПТ
            """
            MERGE (a:Abbreviation {name: 'ЦРПТ'})
            SET a.full_name = 'Центр развития перспективных технологий',
                a.short_form = 'Оператор ЦРПТ',
                a.category = 'Организации',
                a.description = 'Единый национальный оператор государственной системы цифровой маркировки «Честный Знак»'
            """,
            """
            MATCH (a:Abbreviation {name: 'ЦРПТ'}), (sys:System {name: 'ИС «МДЛП»'})
            MERGE (a)-[:REFERS_TO]->(sys)
            """,
            # 17. МНН
            """
            MERGE (a:Abbreviation {name: 'МНН'})
            SET a.full_name = 'Международное непатентованное наименование',
                a.short_form = 'INN',
                a.category = 'Складской учет',
                a.description = 'Уникальное глобальное наименование действующего фармацевтического вещества, рекомендованное ВОЗ'
            """,
            # 18. ТМЦ
            """
            MERGE (a:Abbreviation {name: 'ТМЦ'})
            SET a.full_name = 'Товарно-материальные ценности',
                a.short_form = 'ТМЦ',
                a.category = 'Складской учет',
                a.description = 'Складские активы: лекарственные средства, медизделия, сырьё, тара и упаковочные материалы'
            """,
            # 19. ПКУ
            """
            MERGE (a:Abbreviation {name: 'ПКУ'})
            SET a.full_name = 'Предметно-количественный учет',
                a.short_form = 'ПКУ',
                a.category = 'Нормативная база и качество',
                a.description = 'Специальный строгий режим регистрации движения наркотических, психотропных, сильнодействующих и ядовитых ЛП'
            """,
            """
            MATCH (a:Abbreviation {name: 'ПКУ'}), (cat:Category {name: 'НС и ПВ (Наркотические и психотропные)'})
            MERGE (a)-[:REFERS_TO]->(cat)
            """,
            # 20. ГЛФ
            """
            MERGE (a:Abbreviation {name: 'ГЛФ'})
            SET a.full_name = 'Готовые лекарственные формы',
                a.short_form = 'ГЛФ',
                a.category = 'Складской учет',
                a.description = 'Лекарственные препараты в готовом для клинического применения виде (таблетки, ампулы, капсулы, флаконы)'
            """,
        ])

        logger.info("--- Базовая онтология успешно загружена в Memgraph ---")

    driver.close()


if __name__ == "__main__":
    setup_schema()
