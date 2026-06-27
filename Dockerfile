FROM python:3.12-slim

RUN adduser --disabled-password --uid 1000 appuser
WORKDIR /app

# Install runtime deps only (tests + dev deps live in requirements-dev.txt).
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
COPY frontend/ ./frontend/

RUN mkdir -p /data /app/backend/data/cdx \
 && chown -R appuser:appuser /data /app/backend/data
USER appuser

ENV DATABASE_URL=/data/waytrace.db
ENV PYTHONPATH=/app/backend
ENV PYTHONUNBUFFERED=1
EXPOSE 8000

# Persistent volume declaration so `docker run` without compose still
# keeps the SQLite DB between restarts.
VOLUME ["/data"]

# Baseline healthcheck for operators using `docker run` (compose has
# its own that overrides this).
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')" || exit 1

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
