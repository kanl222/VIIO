import pytest

from viio_documents.config import Settings
from viio_documents.extraction_config import PdfExtractionConfig
from viio_documents.infrastructure.extraction.pdf_extraction import PdfExtractionService


@pytest.mark.parametrize("secure,dedicated,expected", [
    (False, None, "legacy-test-token"),
    (True, None, None),
    (False, "docling-test-token", "docling-test-token"),
    (True, "docling-test-token", "docling-test-token"),
])
def test_pdf_receives_only_resolved_docling_credentials(secure, dedicated, expected):
    settings = Settings(_env_file=None, secure_runtime=secure,
                        internal_api_key="legacy-test-token", docling_api_key=dedicated)
    config = PdfExtractionConfig.from_settings(settings)
    service = PdfExtractionService(config)
    assert service.settings.docling_api_key == expected
    assert not hasattr(service.settings, "internal_api_key")
    assert not hasattr(service.settings, "postgres_password")
    assert "test-token" not in repr(config)
