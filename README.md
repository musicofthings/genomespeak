# 🧬 GenomeSpeak

**AI-powered lab and genomics report interpreter — plain language for everyone.**

> Built for the [Google Cloud Rapid Agent Hackathon](https://rapid-agent.devpost.com/) · Track 1: Build (Net-New Agents)  
> Stack: Google ADK · Gemini 3.1 Pro · MCP · Vertex AI RAG Engine · Cloud Run

---

## The problem

Every year, hundreds of millions of patients receive lab and genomics reports they cannot understand. A BRCA2 pathogenic variant report, a CBC with seven abnormal values, a pharmacogenomics panel — these are written for clinicians, not the people whose lives they affect. Patients either ignore them, spiral into anxiety from Google searches, or wait weeks to ask their doctor a question that could be answered immediately.

**GenomeSpeak closes this gap.**

---

## What it does

Upload any medical report PDF. Ask a question in plain English. Get an answer you actually understand.

| Report type | Examples |
|---|---|
| Routine labs | CBC, CMP, lipid panel, HbA1c, thyroid (TSH/T4), LFT, urine |
| Genomics germline | WES, WGS, hereditary cancer panels, BRCA1/2, Lynch syndrome MMR |
| Oncology somatic | Tumor NGS, ctDNA, TMB, MSI, PDL1, somatic mutation profiling |
| Pharmacogenomics | CYP2D6, CYP2C19, DPYD, TPMT, SLCO1B1 panels |
| Prenatal | NIPT, karyotype, chromosomal microarray, amniocentesis |

Two modes:
- **Patient mode** — zero jargon, warm analogies, emotional sensitivity, Grade 8 reading level
- **Doctor mode** — full clinical detail, ACMG classifications, NCCN guidelines, differential suggestions

---

## Architecture

```
User query + PDF upload
      │
      ▼
QueryClassifierAgent          gemini-3.1-flash-lite   LOW    ~300ms
(report type + query complexity → QueryProfile)
      │ QueryProfile
      ▼
ModelSelectorHarness          pure Python             0ms
(tier matrix + 5 safety override rules → ModelConfig)
      │ SelectionResult
      ▼
DynamicAgentFactory
      │
      ├─ GenomicsAgent         gemini-3.1-pro          HIGH   ACMG + MCP: ClinVar, gnomAD, PubMed, NCBI Gene
      ├─ OncologyAgent         gemini-3.1-pro          HIGH   OncoKB + MCP: PubMed, ClinVar
      ├─ RoutineLabAgent       gemini-3-flash           MEDIUM Code execution + MCP: PubMed
      └─ PharmacogenomicsAgent gemini-3.1-pro/flash     HIGH   MCP: CPIC, PubMed, NCBI Gene
            │ Technical interpretation
            ▼
      PlainLanguageAgent       gemini-3.1-flash-lite   LOW    Jargon → plain English
            │
            ▼
      Streamed SSE response → Chat frontend
```

---

## MCP tool integration

GenomeSpeak uses ADK's `MCPToolset` and `FunctionTool` wrappers to connect specialist agents to live biomedical databases in real time. Every connection is authenticated via GCP IAM — no credentials exposed to the model.

| Source | Type | Protocol | Tools |
|---|---|---|---|
| **PubMed** | Remote MCP server | Streamable HTTP | `search_pubmed`, `fetch_full_text`, `search_mesh_terms`, `find_related_articles` |
| **ClinVar** | FunctionTool | NCBI REST API | Variant classifications, pathogenicity, review status, conditions |
| **gnomAD** | FunctionTool | GraphQL API | Population allele frequencies → ACMG BA1 / BS1 / PM2 evidence |
| **CPIC** | FunctionTool | REST API | Prescribing guidelines for CYP2D6, CYP2C19, DPYD, TPMT, SLCO1B1 |
| **OncoKB** | FunctionTool | REST API | Therapeutic evidence Levels 1–4, R1/R2 resistance |
| **NCBI Gene** | FunctionTool | E-utilities | Gene summaries, aliases, location, OMIM disease links |

### How MCP is wired into ADK

```python
from google.adk.tools.mcp_tool.mcp_toolset import MCPToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams

# Remote MCP server — no local installation required
pubmed_toolset = MCPToolset(
    connection_params=StreamableHTTPConnectionParams(
        url="https://pubmed.caseyjhand.com/mcp",
        timeout=30,
    ),
    tool_filter=["search_pubmed", "fetch_full_text_article", "search_mesh_terms"],
)

# Injected into specialist agent alongside native ADK tools
genomics_agent = LlmAgent(
    model="gemini-3.1-pro-preview",
    tools=[pdf_load_tool, pubmed_toolset, clinvar_tool, gnomad_tool, ncbi_gene_tool],
    generate_content_config=GenerationConfig(
        thinking_config={"thinking_level": "HIGH"}
    ),
)
```

### Agent ↔ MCP tool mapping

| Agent | Live data tools |
|---|---|
| `GenomicsAgent` | PubMed MCP · ClinVar · gnomAD · NCBI Gene |
| `OncologyAgent` | PubMed MCP · OncoKB · ClinVar · NCBI Gene |
| `PharmacogenomicsAgent` | CPIC · PubMed MCP · NCBI Gene |
| `RoutineLabAgent` | PubMed MCP |
| `PlainLanguageAgent` | None — rewrites only, no lookup needed |

---

## Adaptive model selection

The harness automatically selects the right Gemini model. No hardcoded routing.

| Complexity | Routine lab | Genomics germline | Oncology somatic | PGx |
|---|---|---|---|---|
| TIER 1 — simple definition | Flash-Lite / LOW | Flash / MEDIUM* | Pro / MEDIUM* | Flash / MEDIUM |
| TIER 2 — reference ranges | Flash / MEDIUM | Pro / MEDIUM | Pro / MEDIUM* | Flash / HIGH |
| TIER 3 — cross-marker synthesis | Flash / HIGH | Pro / HIGH | Pro / HIGH | Pro / MEDIUM |
| TIER 4 — ACMG / NCCN expert | Pro / HIGH* | Pro / HIGH* | Pro / HIGH* | Pro / HIGH* |

`*` = safety override applied — clinical domain forces model upgrade regardless of query simplicity.

Cost range: ~$0.001 per patient question (Flash-Lite, LOW) → ~$0.08 per ACMG variant classification (Pro, HIGH).

---

## Native PDF ingestion

Gemini 3.1 Pro reads PDFs natively via its 1M token multimodal context window. No OCR, no Document AI preprocessing for standard reports. The PDF is stored as an ADK Artifact in GCS and loaded as a multimodal `Part` directly into the model content list.

---

## Google Cloud and external services used

| Service | Purpose |
|---|---|
| **Google ADK** | Multi-agent orchestration — `LlmAgent`, `SequentialAgent`, `Runner` |
| **ADK MCPToolset** | MCP client — connects to PubMed MCP server via Streamable HTTP |
| **Gemini 3.1 Pro** `gemini-3.1-pro-preview` | Expert clinical reasoning — genomics, oncology |
| **Gemini 3 Flash** `gemini-3-flash-preview` | Routine lab interpretation |
| **Gemini 3.1 Flash-Lite** `gemini-3.1-flash-lite-preview` | Classification + plain language rewrite |
| **Vertex AI RAG Engine** | Static clinical knowledge base (ACMG, NCCN, WHO, CPIC) |
| **Google Search Grounding** | Real-time FDA approvals and guideline updates |
| **Code Execution** | eGFR, LDL-Friedewald, z-score calculations |
| **ADK Artifact Service** | GCS-backed PDF storage and versioning |
| **ADK Memory Bank** | Cross-session patient history recall |
| **Cloud Run** | Backend deployment (`asia-south1`) |
| **Cloud Build** | CI/CD pipeline |
| **Firestore** | Session persistence |
| **Artifact Registry** | Container image storage |
| **Secret Manager** | Secrets management |
| **PubMed MCP** | Live biomedical literature via NCBI E-utilities |
| **ClinVar REST** | Live variant classification database |
| **gnomAD GraphQL** | Live population allele frequencies |
| **CPIC REST** | Live pharmacogenomics prescribing guidelines |
| **OncoKB REST** | Live therapeutic evidence levels |
| **NCBI Gene REST** | Live gene summaries |

---

## Quick start

### Prerequisites
- Python 3.11+
- Google Cloud SDK with billing-enabled project
- NCBI API key — free at https://www.ncbi.nlm.nih.gov/account/

### 1. Clone and install
```bash
git clone https://github.com/musicofthings/genomespeak.git
cd genomespeak
pip install -e ".[dev]"
```

### 2. GCP one-time setup
```bash
# Enable all required APIs
gcloud services enable aiplatform.googleapis.com run.googleapis.com \
  cloudbuild.googleapis.com storage.googleapis.com firestore.googleapis.com \
  artifactregistry.googleapis.com secretmanager.googleapis.com \
  --project YOUR_PROJECT_ID
```

### 3. Configure environment
```bash
cp .env.example .env
# Fill in: GOOGLE_CLOUD_PROJECT, NCBI_API_KEY, NCBI_EMAIL, ONCOKB_TOKEN
```

### 4. Set up RAG corpus
```bash
# First-time setup (us-west1 required for new projects)
python scripts/setup_rag_corpus.py --project YOUR_PROJECT_ID --location us-west1

# Re-import into existing corpus
python scripts/setup_rag_corpus.py \
  --project YOUR_PROJECT_ID \
  --location us-west1 \
  --corpus-resource projects/YOUR_PROJECT_NUMBER/locations/us-west1/ragCorpora/YOUR_ID
```

### 5. Run locally
```bash
python -m api.main          # API on :8080
open frontend/index.html    # Chat UI
```

### 6. Run tests
```bash
pytest tests/ -v
```

### 7. Deploy to Cloud Run (use Cloud Shell)
```bash
gcloud run deploy genomespeak \
  --source . \
  --region asia-south1 \
  --allow-unauthenticated \
  --memory 2Gi --cpu 2 \
  --set-env-vars GOOGLE_CLOUD_PROJECT=YOUR_PROJECT_ID \
  --set-env-vars GENOMESPEAK_RAG_CORPUS=YOUR_CORPUS_RESOURCE \
  --set-env-vars NCBI_API_KEY=YOUR_KEY \
  --project YOUR_PROJECT_ID
```

---

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `GOOGLE_CLOUD_PROJECT` | ✅ | GCP project ID |
| `GOOGLE_CLOUD_LOCATION` | ✅ | Vertex AI region (`us-central1`) |
| `GENOMESPEAK_RAG_CORPUS` | ✅ | Full RAG corpus resource name |
| `GENOMESPEAK_RAG_LOCATION` | ✅ | RAG corpus region (`us-west1`) |
| `NCBI_API_KEY` | Recommended | 10 req/s rate limit vs 3 req/s without |
| `NCBI_EMAIL` | Recommended | Required by NCBI usage policy |
| `ONCOKB_TOKEN` | Optional | Full OncoKB access (public tier works without) |
| `GENOMESPEAK_MAX_PDF_MB` | Optional | Max upload size, default 20 |

---

## Project structure

```
genomespeak/
├── genomespeak/
│   ├── agent.py                  # ADK orchestrator + DynamicAgentFactory
│   ├── harness/
│   │   ├── models.py             # Pydantic types — QueryProfile, ModelConfig, SelectionResult
│   │   ├── registry.py           # 8 named Gemini model configs
│   │   ├── classifier.py         # QueryClassifierAgent (Flash-Lite, LOW, ~300ms)
│   │   └── selector.py           # ModelSelectorHarness — tier matrix + 5 override rules
│   └── tools/
│       ├── pdf_ingest.py         # Native PDF → ADK Artifact → Gemini multimodal Part
│       └── mcp_registry.py       # MCPToolset (PubMed) + FunctionTools (ClinVar, gnomAD, CPIC, OncoKB, NCBI Gene)
├── api/
│   └── main.py                   # FastAPI: /upload, /chat (SSE), /session, /health
├── frontend/
│   └── index.html                # Single-file chat UI — drag-drop PDF, mode toggle, SSE streaming
├── scripts/
│   ├── setup_rag_corpus.py       # RAG corpus creation + knowledge base import
│   └── gcp_setup.sh              # One-time GCP API + IAM + bucket setup
├── tests/
│   └── test_selector.py          # Harness unit tests — no GCP credentials needed
├── Dockerfile                    # Multi-stage, non-root, Cloud Run optimised
├── cloudbuild.yaml               # CI/CD: test → build → push → deploy
├── pyproject.toml                # Dependencies including google-adk, mcp, httpx
├── .env.example                  # Environment variable template
└── session_handover.md           # Claude Code session handover document
```

---

## Known deployment notes

- **RAG Engine region**: New GCP projects must use `us-west1` — `us-central1` is restricted to allowlisted projects. The corpus `projects/1075013625841/locations/us-west1/ragCorpora/6917529027641081856` is created; only WHO reference ranges are indexed. Remaining 9 sources need re-import.
- **Cloud Run region**: `asia-south1` (Mumbai) for lowest latency from India.
- **Gemini model strings**: Always use `-preview` suffix. `gemini-3-pro-preview` is deprecated — use `gemini-3.1-pro-preview`.

---

## License

MIT — see [LICENSE](LICENSE)

---

## Author

Dr. Shibichakravarthy Kannan  
Consultant, Medical Genetics · Apollo Diagnostics, Hyderabad  
PhD (Biochemistry & Molecular Biology) · Postdoctoral Fellow, MD Anderson Cancer Center  
GitHub: [@musicofthings](https://github.com/musicofthings)
