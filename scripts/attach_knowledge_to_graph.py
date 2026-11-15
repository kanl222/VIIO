"""Скрипт прикрепления данных к графу знаний Memgraph (Semantic GraphRAG).

Добавляет и связывает:
1. Реальные организации: ГАУЗ «ОАС» (ГосАптека 56) и Минздрав Оренбургской области
2. Все структурные подразделения и отделы (Department)
3. Должностных лиц и сотрудников (Employee) с контактами, телефонами и доб. номерами
4. Нормативно-правовые акты и законы (Regulation: ФЗ, Постановления, Приказы, Распоряжения)
5. Информационные системы (System: ИС ЛО, ЕГИСЗ, МДЛП, Карта Оренбуржца)
6. Новые документы базы знаний (Document № 70-78)
7. Бизнес-процессы (Process) и ключевые шаги (Step)
8. Связи (WORKS_IN, HAS_ROLE, OWNS, DESCRIBES, COMPLIES_WITH, USES_SYSTEM, RESPONSIBLE_FOR, REGULATES, OPERATOR_OF)
"""

import os
import logging
from neo4j import GraphDatabase

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("graph_attacher")

MEMGRAPH_URI = os.getenv("MEMGRAPH_URI", "bolt://memgraph:7687")
MEMGRAPH_USER = os.getenv("MEMGRAPH_USER", "")
MEMGRAPH_PASSWORD = os.getenv("MEMGRAPH_PASSWORD", "")


def attach_knowledge_to_graph():
    driver = GraphDatabase.driver(MEMGRAPH_URI, auth=(MEMGRAPH_USER, MEMGRAPH_PASSWORD))
    
    with driver.session() as s:
        logger.info("Подключение к Memgraph успешно. Начинаем прикрепление сущностей к графу...")

        # -------------------------------------------------------------------------
        # 1. Организации (Organization)
        # -------------------------------------------------------------------------
        logger.info("1. Создание/обновление организаций (Organization)...")
        queries_org = [
            """
            MERGE (org:Organization {name: 'ГАУЗ «ОАС»'})
            SET org.full_name = 'Государственное автономное учреждение здравоохранения «Областной аптечный склад»',
                org.short_name = 'ГосАптека 56',
                org.inn = '5610014238',
                org.ogrn = '1025601026040',
                org.address = '460027, г. Оренбург, ул. Берёзка, д. 24',
                org.phone = '+7 (3532) 507-507',
                org.email = 'office@oas56.ru',
                org.website = 'https://gosapteka56.ru';
            """,
            """
            MERGE (mz:Organization {name: 'Министерство здравоохранения Оренбургской области'})
            SET mz.short_name = 'Минздрав Оренбургской области',
                mz.address = '460006, г. Оренбург, ул. Терешковой, д. 33',
                mz.phone = '+7 (3532) 44-88-88',
                mz.hotline = '122 (доб. 3)',
                mz.email = 'minzdrav@mail.orb.ru',
                mz.website = 'https://minzdrav.orb.ru';
            """,
            """
            MATCH (mz:Organization {name: 'Министерство здравоохранения Оренбургской области'}),
                  (oas:Organization {name: 'ГАУЗ «ОАС»'})
            MERGE (mz)-[:REGULATES]->(oas);
            """
        ]
        for q in queries_org:
            s.run(q)

        # -------------------------------------------------------------------------
        # 2. Отделы ГАУЗ «ОАС» и Минздрава ОО (Department)
        # -------------------------------------------------------------------------
        logger.info("2. Создание отделов (Department)...")
        queries_dept = [
            # Отделы ГАУЗ «ОАС»
            """
            MERGE (d:Department {code: 'OAS_MANAGEMENT'})
            SET d.name = 'Руководство ГАУЗ «ОАС»',
                d.phone = '+7 (3532) 507-507';
            """,
            """
            MERGE (d:Department {code: 'OAS_OSMP'})
            SET d.name = 'Отдел по сопровождению медицинских программ (ОСМП)',
                d.description = 'Техническая и методическая поддержка пользователей ИС ЛО в медицинских и аптечных организациях',
                d.phone = '+7 (3532) 507-507 доб. 288, 297, 298',
                d.email = 'islo@oas56.ru';
            """,
            """
            MERGE (d:Department {code: 'OAS_IT'})
            SET d.name = 'Отдел информационных технологий и автоматизации (ОИТ)',
                d.phone = '+7 (3532) 507-507 доб. 287',
                d.email = 'it@oas56.ru';
            """,
            """
            MERGE (d:Department {code: 'OAS_WAREHOUSE'})
            SET d.name = 'Аптечный складской комплекс ГАУЗ «ОАС»',
                d.phone = '+7 (3532) 507-507 доб. 888',
                d.address = 'г. Оренбург, ул. Берёзка, д. 24';
            """,
            """
            MERGE (d:Department {code: 'OAS_PROCUREMENT'})
            SET d.name = 'Отдел льготного лекарственного обеспечения и закупок ГАУЗ «ОАС»',
                d.phone = '+7 (3532) 507-507 доб. 151';
            """,
            """
            MERGE (d:Department {code: 'OAS_QA'})
            SET d.name = 'Отдел обеспечения качества и фармацевтического контроля',
                d.phone = '+7 (3532) 507-507 доб. 278';
            """,
            """
            MERGE (d:Department {code: 'OAS_PHARMACY_NETWORK'})
            SET d.name = 'Аптечная розничная сеть «ГосАптека 56»',
                d.description = 'Сеть пунктов льготного и розничного отпуска лекарственных препаратов по Оренбургской области',
                d.phone = '+7 (3532) 507-507';
            """,
            # Связь отделов с организацией ГАУЗ «ОАС»
            """
            MATCH (org:Organization {name: 'ГАУЗ «ОАС»'})
            MATCH (d:Department) WHERE d.code STARTS WITH 'OAS_'
            MERGE (org)-[:OWNS]->(d);
            """,
            # Отделы Минздрава ОО
            """
            MERGE (d:Department {code: 'MZ_OAS_LEK'})
            SET d.name = 'Отдел организации лекарственного обеспечения МЗ ОО',
                d.phone = '+7 (3532) 44-89-38',
                d.hotline = '122 доб. 3';
            """,
            """
            MERGE (d:Department {code: 'MZ_PROGNOZ'})
            SET d.name = 'Отдел прогнозирования и анализа лекарственного обеспечения МЗ ОО',
                d.phone = '+7 (3532) 44-89-40';
            """,
            # Связь отделов с организацией Минздрав ОО
            """
            MATCH (mz:Organization {name: 'Министерство здравоохранения Оренбургской области'})
            MATCH (d:Department) WHERE d.code STARTS WITH 'MZ_'
            MERGE (mz)-[:OWNS]->(d);
            """
        ]
        for q in queries_dept:
            s.run(q)

        # -------------------------------------------------------------------------
        # 3. Сотрудники, руководство и контакты (Employee)
        # -------------------------------------------------------------------------
        logger.info("3. Создание сотрудников и привязка к отделам и ролям (Employee)...")
        queries_emp = [
            # Руководство ГАУЗ «ОАС»
            """
            MERGE (e:Employee {name: 'Ступин Сергей Михайлович'})
            SET e.position = 'Директор ГАУЗ «ОАС»',
                e.phone = '+7 (3532) 507-507',
                e.org = 'ГАУЗ «ОАС»';
            """,
            """
            MERGE (e:Employee {name: 'Дебус Ирина Александровна'})
            SET e.position = 'Заместитель директора по общим вопросам',
                e.phone = '+7 (3532) 507-507 доб. 111',
                e.org = 'ГАУЗ «ОАС»';
            """,
            """
            MERGE (e:Employee {name: 'Некрасова Ольга Владимировна'})
            SET e.position = 'Главный бухгалтер',
                e.phone = '+7 (3532) 507-507 доб. 200',
                e.org = 'ГАУЗ «ОАС»';
            """,
            """
            MERGE (e:Employee {name: 'Бурасова Светлана Сергеевна'})
            SET e.position = 'Заместитель директора по коммерческим вопросам',
                e.phone = '+7 (3532) 507-507 доб. 151',
                e.org = 'ГАУЗ «ОАС»';
            """,
            """
            MERGE (e:Employee {name: 'Квасов Александр Владимирович'})
            SET e.position = 'Заместитель директора по логистике',
                e.phone = '+7 (3532) 507-507 доб. 255',
                e.org = 'ГАУЗ «ОАС»';
            """,
            """
            MERGE (e:Employee {name: 'Вербина Евгения Андреевна'})
            SET e.position = 'Заместитель директора по качеству',
                e.phone = '+7 (3532) 507-507 доб. 278',
                e.org = 'ГАУЗ «ОАС»';
            """,
            # Начальники подразделений ГАУЗ «ОАС»
            """
            MERGE (e:Employee {name: 'Князев Алексей Николаевич'})
            SET e.position = 'Начальник аптечного склада',
                e.phone = '+7 (3532) 507-507 доб. 888',
                e.org = 'ГАУЗ «ОАС»';
            """,
            """
            MERGE (e:Employee {name: 'Сумкин Евгений Владимирович'})
            SET e.position = 'Начальник отдела информационных технологий и автоматизации',
                e.phone = '+7 (3532) 507-507 доб. 287',
                e.org = 'ГАУЗ «ОАС»';
            """,
            """
            MERGE (e:Employee {name: 'Рагузин Сергей Владимирович'})
            SET e.position = 'Начальник отдела по сопровождению медицинских программ (ОСМП)',
                e.phone = '+7 (3532) 507-507 доб. 288',
                e.support_phones = '+7 (3532) 507-507 доб. 297, 298',
                e.org = 'ГАУЗ «ОАС»';
            """,
            """
            MERGE (e:Employee {name: 'Терентьева Наталья Ивановна'})
            SET e.position = 'Начальник отдела закупок',
                e.phone = '+7 (3532) 507-507',
                e.org = 'ГАУЗ «ОАС»';
            """,
            """
            MERGE (e:Employee {name: 'Балябкина Елена Петровна'})
            SET e.position = 'Начальник отдела кадров',
                e.phone = '+7 (3532) 507-507',
                e.org = 'ГАУЗ «ОАС»';
            """,
            """
            MERGE (e:Employee {name: 'Серова Татьяна Николаевна'})
            SET e.position = 'Начальник юридического отдела',
                e.phone = '+7 (3532) 507-507',
                e.org = 'ГАУЗ «ОАС»';
            """,
            # Сотрудники Минздрава Оренбургской области
            """
            MERGE (e:Employee {name: 'Савинова Татьяна Леонидовна'})
            SET e.position = 'Вице-губернатор – заместитель председателя Правительства ОО по социальной политике – министр здравоохранения',
                e.phone = '+7 (3532) 44-88-88',
                e.org = 'Минздрав Оренбургской области';
            """,
            """
            MERGE (e:Employee {name: 'Какунина Татьяна Вячеславовна'})
            SET e.position = 'Начальник отдела организации лекарственного обеспечения МЗ ОО',
                e.phone = '+7 (3532) 44-89-38',
                e.org = 'Минздрав Оренбургской области';
            """,
            """
            MERGE (e:Employee {name: 'Колмыкова Елена Александровна'})
            SET e.position = 'Консультант отдела организации лекарственного обеспечения',
                e.phone = '+7 (3532) 44-89-38 доб. 211',
                e.org = 'Минздрав Оренбургской области';
            """,
            """
            MERGE (e:Employee {name: 'Куксина Виктория Николаевна'})
            SET e.position = 'Главный специалист отдела организации лекарственного обеспечения',
                e.phone = '+7 (3532) 44-89-38 доб. 212',
                e.org = 'Минздрав Оренбургской области';
            """,
            """
            MERGE (e:Employee {name: 'Пешкова Лариса Александровна'})
            SET e.position = 'Ведущий специалист отдела организации лекарственного обеспечения',
                e.phone = '+7 (3532) 44-89-38 доб. 213',
                e.org = 'Минздрав Оренбургской области';
            """,
            """
            MERGE (e:Employee {name: 'Вяткина Ирина Николаевна'})
            SET e.position = 'Ведущий специалист отдела организации лекарственного обеспечения',
                e.phone = '+7 (3532) 44-89-38 доб. 214',
                e.org = 'Минздрав Оренбургской области';
            """,
            """
            MERGE (e:Employee {name: 'Савельева Любовь Ивановна'})
            SET e.position = 'Начальник отдела прогнозирования и анализа лекарственного обеспечения МЗ ОО',
                e.phone = '+7 (3532) 44-89-40',
                e.org = 'Минздрав Оренбургской области';
            """,
            """
            MERGE (e:Employee {name: 'Рогачева Анастасия Владимировна'})
            SET e.position = 'Главный специалист отдела прогнозирования',
                e.phone = '+7 (3532) 44-89-40 доб. 215',
                e.org = 'Минздрав Оренбургской области';
            """,
            """
            MERGE (e:Employee {name: 'Новикова Екатерина Андреевна'})
            SET e.position = 'Ведущий специалист отдела прогнозирования',
                e.phone = '+7 (3532) 44-89-40 доб. 216',
                e.org = 'Минздрав Оренбургской области';
            """,
            # Связи сотрудников с их отделами (WORKS_IN)
            """
            MATCH (e:Employee {name: 'Ступин Сергей Михайлович'}), (d:Department {code: 'OAS_MANAGEMENT'}) MERGE (e)-[:WORKS_IN]->(d);
            """,
            """
            MATCH (e:Employee {name: 'Дебус Ирина Александровна'}), (d:Department {code: 'OAS_MANAGEMENT'}) MERGE (e)-[:WORKS_IN]->(d);
            """,
            """
            MATCH (e:Employee {name: 'Бурасова Светлана Сергеевна'}), (d:Department {code: 'OAS_PROCUREMENT'}) MERGE (e)-[:WORKS_IN]->(d);
            """,
            """
            MATCH (e:Employee {name: 'Квасов Александр Владимирович'}), (d:Department {code: 'OAS_WAREHOUSE'}) MERGE (e)-[:WORKS_IN]->(d);
            """,
            """
            MATCH (e:Employee {name: 'Вербина Евгения Андреевна'}), (d:Department {code: 'OAS_QA'}) MERGE (e)-[:WORKS_IN]->(d);
            """,
            """
            MATCH (e:Employee {name: 'Князев Алексей Николаевич'}), (d:Department {code: 'OAS_WAREHOUSE'}) MERGE (e)-[:WORKS_IN]->(d);
            """,
            """
            MATCH (e:Employee {name: 'Сумкин Евгений Владимирович'}), (d:Department {code: 'OAS_IT'}) MERGE (e)-[:WORKS_IN]->(d);
            """,
            """
            MATCH (e:Employee {name: 'Рагузин Сергей Владимирович'}), (d:Department {code: 'OAS_OSMP'}) MERGE (e)-[:WORKS_IN]->(d);
            """,
            """
            MATCH (e:Employee {name: 'Какунина Татьяна Вячеславовна'}), (d:Department {code: 'MZ_OAS_LEK'}) MERGE (e)-[:WORKS_IN]->(d);
            """,
            """
            MATCH (e:Employee {name: 'Колмыкова Елена Александровна'}), (d:Department {code: 'MZ_OAS_LEK'}) MERGE (e)-[:WORKS_IN]->(d);
            """,
            """
            MATCH (e:Employee {name: 'Куксина Виктория Николаевна'}), (d:Department {code: 'MZ_OAS_LEK'}) MERGE (e)-[:WORKS_IN]->(d);
            """,
            """
            MATCH (e:Employee {name: 'Пешкова Лариса Александровна'}), (d:Department {code: 'MZ_OAS_LEK'}) MERGE (e)-[:WORKS_IN]->(d);
            """,
            """
            MATCH (e:Employee {name: 'Вяткина Ирина Николаевна'}), (d:Department {code: 'MZ_OAS_LEK'}) MERGE (e)-[:WORKS_IN]->(d);
            """,
            """
            MATCH (e:Employee {name: 'Савельева Любовь Ивановна'}), (d:Department {code: 'MZ_PROGNOZ'}) MERGE (e)-[:WORKS_IN]->(d);
            """,
            """
            MATCH (e:Employee {name: 'Рогачева Анастасия Владимировна'}), (d:Department {code: 'MZ_PROGNOZ'}) MERGE (e)-[:WORKS_IN]->(d);
            """,
            """
            MATCH (e:Employee {name: 'Новикова Екатерина Андреевна'}), (d:Department {code: 'MZ_PROGNOZ'}) MERGE (e)-[:WORKS_IN]->(d);
            """
        ]
        for q in queries_emp:
            s.run(q)

        # -------------------------------------------------------------------------
        # 4. Нормативно-правовые акты (Regulation)
        # -------------------------------------------------------------------------
        logger.info("4. Создание нормативных актов (Regulation)...")
        queries_regs = [
            # Федеральные законы
            """
            MERGE (r:Regulation {code: 'FZ-323'})
            SET r.title = 'Федеральный закон № 323-ФЗ «Об основах охраны здоровья граждан в РФ»',
                r.scope = 'Федеральный',
                r.key_articles = 'ст. 80 (госгарантии), ст. 73 (обязанности медработников), ст. 91 (ЕГИСЗ)';
            """,
            """
            MERGE (r:Regulation {code: 'FZ-61'})
            SET r.title = 'Федеральный закон № 61-ФЗ «Об обращении лекарственных средств»',
                r.scope = 'Федеральный',
                r.key_articles = 'ст. 67.1 (МДЛП маркировка), ст. 55 (порядок розничной торговли)';
            """,
            """
            MERGE (r:Regulation {code: 'FZ-178'})
            SET r.title = 'Федеральный закон № 178-ФЗ «О государственной социальной помощи»',
                r.scope = 'Федеральный',
                r.key_articles = 'ст. 6.1, 6.2 (набор соцуслуг, ОНЛП льготное лекарственное обеспечение)';
            """,
            """
            MERGE (r:Regulation {code: 'FZ-3'})
            SET r.title = 'Федеральный закон № 3-ФЗ «О наркотических средствах и психотропных веществах»',
                r.scope = 'Федеральный',
                r.key_articles = 'Списки II и III, предметно-количественный учет (ПКУ)';
            """,
            # Постановления Правительства РФ
            """
            MERGE (r:Regulation {code: 'PP-890'})
            SET r.title = 'Постановление Правительства РФ № 890 от 30.07.1994',
                r.scope = 'Федеральный / Региональный',
                r.description = 'Региональные льготы: Перечень групп населения и категорий заболеваний с бесплатным и 50% отпуском лекарств';
            """,
            """
            MERGE (r:Regulation {code: 'PP-1556'})
            SET r.title = 'Постановление Правительства РФ № 1556 от 14.12.2018',
                r.scope = 'Федеральный',
                r.description = 'Положение о системе мониторинга движения лекарственных препаратов (ФГИС МДЛП «Честный Знак»)';
            """,
            # Приказы Минздрава России
            """
            MERGE (r:Regulation {code: 'MZ-RF-1094n'})
            SET r.title = 'Приказ Минздрава России № 1094н от 24.11.2021',
                r.scope = 'Федеральный',
                r.description = 'Порядок назначения лекарственных препаратов, формы рецептурных бланков 148-1/у-88, 107-1/у, 107/у-НП и электронные рецепты';
            """,
            """
            MERGE (r:Regulation {code: 'MZ-RF-1093n'})
            SET r.title = 'Приказ Минздрава России № 1093н от 24.11.2021',
                r.scope = 'Федеральный',
                r.description = 'Правила отпуска лекарственных препаратов аптечными организациями и ИП';
            """,
            """
            MERGE (r:Regulation {code: 'MZ-RF-947n'})
            SET r.title = 'Приказ Минздрава России № 947н от 07.09.2020',
                r.scope = 'Федеральный',
                r.description = 'Порядок организации электронного медицинского документооборота (ЭМЭД/РЭМД ЕГИСЗ)';
            """,
            """
            MERGE (r:Regulation {code: 'MZ-RF-459n'})
            SET r.title = 'Приказ Минздрава России № 459н от 01.09.2023',
                r.scope = 'Федеральный',
                r.description = 'Перечень лекарственных средств, подлежащих предметно-количественному учету (ПКУ), действующий с 01.09.2024';
            """,
            # Региональные акты Оренбургской области
            """
            MERGE (r:Regulation {code: 'PP-OO-528-pp'})
            SET r.title = 'Постановление Правительства Оренбургской области № 528-пп',
                r.scope = 'Региональный (Оренбургская область)',
                r.description = 'Положение о государственной информационной системе в сфере здравоохранения Оренбургской области (ГИСЗ ОО)';
            """,
            """
            MERGE (r:Regulation {code: 'PP-OO-597-pp'})
            SET r.title = 'Постановление Правительства Оренбургской области № 597-пп',
                r.scope = 'Региональный (Оренбургская область)',
                r.description = 'Порядок информационного взаимодействия при формировании и использовании электронных рецептов с Картой жителя Оренбуржья';
            """,
            """
            MERGE (r:Regulation {code: 'MZ-OO-2983'})
            SET r.title = 'Распоряжение Минздрава Оренбургской области № 2983 от 12.12.2019',
                r.scope = 'Региональный (Оренбургская область)',
                r.description = 'Определение ГАУЗ «ОАС» оператором ИС «Лекарственное обеспечение» на территории Оренбургской области';
            """,
            """
            MERGE (r:Regulation {code: 'MZ-OO-740'})
            SET r.title = 'Распоряжение Минздрава Оренбургской области № 740',
                r.scope = 'Региональный (Оренбургская область)',
                r.description = 'Обеспечение лекарственными препаратами пациентов высокого сердечно-сосудистого риска (программа БСК)';
            """,
            """
            MERGE (r:Regulation {code: 'MZ-OO-390'})
            SET r.title = 'Распоряжение Минздрава Оренбургской области № 390 от 21.02.2024',
                r.scope = 'Региональный (Оренбургская область)',
                r.description = 'Актуализация перечня и порядка обеспечения пациентов по программе БСК в 2024 году';
            """,
            """
            MERGE (r:Regulation {code: 'MZ-OO-1627'})
            SET r.title = 'Распоряжение Минздрава Оренбургской области № 1627 от 01.08.2025',
                r.scope = 'Региональный (Оренбургская область)',
                r.description = 'Организация льготного лекарственного обеспечения по БСК и реестры пациентов на 2025-2026 гг.';
            """,
            """
            MERGE (r:Regulation {code: 'MZ-OO-883'})
            SET r.title = 'Распоряжение Минздрава Оренбургской области № 883 от 07.05.2024',
                r.scope = 'Региональный (Оренбургская область)',
                r.description = 'Справочник категорий граждан, имеющих право на льготное обеспечение (код 85 инвалиды, региональные коды)';
            """,
            """
            MERGE (r:Regulation {code: 'MZ-OO-1119'})
            SET r.title = 'Распоряжение Минздрава Оренбургской области № 1119 от 10.06.2026',
                r.scope = 'Региональный (Оренбургская область)',
                r.description = 'Мониторинг остатков лекарственных препаратов и оперативное устранение дефектуры';
            """,
            """
            MERGE (r:Regulation {code: 'MZ-OO-2719'})
            SET r.title = 'Распоряжение Минздрава Оренбургской области № 2719 от 24.11.2025',
                r.scope = 'Региональный (Оренбургская область)',
                r.description = 'Центр персонифицированного сопровождения льготного лекарственного обеспечения (ЦПС ЛЛО)';
            """
        ]
        for q in queries_regs:
            s.run(q)

        # -------------------------------------------------------------------------
        # 5. Информационные системы (System)
        # -------------------------------------------------------------------------
        logger.info("5. Создание систем (System)...")
        queries_sys = [
            """
            MERGE (s:System {name: 'ИС «Лекарственное обеспечение»'})
            SET s.short_name = 'ИС ЛО',
                s.type = 'WMS / Региональная система льготного учета',
                s.operator = 'ГАУЗ «ОАС»',
                s.description = 'Централизованная система выписки льготных рецептов, распределения остатков и учета в медицинских и аптечных организациях Оренбургской области';
            """,
            """
            MERGE (s:System {name: 'ГИСЗ Оренбургской области'})
            SET s.short_name = 'ГИСЗ ОО',
                s.type = 'Региональная медицинская система',
                s.description = 'Единая государственная информационная система в сфере здравоохранения Оренбургской области';
            """,
            """
            MERGE (s:System {name: 'ФГИС МДЛП «Честный Знак»'})
            SET s.short_name = 'МДЛП',
                s.type = 'Федеральная система маркировки',
                s.operator = 'ЦРПТ',
                s.description = 'Мониторинг движения лекарственных препаратов по цифровым кодам DataMatrix';
            """,
            """
            MERGE (s:System {name: 'Сервис «Универсальная карта оренбуржца»'})
            SET s.short_name = 'Карта жителя Оренбуржья',
                s.type = 'Региональный социальный сервис',
                s.description = 'Идентификатор пациента для обслуживания по электронным рецептам в пунктах «ГосАптека»';
            """,
            """
            MERGE (s:System {name: 'ЕГИСЗ (РЭМД)'})
            SET s.short_name = 'РЭМД',
                s.type = 'Федеральный реестр электронных медицинских документов',
                s.description = 'Регистрация СЭМД 102 (электронный рецепт) и сведений об отпуске';
            """,
            # Связи систем
            """
            MATCH (oas:Organization {name: 'ГАУЗ «ОАС»'}), (islo:System {name: 'ИС «Лекарственное обеспечение»'})
            MERGE (oas)-[:OPERATOR_OF]->(islo);
            """,
            """
            MATCH (islo:System {name: 'ИС «Лекарственное обеспечение»'}), (gisz:System {name: 'ГИСЗ Оренбургской области'})
            MERGE (islo)-[:INTEGRATES_WITH]->(gisz);
            """,
            """
            MATCH (islo:System {name: 'ИС «Лекарственное обеспечение»'}), (mdlp:System {name: 'ФГИС МДЛП «Честный Знак»'})
            MERGE (islo)-[:INTEGRATES_WITH]->(mdlp);
            """,
            """
            MATCH (islo:System {name: 'ИС «Лекарственное обеспечение»'}), (karta:System {name: 'Сервис «Универсальная карта оренбуржца»'})
            MERGE (islo)-[:INTEGRATES_WITH]->(karta);
            """,
            """
            MATCH (d:Department {code: 'OAS_OSMP'}), (islo:System {name: 'ИС «Лекарственное обеспечение»'})
            MERGE (d)-[:USES_SYSTEM]->(islo);
            """
        ]
        for q in queries_sys:
            s.run(q)

        # -------------------------------------------------------------------------
        # 6. Документы базы знаний № 70–78 (Document)
        # -------------------------------------------------------------------------
        logger.info("6. Создание документов базы знаний (Document 70-78)...")
        queries_docs = [
            """
            MERGE (d:Document {id: 'doc-kontakty-tehpodderzhki-islo-oas'})
            SET d.number = '70',
                d.title = 'Регламент и контакты технической поддержки ИС «Лекарственное обеспечение» и ГАУЗ «ОАС»',
                d.file = '70_kontakty_i_reglament_tehpodderzhki_is_lo_oas.md',
                d.type = 'Регламент / Справочник контактов',
                d.version = '1.0';
            """,
            """
            MERGE (d:Document {id: 'doc-elektronny-recept-karta-orenburzhtsa'})
            SET d.number = '71',
                d.title = 'Регламент работы с электронными рецептами и «Универсальной картой оренбуржца»',
                d.file = '71_elektronny_recept_karta_zhitelya_orenburzhtsa.md',
                d.type = 'Регламент процесса',
                d.version = '1.0';
            """,
            """
            MERGE (d:Document {id: 'doc-prikazy-mz-rf-1094n-1093n-pravila-otpuska'})
            SET d.number = '72',
                d.title = 'Нормативно-правовое регулирование: Приказы Минздрава России № 1094н, № 1093н, № 947н и № 911н',
                d.file = '72_prikazy_mz_rf_1094n_1093n_pravila_naznacheniya_i_otpuska.md',
                d.type = 'Нормативно-правовой справочник',
                d.version = '1.0';
            """,
            """
            MERGE (d:Document {id: 'doc-mdlp-shemy-vybytiya-registrator-vybytiya'})
            SET d.number = '73',
                d.title = 'Регламент МДЛП «Честный Знак»: Регистраторы выбытия, схемы льготного отпуска 10521 и списания 10531',
                d.file = '73_mdlp_shemy_vybytiya_registrator_vybytiya_10521_10531.md',
                d.type = 'Технический регламент МДЛП',
                d.version = '1.0';
            """,
            """
            MERGE (d:Document {id: 'doc-goryachie-linii-lekarstvennogo-obespecheniya-oo'})
            SET d.number = '74',
                d.title = 'Горячие линии и маршрутизация обращений по льготному лекарственному обеспечению в Оренбургской области',
                d.file = '74_goryachie_linii_i_marshruty_obrascheniy_lekarstva_oo.md',
                d.type = 'Справочник маршрутизации',
                d.version = '1.0';
            """,
            """
            MERGE (d:Document {id: 'doc-federalnye-zakony-323-61-178-3-fz'})
            SET d.number = '75',
                d.title = 'Справочник законодательства РФ: Федеральные законы № 323-ФЗ, № 61-ФЗ, № 178-ФЗ и № 3-ФЗ',
                d.file = '75_federalnye_zakony_323_61_178_3_fz_lekarstvennoe_obespechenie.md',
                d.type = 'Законодательный справочник',
                d.version = '1.0';
            """,
            """
            MERGE (d:Document {id: 'doc-postanovleniya-pravitelstva-rf-890-1556'})
            SET d.number = '76',
                d.title = 'Постановления Правительства РФ № 890 и № 1556: Региональные льготы и система маркировки МДЛП',
                d.file = '76_postanovleniya_pravitelstva_rf_890_1556_lgoty_i_markirovka.md',
                d.type = 'Нормативный справочник',
                d.version = '1.0';
            """,
            """
            MERGE (d:Document {id: 'doc-regionalnye-akty-oo-postanovleniya-528-597'})
            SET d.number = '77',
                d.title = 'Нормативные акты Оренбургской области: Постановления Правительства № 528-пп и № 597-пп',
                d.file = '77_regionalnye_akty_orenburgskoy_oblasti_postanovleniya_528_597.md',
                d.type = 'Региональный нормативный акт',
                d.version = '1.0';
            """,
            """
            MERGE (d:Document {id: 'doc-regionalnye-rasporyazheniya-mz-oo-reestry'})
            SET d.number = '78',
                d.title = 'Сводный реестр распоряжений Минздрава Оренбургской области: Программы БСК, коды льгот и мониторинг',
                d.file = '78_regionalnye_rasporyazheniya_mz_oo_lgotnye_programmy_reestry.md',
                d.type = 'Сводный региональный реестр',
                d.version = '1.0';
            """,
            # Связи документов с отделами, регламентами и системами
            """
            MATCH (d:Document {id: 'doc-kontakty-tehpodderzhki-islo-oas'}), (dep:Department {code: 'OAS_OSMP'})
            MERGE (dep)-[:OWNS]->(d);
            """,
            """
            MATCH (d:Document {id: 'doc-kontakty-tehpodderzhki-islo-oas'}), (sys:System {name: 'ИС «Лекарственное обеспечение»'})
            MERGE (d)-[:DESCRIBES]->(sys);
            """,
            """
            MATCH (d:Document {id: 'doc-elektronny-recept-karta-orenburzhtsa'}), (r:Regulation {code: 'PP-OO-597-pp'})
            MERGE (d)-[:COMPLIES_WITH]->(r);
            """,
            """
            MATCH (d:Document {id: 'doc-elektronny-recept-karta-orenburzhtsa'}), (s:System {name: 'Сервис «Универсальная карта оренбуржца»'})
            MERGE (d)-[:DESCRIBES]->(s);
            """,
            """
            MATCH (d:Document {id: 'doc-prikazy-mz-rf-1094n-1093n-pravila-otpuska'}), (r1:Regulation {code: 'MZ-RF-1094n'})
            MERGE (d)-[:COMPLIES_WITH]->(r1);
            """,
            """
            MATCH (d:Document {id: 'doc-prikazy-mz-rf-1094n-1093n-pravila-otpuska'}), (r2:Regulation {code: 'MZ-RF-1093n'})
            MERGE (d)-[:COMPLIES_WITH]->(r2);
            """,
            """
            MATCH (d:Document {id: 'doc-mdlp-shemy-vybytiya-registrator-vybytiya'}), (s:System {name: 'ФГИС МДЛП «Честный Знак»'})
            MERGE (d)-[:DESCRIBES]->(s);
            """,
            """
            MATCH (d:Document {id: 'doc-mdlp-shemy-vybytiya-registrator-vybytiya'}), (r:Regulation {code: 'PP-1556'})
            MERGE (d)-[:COMPLIES_WITH]->(r);
            """,
            """
            MATCH (d:Document {id: 'doc-federalnye-zakony-323-61-178-3-fz'}), (r1:Regulation {code: 'FZ-323'})
            MERGE (d)-[:COMPLIES_WITH]->(r1);
            """,
            """
            MATCH (d:Document {id: 'doc-federalnye-zakony-323-61-178-3-fz'}), (r2:Regulation {code: 'FZ-61'})
            MERGE (d)-[:COMPLIES_WITH]->(r2);
            """,
            """
            MATCH (d:Document {id: 'doc-federalnye-zakony-323-61-178-3-fz'}), (r3:Regulation {code: 'FZ-178'})
            MERGE (d)-[:COMPLIES_WITH]->(r3);
            """,
            """
            MATCH (d:Document {id: 'doc-federalnye-zakony-323-61-178-3-fz'}), (r4:Regulation {code: 'FZ-3'})
            MERGE (d)-[:COMPLIES_WITH]->(r4);
            """,
            """
            MATCH (d:Document {id: 'doc-postanovleniya-pravitelstva-rf-890-1556'}), (r1:Regulation {code: 'PP-890'})
            MERGE (d)-[:COMPLIES_WITH]->(r1);
            """,
            """
            MATCH (d:Document {id: 'doc-regionalnye-akty-oo-postanovleniya-528-597'}), (r1:Regulation {code: 'PP-OO-528-pp'})
            MERGE (d)-[:COMPLIES_WITH]->(r1);
            """,
            """
            MATCH (d:Document {id: 'doc-regionalnye-akty-oo-postanovleniya-528-597'}), (r2:Regulation {code: 'PP-OO-597-pp'})
            MERGE (d)-[:COMPLIES_WITH]->(r2);
            """,
            """
            MATCH (d:Document {id: 'doc-regionalnye-akty-oo-postanovleniya-528-597'}), (r3:Regulation {code: 'MZ-OO-2983'})
            MERGE (d)-[:COMPLIES_WITH]->(r3);
            """,
            """
            MATCH (d:Document {id: 'doc-regionalnye-rasporyazheniya-mz-oo-reestry'}), (r1:Regulation {code: 'MZ-OO-740'})
            MERGE (d)-[:COMPLIES_WITH]->(r1);
            """,
            """
            MATCH (d:Document {id: 'doc-regionalnye-rasporyazheniya-mz-oo-reestry'}), (r2:Regulation {code: 'MZ-OO-390'})
            MERGE (d)-[:COMPLIES_WITH]->(r2);
            """,
            """
            MATCH (d:Document {id: 'doc-regionalnye-rasporyazheniya-mz-oo-reestry'}), (r3:Regulation {code: 'MZ-OO-1627'})
            MERGE (d)-[:COMPLIES_WITH]->(r3);
            """,
            """
            MATCH (d:Document {id: 'doc-regionalnye-rasporyazheniya-mz-oo-reestry'}), (r4:Regulation {code: 'MZ-OO-883'})
            MERGE (d)-[:COMPLIES_WITH]->(r4);
            """,
            """
            MATCH (d:Document {id: 'doc-regionalnye-rasporyazheniya-mz-oo-reestry'}), (r5:Regulation {code: 'MZ-OO-1119'})
            MERGE (d)-[:COMPLIES_WITH]->(r5);
            """
        ]
        for q in queries_docs:
            s.run(q)

        # -------------------------------------------------------------------------
        # 7. Бизнес-процессы (Process) и шаги (Step)
        # -------------------------------------------------------------------------
        logger.info("7. Создание процессов (Process) и связей с регламентами...")
        queries_proc = [
            """
            MERGE (p:Process {name: 'Выписка льготного электронного рецепта'})
            SET p.owner = 'Медицинская организация / Врач',
                p.version = '2026.1';
            """,
            """
            MERGE (p:Process {name: 'Отпуск льготных лекарственных препаратов по Карте оренбуржца'})
            SET p.owner = 'ГАУЗ «ОАС» / ГосАптека 56',
                p.version = '2026.1';
            """,
            """
            MERGE (p:Process {name: 'Выбытие маркированных лекарственных препаратов в МДЛП (10521)'})
            SET p.owner = 'Аптечная организация / Провизор',
                p.version = '2026.1';
            """,
            """
            MERGE (p:Process {name: 'Списание лекарственных препаратов по регистратору выбытия (10531)'})
            SET p.owner = 'Медицинская организация / Старшая медсестра',
                p.version = '2026.1';
            """,
            """
            MERGE (p:Process {name: 'Обеспечение пациентов высокого кардио-риска по программе БСК'})
            SET p.owner = 'Минздрав ОО / Медорганизации / ГАУЗ «ОАС»',
                p.version = '2026.1';
            """,
            # Связи процессов с нормативными актами и системами
            """
            MATCH (p:Process {name: 'Выписка льготного электронного рецепта'}), (r:Regulation {code: 'MZ-RF-1094n'})
            MERGE (p)-[:COMPLIES_WITH]->(r);
            """,
            """
            MATCH (p:Process {name: 'Отпуск льготных лекарственных препаратов по Карте оренбуржца'}), (r:Regulation {code: 'PP-OO-597-pp'})
            MERGE (p)-[:COMPLIES_WITH]->(r);
            """,
            """
            MATCH (p:Process {name: 'Отпуск льготных лекарственных препаратов по Карте оренбуржца'}), (r:Regulation {code: 'MZ-RF-1093n'})
            MERGE (p)-[:COMPLIES_WITH]->(r);
            """,
            """
            MATCH (p:Process {name: 'Выбытие маркированных лекарственных препаратов в МДЛП (10521)'}), (r:Regulation {code: 'PP-1556'})
            MERGE (p)-[:COMPLIES_WITH]->(r);
            """,
            """
            MATCH (p:Process {name: 'Обеспечение пациентов высокого кардио-риска по программе БСК'}), (r:Regulation {code: 'MZ-OO-740'})
            MERGE (p)-[:COMPLIES_WITH]->(r);
            """
        ]
        for q in queries_proc:
            s.run(q)

        # -------------------------------------------------------------------------
        # Итоговая статистика графа
        # -------------------------------------------------------------------------
        logger.info("Проверка итоговой статистики Memgraph...")
        nodes_stat = s.run("MATCH (n) RETURN labels(n) AS lbl, count(n) AS cnt ORDER BY cnt DESC").data()
        rels_stat = s.run("MATCH ()-[r]->() RETURN type(r) AS rel, count(r) AS cnt ORDER BY cnt DESC").data()
        
        print("\n================== ИТОГОВАЯ СТАТИСТИКА ГРАФА MEMGRAPH ==================")
        print("Узлы (Nodes):")
        total_n = 0
        for r in nodes_stat:
            total_n += r["cnt"]
            print(f"  {r['lbl']}: {r['cnt']}")
        print(f"Всего узлов: {total_n}")

        print("\nСвязи (Relations):")
        total_r = 0
        for r in rels_stat:
            total_r += r["cnt"]
            print(f"  {r['rel']}: {r['cnt']}")
        print(f"Всего связей: {total_r}")
        print("=======================================================================\n")

    driver.close()
    logger.info("Прикрепление к графу Memgraph успешно завершено!")


if __name__ == "__main__":
    attach_knowledge_to_graph()
