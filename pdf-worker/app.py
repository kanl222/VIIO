"""CPU-only local Docling conversion with process-level timeout and bounded input."""

import asyncio
import hmac
from io import BytesIO
import multiprocessing
import os
import queue

import anyio.to_thread
from fastapi import FastAPI, HTTPException, Request

app = FastAPI()
busy = asyncio.Lock()
MAX_BYTES = 10 * 1024 * 1024
MAX_PAGES = int(os.environ.get("PDF_MAX_PAGES", "50"))
MAX_CHARS = int(os.environ.get("PDF_MAX_CHARS", "500000"))
TIMEOUT = int(os.environ.get("PDF_TIMEOUT_SECONDS", "120"))


def convert_child(data, output):
    try:
        from pypdf import PdfReader
        from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
        from docling.datamodel.base_models import ConversionStatus, DocumentStream, InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions, TesseractCliOcrOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption

        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted or not 1 <= len(reader.pages) <= MAX_PAGES:
            output.put({"status": 413})
            return
        options = PdfPipelineOptions(
            artifacts_path=os.environ.get("DOCLING_ARTIFACTS_PATH", "/models/artifacts"),
            accelerator_options=AcceleratorOptions(device=AcceleratorDevice.CPU, num_threads=int(os.environ.get("DOCLING_CPU_THREADS", "2"))),
            do_ocr=True,
            ocr_options=TesseractCliOcrOptions(lang=["eng", "rus"]),
            enable_remote_services=False,
            allow_external_plugins=False,
            document_timeout=TIMEOUT - 5,
        )
        converter = DocumentConverter(allowed_formats=[InputFormat.PDF], format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)})
        result = converter.convert(DocumentStream(name="upload.pdf", stream=BytesIO(data)), max_num_pages=MAX_PAGES, max_file_size=MAX_BYTES)
        if result.status != ConversionStatus.SUCCESS:
            output.put({"status": 422})
            return
        content = result.document.export_to_markdown().strip()
        if not content or len(content) > MAX_CHARS:
            output.put({"status": 413 if len(content) > MAX_CHARS else 422})
            return
        output.put({"content": content, "engine": "docling-cpu"})
    except Exception as exc:
        print("PDF conversion failed:", type(exc).__name__, flush=True)
        # Document contents, filenames and upstream exceptions are not returned.
        output.put({"status": 422})


def convert_bounded(data):
    context = multiprocessing.get_context("spawn")
    output = context.Queue(maxsize=1)
    process = context.Process(target=convert_child, args=(data, output))
    process.start()
    try:
        try:
            return output.get(timeout=TIMEOUT)
        except queue.Empty:
            return {"status": 504}
    finally:
        process.join(timeout=2)
        if process.is_alive():
            process.kill()
            process.join(timeout=5)
        output.cancel_join_thread()
        output.close()


@app.get("/health")
def health():
    return {"status": "ok", "device": "cpu"}


@app.post("/extract")
async def extract(request: Request):
    expected = os.environ.get("INTERNAL_API_KEY", "")
    supplied = request.headers.get("X-Internal-Token", "")
    if not expected:
        raise HTTPException(503, "OCR service key is not configured")
    if not hmac.compare_digest(expected.encode(), supplied.encode()):
        raise HTTPException(401, "Unauthorized")
    if busy.locked():
        raise HTTPException(429, "PDF conversion is busy")
    async with busy:
        data = bytearray()
        async for chunk in request.stream():
            if len(data) + len(chunk) > MAX_BYTES:
                raise HTTPException(413, "PDF too large")
            data.extend(chunk)
        if not data.startswith(b"%PDF-"):
            raise HTTPException(400, "Invalid PDF")
        result = await anyio.to_thread.run_sync(convert_bounded, bytes(data))
        if "status" in result:
            raise HTTPException(result["status"], "PDF conversion did not complete successfully")
        return result
