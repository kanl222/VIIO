"""The file library follows the same allow rules as knowledge search."""
from enum import Enum


class DocumentAccessClass(str, Enum):
    PUBLIC = 'public'
    RESTRICTED = 'restricted'


def document_access_class(record):
    """Public knowledge is available to everyone, including anonymous guests."""
    return DocumentAccessClass.PUBLIC if record.is_public else DocumentAccessClass.RESTRICTED


def can_read_document(record, context):
    context = context or {}
    role = str(context.get("role", "guest")).lower()
    if getattr(record, 'document_kind', 'document') == 'code' and role not in {'admin', 'programmer'}:
        return False
    if role == "admin" or document_access_class(record) == DocumentAccessClass.PUBLIC:
        return True
    roles = {role, "employee"} if role == "programmer" else {role}
    return bool(
        roles.intersection(record.allowed_roles)
        or set(context.get("departments") or []).intersection(record.allowed_departments)
        or context.get("user_id") and str(context["user_id"]) == record.owner_id
    )
