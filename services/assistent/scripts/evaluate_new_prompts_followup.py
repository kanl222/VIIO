"""Fresh natural questions against the deployed main agent; no search commands."""
import asyncio
import evaluate_main_natural as evaluation

evaluation.CASES = [
    ('controller_precise', 'Какой контроллер отвечает за административную настройку каталога отчётов SSRSReport? Нужен контроллер, а не модель доступа.', 'programmer'),
    ('mapping_contract', 'Для формы выбора типа отчёта SSRSReport напиши Python-словарь report_types. Ключи строго "SSRS" и "ИСЛО", значения — числовой report_type этих видов отчётов.', 'programmer'),
    ('parameter_contexts', 'В JSON-описании параметра dateStart для отчёта SSRS какой parameterType? Сравни его с типом dateStart в перечне полей и объясни разницу.', 'programmer'),
    ('direction_table', 'Какие числовые значения flag_sp соответствуют ВЗН, ОНЛП и 1191 в отчётах SSRS? Нужна краткая таблица.', 'employee'),
]

if __name__ == '__main__':
    asyncio.run(evaluation.main())
