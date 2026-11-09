"""Bounded PDF preview extraction; indexing and ACL assignment remain separate."""

import asyncio
import hashlib
from io import BytesIO

import anyio.to_thread
import httpx
from pypdf import PdfReader

from viio_documents.config import Settings
from viio_documents.services.text_normalization import normalize_document_text
from viio_rag.diagnostics import capture, record


class PdfExtractionError(Exception):
    def __init__(self, status: int, detail: str):
        self.status = status
        self.detail = detail
        super().__init__(detail)


class PdfExtractionService:
    def __init__(self, settings: Settings, *, filename=None, job_id=None):
        self.settings = settings
        self.filename, self.job_id = filename, job_id
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
        payload = response.json()
        text = payload.get("content")
        if not isinstance(text, str) or not text.strip():
            raise PdfExtractionError(422, "В PDF не удалось распознать текст.")
        if len(text) > self.settings.pdf_max_chars:
            raise PdfExtractionError(413, "В PDF больше 500 000 символов. Разделите документ на части.")
        if payload.get('pages', pages) != pages:
            raise PdfExtractionError(422, "Распознаны не все страницы PDF.")
        result = {"content": text.strip(), "pages": pages, "engine": "docling-cpu"}
        if isinstance(payload.get('document'), dict):
            result['document'] = payload['document']
        return result

    def _read(self, data: bytes) -> tuple[str, int, bool]:
        try:
            reader = PdfReader(BytesIO(data))
            if reader.is_encrypted:
                raise PdfExtractionError(400, "PDF защищён паролем. Загрузите незашифрованную копию.")
            pages = len(reader.pages)
            if not pages or pages > self.settings.pdf_max_pages:
                raise PdfExtractionError(413, f"PDF должен содержать от 1 до {self.settings.pdf_max_pages} страниц.")
            # Наличие текста ничего не говорит о колонках, таблицах или порядке чтения.
            # auto использует парсер макета; pypdf — явное устаревшее включение.
            if self.settings.pdf_engine != "pypdf":
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
        with capture('ocr', self.filename or 'Распознавание PDF', self.job_id) as trace:
            if trace is not None:
                record('ocr_request', engine=self.settings.pdf_engine, size_bytes=len(data),
                       filename=self.filename,
                       sha256=hashlib.sha256(data).hexdigest(), languages=['rus', 'eng'],
                       max_pages=self.settings.pdf_max_pages, max_chars=self.settings.pdf_max_chars,
                       timeout_seconds=self.settings.pdf_timeout_seconds)
            try:
                result = await self._extract(data)
            except PdfExtractionError as error:
                record('ocr_result', status='error', error=type(error).__name__,
                       status_code=error.status, detail=error.detail)
                raise
            except Exception as error:
                record('ocr_result', status='error', error=type(error).__name__)
                raise
            if trace is not None:
                record('ocr_result', status='ok', **result)
            return result

    async def _extract(self, data: bytes) -> dict:
        if len(data) > self.settings.pdf_max_bytes:
            raise PdfExtractionError(413, f"Размер PDF превышает лимит {self.settings.pdf_max_bytes // 1024 // 1024} МБ.")
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
