"""Storage options passed to the file-store factory without service credentials."""
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from viio_documents.config import Settings


@dataclass(frozen=True)
class StorageConfig:
    documents_storage_backend: Literal["local", "s3"]
    documents_storage_path: str
    s3_endpoint_url: str | None
    s3_bucket: str
    s3_access_key: str | None = field(repr=False)
    s3_secret_key: str | None = field(repr=False)
    s3_region: str

    @classmethod
    def from_settings(cls, settings: "Settings | StorageConfig") -> "StorageConfig":
        if isinstance(settings, cls):
            return settings
        return cls(
            documents_storage_backend=settings.documents_storage_backend,
            documents_storage_path=settings.documents_storage_path,
            s3_endpoint_url=settings.s3_endpoint_url,
            s3_bucket=settings.s3_bucket,
            s3_access_key=settings.s3_access_key,
            s3_secret_key=settings.s3_secret_key,
            s3_region=settings.s3_region,
        )
