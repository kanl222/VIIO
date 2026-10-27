"""Bounded PDF preview extraction; indexing and ACL assignment remain separate."""

import asyncio
from io import BytesIO

import anyio.to_thread
import httpx
from pypdf import PdfReader

from viio_documents.config import Settings
from viio_documents.services.text_normalization import normalize_document_text


class PdfExtractionError(Exception):
    def __init__(self, status: int, detail: str):
        self.status = status
        self.detail = detail
        super().__init__(detail)


class PdfExtractionService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.lock = asyncio.Lock()

    def _docling_result(self, response: httpx.Response, pages: int) -> dict:
        failures = {
            413: "PDF превышает ограничения обработки. Разделите документ на части.",
            422: "Не удалось распознать весь PDF. Проверьте качество скана или вставьте текст вручную.",
            429: "Распознавание PDF занято. Повторите загрузку позже.",
            504: "Распознавание PDF не успело завершиться. Разделите файл на части.",
        }
        status = 422 if response.status_code == 400 else response.status_code
        if status in failures:
            raise PdfExtractionError(status, failures[status])
        response.raise_for_status()
        text = response.json().get("content")
        if not isinstance(text, str) or not text.strip():
            raise PdfExtractionError(422, "В PDF не удалось распознать текст.")
        if len(text) > self.settings.pdf_max_chars:
            raise PdfExtractionError(413, "В PDF больше 500 000 символов. Разделите документ на части.")
        return {"content": normalize_document_text(text), "pages": pages, "engine": "docling-cpu"}

    def _read(self, data: bytes) -> tuple[str, int, bool]:
        try:
            reader = PdfReader(BytesIO(data))
            if reader.is_encrypted:
                raise PdfExtractionError(400, "PDF защищён паролем. Загрузите незашифрованную копию.")
            pages = len(reader.pages)
            if not pages or pages > self.settings.pdf_max_pages:
                raise PdfExtractionError(413, f"PDF должен содержать от 1 до {self.settings.pdf_max_pages} страниц.")
            if self.settings.pdf_engine == "docling":
                return "", pages, True
            texts = []
            needs_ocr = False
            total = 0
            for page in reader.pages:
                text = (page.extract_text() or "").strip()
                needs_ocr = needs_ocr or not text
                total += len(text) + 2
                if total > self.settings.pdf_max_chars:
                    raise PdfExtractionError(413, "В PDF больше 500 000 символов. Разделите документ на части.")
                texts.append(text)
            return "\n\n".join(texts).strip(), pages, needs_ocr
        except PdfExtractionError:
            raise
        except Exception:
            raise PdfExtractionError(400, "Не удалось прочитать PDF. Проверьте, что файл не повреждён.") from None

    async def extract(self, data: bytes) -> dict:
        if len(data) > self.settings.pdf_max_bytes:
            raise PdfExtractionError(413, "Размер PDF превышает лимит 10 МБ.")
        if not data.startswith(b"%PDF-"):
            raise PdfExtractionError(400, "Файл не является PDF.")
        if self.lock.locked():
            raise PdfExtractionError(429, "Другой PDF уже обрабатывается. Повторите загрузку немного позже.")
        async with self.lock:
            content, pages, needs_ocr = await anyio.to_thread.run_sync(self._read, data)
            if self.settings.pdf_engine == "pypdf" and needs_ocr:
                raise PdfExtractionError(422, "В PDF есть страницы без текста. Включите Docling для распознавания сканов.")
            if self.settings.pdf_engine != "docling" and not needs_ocr and content:
                return {"content": normalize_document_text(content), "pages": pages, "engine": "pypdf"}
            headers = {"Content-Type": "application/pdf"}
            token=self.settings.docling_api_key or (self.settings.internal_api_key if not self.settings.secure_runtime else None)
            if token:
                headers["X-Internal-Token"] = token
            try:
                async with httpx.AsyncClient(timeout=self.settings.pdf_timeout_seconds + 10, trust_env=False) as client:
                    response = await client.post(self.settings.docling_url.rstrip("/") + "/extract", content=data, headers=headers)
                return self._docling_result(response, pages)
            except PdfExtractionError:
                raise
            except httpx.TimeoutException:
                raise PdfExtractionError(504, "Распознавание PDF не успело завершиться. Разделите файл на части.") from None
            except Exception:
                raise PdfExtractionError(503, "Для этого PDF нужен Docling. Запустите сервис обработки PDF с профилем pdf и повторите загрузку.") from None
