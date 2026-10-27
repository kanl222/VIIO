"""The file library follows the same allow rules as knowledge search."""


def can_read_document(record, context):
    context = context or {}
    role = str(context.get("role", "guest")).lower()
    if role == "admin" or record.is_public:
        return True
    roles = {role, "employee"} if role == "programmer" else {role}
    return bool(
        roles.intersection(record.allowed_roles)
        or set(context.get("departments") or []).intersection(record.allowed_departments)
        or context.get("user_id") and str(context["user_id"]) == record.owner_id
    )
