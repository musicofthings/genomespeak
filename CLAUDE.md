# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install (editable + dev deps)
pip install -e ".[dev]"

# Run API locally (port 8080)
python -m api.main

# Run tests (no GCP credentials required)
pytest tests/ -v

# Run a single test
pytest tests/test_selector.py::TestOverrideRules::test_tier4_always_pro_high -v

# Lint
ruff check .
ruff format .

# One-time GCP setup (APIs, IAM, buckets)
bash scripts/gcp_setup.sh

# Create or re-import RAG corpus (us-west1 required for new GCP projects)
python scripts/setup_rag_corpus.py --project YOUR_PROJECT_ID --location us-west1

# Deploy to Cloud Run (run from Cloud Shell or with gcloud auth)
gcloud run deploy genomespeak \
  --source . --region asia-south1 --allow-unauthenticated \
  --memory 2Gi --cpu 2 \
  --set-env-vars GOOGLE_CLOUD_PROJECT=...,GENOMESPEAK_RAG_CORPUS=...
```

## Architecture

The system is a Google ADK multi-agent pipeline. Every user request flows through four sequential stages:

**1. `QueryClassifierAgent`** (`genomespeak/harness/classifier.py`)  
Runs on `gemini-3.1-flash-lite-preview` at LOW thinking (~300ms). Takes raw user query + optional PDF filename + session state, returns a structured `QueryProfile` (Pydantic model). This is the only component that sees unprocessed user input.

**2. `ModelSelectorHarness`** (`genomespeak/harness/selector.py`)  
Pure Python, zero-latency. Applies 5 hard override rules first (clinical safety constraints), then falls back to a `TIER_MATRIX` lookup keyed on `(ComplexityTier, ReportType)`. Produces a `SelectionResult` containing a fully-resolved `ModelConfig`. Override rules encode things like "oncology somatic never below Pro+MEDIUM" and "TIER_4 always gets Pro+HIGH". Model configs are named presets in `MODEL_REGISTRY` (`harness/registry.py`).

**3. `DynamicAgentFactory`** (`genomespeak/agent.py`)  
Creates ephemeral `LlmAgent` instances per request — one specialist plus one `PlainLanguageAgent`. The specialist is selected from: `genomics`, `oncology`, `routine_lab`, `pharmacogenomics`. Both agents are composed into a `SequentialAgent` pipeline so the specialist runs first and the plain-language rewrite runs second.

**4. `GenomeSpeakOrchestrator`** (`genomespeak/agent.py`)  
Top-level class wiring all three stages. Called by the FastAPI `/chat` endpoint, it yields tokens via an `AsyncIterator` for SSE streaming. An `InMemorySessionService` is used locally; production uses Firestore.

### Key type relationships

```
QueryProfile (Pydantic)   — produced by QueryClassifierAgent
    ↓
SelectionResult (dataclass) + ModelConfig (dataclass)  — produced by ModelSelectorHarness
    ↓
LlmAgent (ADK)            — built by DynamicAgentFactory per request
```

All types are in `genomespeak/harness/models.py`. The test suite (`tests/test_selector.py`) exercises the harness entirely offline — no Vertex AI calls needed.

### API layer (`api/main.py`)

FastAPI with four endpoints:
- `POST /upload` — receives PDF, stores bytes in `SESSION_STORE` (in-memory; production: ADK Artifact → GCS), returns `artifact_name`
- `POST /chat` — JSON body `{session_id, query, user_mode}`, returns SSE stream of token events
- `GET /session/{id}` — session metadata
- `GET /health` — Cloud Run health check; also serves `frontend/index.html` at `/`

SSE event types: `meta`, `token`, `done`, `error`.

### PDF handling (`genomespeak/tools/pdf_ingest.py`)

PDFs are stored as ADK Artifacts (GCS-backed) and injected directly as multimodal `Part` objects into Gemini's content list. No OCR or Document AI — Gemini 3.1 Pro reads PDFs natively. The `pdf_save_tool` and `pdf_load_tool` are ADK `FunctionTool` wrappers. The loaded `Part` is cached in `tool_context.state["_pdf_part"]` so specialist agents don't re-fetch from GCS.

### MCP tools

External biomedical databases are connected via `MCPToolset` (PubMed, remote Streamable HTTP) and `FunctionTool` wrappers (ClinVar, gnomAD, CPIC, OncoKB, NCBI Gene). Tool-to-agent mapping: GenomicsAgent gets PubMed+ClinVar+gnomAD+NCBI Gene; OncologyAgent gets PubMed+OncoKB+ClinVar+NCBI Gene; PharmacogenomicsAgent gets CPIC+PubMed+NCBI Gene; RoutineLabAgent gets PubMed only; PlainLanguageAgent gets nothing (rewrite only).

## Critical environment notes

- `GOOGLE_GENAI_USE_VERTEXAI=1` must be set — ADK must route through Vertex AI, not the Gemini API (which requires an API key). This is set via `os.environ.setdefault` in `agent.py` but must also be present in Cloud Run env vars.
- All Gemini model strings use `-preview` suffix: `gemini-3.1-pro-preview`, `gemini-3-flash-preview`, `gemini-3.1-flash-lite-preview`. These are Vertex AI endpoint identifiers — confirm availability before changing.
- Vertex AI RAG Engine requires `us-west1` for new GCP projects (`us-central1` is allowlist-only). The existing corpus is at `projects/1075013625841/locations/us-west1/ragCorpora/6917529027641081856`.
- Cloud Run is deployed to `asia-south1` (Mumbai) for India latency.
- `GOOGLE_CLOUD_LOCATION` defaults to `"global"` in `agent.py` — this is intentional for Gemini 3.x preview models which use the global endpoint.

## ADK entry point

ADK discovers the agent via `root_agent = create_agent()` at module level in `genomespeak/agent.py`. The `create_agent()` function returns a thin wrapper `LlmAgent` (Flash-Lite); the real model is selected per-request by the harness. Running `adk run genomespeak` or `adk deploy cloud-run genomespeak` targets this file.
