from dataclasses import dataclass
from datetime import datetime
from typing import Any

@dataclass
class DocumentRecord:
    """Запись каталога для одного документа."""

    id: str
    title: str
    mime_type: str
    file_size: int | None
    sha256: str | None
    owner_id: str | None
    version: int
    parent_id: str | None
    status: str
    is_public: bool
    allowed_roles: list[str]
    allowed_departments: list[str]
    storage_key: str | None
    chunks_count: int
    created_at: datetime
    updated_at: datetime
    family_id: str | None = None
    is_active: bool = True
    document_kind: str = 'document'
    original_filename: str | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "DocumentRecord":
        return cls(
            id=row["id"],
            title=row["title"],
            mime_type=row["mime_type"],
            file_size=row["file_size"],
            sha256=row["sha256"],
            owner_id=row["owner_id"],
            version=row["version"],
            parent_id=row["parent_id"],
            status=row["status"],
            is_public=row["is_public"],
            allowed_roles=list(row["allowed_roles"] or []),
            allowed_departments=list(row["allowed_departments"] or []),
            storage_key=row["storage_key"],
            chunks_count=row["chunks_count"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            family_id=row.get('family_id', row['id']),
            is_active=row.get('is_active', True),
            document_kind=row.get('document_kind', 'document'),
            original_filename=row.get('original_filename'),
        )
