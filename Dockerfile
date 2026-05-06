# GenomeSpeak — Cloud Run Dockerfile
# Multi-stage build: keeps final image lean (~200MB vs ~800MB)

# ── Stage 1: dependency builder ──────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# Install build tools
RUN pip install --upgrade pip hatchling

# Copy dependency files first (layer cache)
COPY pyproject.toml ./
COPY genomespeak/__init__.py ./genomespeak/

# Install all deps into a prefix we'll copy to the final stage
RUN pip install --prefix=/install \
    google-adk \
    google-cloud-aiplatform \
    google-cloud-storage \
    google-cloud-firestore \
    pydantic \
    python-dotenv \
    fastapi \
    "uvicorn[standard]" \
    httpx \
    packaging

# ── Stage 2: runtime image ────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

# Non-root user for Cloud Run security best practice
RUN useradd --create-home --shell /bin/bash genomespeak
WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application code
COPY genomespeak/ ./genomespeak/
COPY api/          ./api/
COPY frontend/     ./frontend/
COPY scripts/      ./scripts/

# Cloud Run injects PORT env var; default 8080
ENV PORT=8080
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Switch to non-root
USER genomespeak

# Cloud Run health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:${PORT}/health')"

EXPOSE ${PORT}

CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port ${PORT} --workers 2 --log-level info"]
