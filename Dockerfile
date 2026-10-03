FROM python:3.12-slim

RUN adduser --disabled-password --uid 1000 appuser
WORKDIR /app

# Install runtime deps only (tests + dev deps live in requirements-dev.txt).
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
COPY frontend/ ./frontend/

# /data holds everything that must survive a rebuild: the database, the CDX
# cache and the application log. Nothing persistent lives in the image layer.
RUN mkdir -p /data/cdx-cache /data/logs /app/backend/data \
 && chown -R appuser:appuser /data /app/backend/data
USER appuser

# Build identity. Passed by deploy/scripts/deploy-prod.sh so /api/health can
# name the exact commit and build time this image came from. Declared after the
# COPY steps so changing them never invalidates the dependency layer cache.
ARG WAYTRACE_COMMIT=""
ARG WAYTRACE_BUILT_AT=""
ENV WAYTRACE_COMMIT=${WAYTRACE_COMMIT}
ENV WAYTRACE_BUILT_AT=${WAYTRACE_BUILT_AT}

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

# --no-access-log: uvicorn's access line contains the full path, so it wrote
# every /api/s/{url_id} capability token to the log. The reverse proxy already
# records requests (with those tokens redacted), and the healthcheck alone
# produced two lines a minute of pure noise.
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
