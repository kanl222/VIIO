from types import SimpleNamespace
import pytest
from viio_rag.application import access


def record(public):
    return SimpleNamespace(is_public=public, document_kind='document',
        allowed_roles=['employee'], allowed_departments=['finance'], owner_id='owner')


@pytest.mark.parametrize('context', [None, {}, {'role': 'guest'}, {'role': 'employee'},
    {'role': 'manager'}, {'role': 'programmer'}, {'role': 'admin'}])
def test_public_class_allows_everyone(context):
    doc = record(True)
    assert access.document_access_class(doc).value == 'public'
    assert access.can_read_document(doc, context)


def test_restricted_document_is_not_opened_to_guest():
    doc = record(False)
    assert access.document_access_class(doc).value == 'restricted'
    assert not access.can_read_document(doc, None)
    assert not access.can_read_document(doc, {'role': 'guest'})
    assert access.can_read_document(doc, {'role': 'employee'})


def test_source_code_retains_separate_role_restriction():
    doc = record(True)
    doc.document_kind = 'code'
    assert not access.can_read_document(doc, {'role': 'guest'})
    assert access.can_read_document(doc, {'role': 'programmer'})
