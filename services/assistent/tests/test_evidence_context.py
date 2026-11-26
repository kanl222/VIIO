from assistent.application.evidence_context import build_evidence_context, excerpt


def test_evidence_deduplicates_parent_and_keeps_exact_menu():
    fragment = {'document_id': 'd1', 'title': 'Рецепты', 'section_path': ['Поиск'],
                'parent_context': '> Служебные сведения\n\nМеню Сервис → Поиск ЭР по СНИЛС.\n\nНажмите Поиск в системе.'}
    context, sources = build_evidence_context('Как найти рецепт по СНИЛС?', [fragment, fragment], 1000)
    assert context.count('Сервис → Поиск ЭР по СНИЛС') == 1
    assert 'Служебные сведения' not in context
    assert sources == [{'document_id': 'd1', 'title': 'Рецепты'}]
    assert len(context) <= 1000


def test_excerpt_keeps_matching_late_paragraph_in_budget():
    context = excerpt(('Общие сведения.\n\n' * 100) + 'Налогообложение: Закупка не облагается НДС.', {'налого', 'ндс'}, 180)
    assert 'Закупка не облагается НДС.' in context
    assert len(context) <= 180


def test_empty_evidence_is_safe():
    assert build_evidence_context('Вопрос', [None, {}, {'document_id': 'x'}]) == ('', [])


def test_legacy_heading_only_fragments_are_not_evidence():
    assert build_evidence_context('Поиск рецепта', [{'document_id': 'x', 'content': '# Поиск рецепта\n\n---'}]) == ('', [])
