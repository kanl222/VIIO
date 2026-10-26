FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 libgl1 libgomp1 tesseract-ocr tesseract-ocr-eng tesseract-ocr-rus \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py start.py ./
ENV PYTHONUNBUFFERED=1 CUDA_VISIBLE_DEVICES="" HF_HOME=/models/huggingface TORCH_HOME=/models/torch DOCLING_ARTIFACTS_PATH=/models/artifacts
EXPOSE 8080
CMD ["python", "start.py"]
