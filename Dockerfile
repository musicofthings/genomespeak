# GenomeSpeak — Cloud Run Dockerfile
# Multi-stage build: keeps final image lean (~200MB vs ~800MB)

# ── Stage 1: dependency builder ──────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

RUN pip install --upgrade pip && python -m venv /venv

# Install all deps into the venv — fully self-contained, no --prefix tricks
RUN /venv/bin/pip install --no-cache-dir \
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

# Copy the entire venv from builder — packaging is guaranteed to be inside
COPY --from=builder /venv /venv

# Copy application code
COPY genomespeak/ ./genomespeak/
COPY api/          ./api/
COPY frontend/     ./frontend/
COPY scripts/      ./scripts/

# Activate venv for all subsequent commands
ENV PATH="/venv/bin:$PATH"
ENV PORT=8080
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Switch to non-root
USER genomespeak

# Cloud Run health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:${PORT}/health')"

EXPOSE ${PORT}

CMD ["sh", "-c", "/venv/bin/uvicorn api.main:app --host 0.0.0.0 --port ${PORT} --workers 2 --log-level info"]
