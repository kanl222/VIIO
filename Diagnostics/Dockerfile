FROM node:24-alpine AS frontend
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY index.html tsconfig.json vite.config.ts ./
COPY src ./src
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
COPY server.py ./server.py
COPY --from=frontend /app/dist ./dist
RUN mkdir -p /data/reviews /traces/main /traces/consultant /traces/documents /traces/document-worker /traces/rag && chown -R 65532:65532 /data /traces
ENV PYTHONDONTWRITEBYTECODE=1 BIND_HOST=0.0.0.0
USER 65532:65532
CMD ["python", "server.py"]
