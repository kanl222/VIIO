from assistent.application.evidence_context import build_evidence_context, excerpt
from assistent.application.evidence_context import terms, graph_definition_context, strip_invalid_citations
import pytest


@pytest.mark.parametrize('question', ['ИСЛО', 'ИС ЛО', 'Что такое ИСЛО?', 'Что такео ИС ЛО?'])
def test_abbreviation_definition_spelling_is_consistent(question):
    assert 'исло' in terms(question)
    definitions = [{'name': 'ИСЛО', 'short_form': 'ИС ЛО',
                    'full_name': 'Информационная система «Лекарственное обеспечение»',
                    'description': 'Региональная система выписки электронных рецептов.'}]
    assert 'Региональная система' in graph_definition_context(question, definitions, 1000)
    assert graph_definition_context('Как выписать рецепт в ИС ЛО?', definitions, 1000) == ''


def test_invalid_citations_are_removed_without_changing_code():
    assert strip_invalid_citations('Привет [1].', 0) == 'Привет.'
    assert strip_invalid_citations('Факт [1], выдумка [9] и [0].', 1) == 'Факт [1], выдумка  и.'
    assert strip_invalid_citations('Пример `[1]`:\n```python\nx = [1]\n```\n[9]', 0).endswith('```\n')


def test_definitions_use_data_driven_multiword_aliases_and_short_terms():
    definitions = [{'name': 'ЭП', 'full_name': 'Электронная подпись',
                    'aliases': ['цифровая подпись', 'электронная подпись']}]
    assert 'Электронная подпись' in graph_definition_context('Что такое цифровая подпись?', definitions, 1000)
    assert 'Электронная подпись' in graph_definition_context('ЭП', definitions, 1000)
    assert not graph_definition_context('Как подписать документ ЭП?', definitions, 1000)


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
