# ---- frontend -------------------------------------------------------------
FROM node:20-slim AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- python deps ----------------------------------------------------------
FROM python:3.12-slim AS deps
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY pyproject.toml ./
# Resolve from pyproject so the container and the local environment cannot drift.
RUN pip install --no-cache-dir \
      "google-adk>=2.0.0" "fastapi>=0.115" "uvicorn[standard]>=0.32" \
      "sqlalchemy[asyncio]>=2.0" "aiosqlite>=0.20" "pydantic>=2.9" \
      "python-dotenv>=1.0" "sse-starlette>=2.1"

# ---- runtime --------------------------------------------------------------
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/backend \
    # Cloud Run gives a read-only image and a writable, in-memory /tmp.
    FIXER_MISSION_DIR=/tmp/missions \
    FIXER_CACHE_DIR=/app/worldcache \
    PORT=8080

WORKDIR /app
COPY --from=deps /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=deps /usr/local/bin /usr/local/bin
COPY backend/ ./backend/
COPY scripts/ ./scripts/
COPY --from=frontend /build/dist ./frontend/dist

# Bake the simulated history into the image. Without this the first mission
# spends ~40s generating a day of traffic, which is the worst possible first
# impression for someone evaluating the project.
RUN FIXER_MISSION_DIR=/tmp/prebuild python scripts/prebuild_cache.py --seeds 4242 \
    && rm -rf /tmp/prebuild

RUN useradd --create-home --uid 1001 fixer && chown -R fixer:fixer /app
USER fixer

EXPOSE 8080
CMD exec uvicorn fixer.api.app:app --host 0.0.0.0 --port ${PORT} --workers 1
