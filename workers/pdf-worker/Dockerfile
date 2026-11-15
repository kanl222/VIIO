FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 libgl1 libgomp1 tesseract-ocr tesseract-ocr-eng tesseract-ocr-rus \
    && rm -rf /var/lib/apt/lists/*
RUN --mount=type=cache,target=/root/.cache/pip pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
COPY requirements.txt ./
RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements.txt
COPY app.py start.py ./
RUN mkdir -p /models/huggingface /models/torch /models/artifacts && chown -R 65532:65532 /models
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 CUDA_VISIBLE_DEVICES="" HF_HOME=/models/huggingface TORCH_HOME=/models/torch DOCLING_ARTIFACTS_PATH=/models/artifacts
EXPOSE 8080
USER 65532:65532
CMD ["python", "start.py"]
