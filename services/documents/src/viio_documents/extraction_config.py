"""PDF runtime options without database, storage or agent configuration."""
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from viio_documents.config import Settings


@dataclass(frozen=True)
class PdfExtractionConfig:
    pdf_engine: Literal["auto", "pypdf", "docling"]
    pdf_max_bytes: int
    pdf_max_pages: int
    pdf_max_chars: int
    pdf_timeout_seconds: int
    docling_url: str
    docling_api_key: str | None = field(repr=False)

    @classmethod
    def from_settings(cls, settings: "Settings | PdfExtractionConfig") -> "PdfExtractionConfig":
        if isinstance(settings, cls):
            return settings
        return cls(
            pdf_engine=settings.pdf_engine,
            pdf_max_bytes=settings.pdf_max_bytes,
            pdf_max_pages=settings.pdf_max_pages,
            pdf_max_chars=settings.pdf_max_chars,
            pdf_timeout_seconds=settings.pdf_timeout_seconds,
            docling_url=settings.docling_url,
            docling_api_key=settings.docling_api_key or (
                settings.internal_api_key if not settings.secure_runtime else None
            ),
        )
