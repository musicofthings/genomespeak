# 🧬 GenomeSpeak

**AI-powered lab and genomics report interpreter — plain language for everyone.**

> Built for the Google Cloud Rapid Agent Hackathon · May–June 2026
> Stack: Google ADK · Gemini 3.1 Pro · Vertex AI RAG Engine · Cloud Run

---

## The problem

Every year, hundreds of millions of patients receive lab and genomics reports they cannot understand. A BRCA2 pathogenic variant report, a CBC with seven abnormal values, a pharmacogenomics panel — these are written for clinicians, not the people whose lives they affect. Patients either ignore them, spiral into anxiety from Google searches, or wait weeks to ask their doctor a question that could have been answered immediately.

**GenomeSpeak closes this gap.**

---

## What it does

Upload any medical report PDF. Ask a question in plain English. Get an answer you actually understand.

| Report type | Examples |
|---|---|
| Routine labs | CBC, CMP, lipid panel, HbA1c, thyroid (TSH/T4), LFT |
| Genomics germline | WES, WGS, hereditary cancer panels, BRCA1/2, Lynch syndrome |
| Oncology somatic | Tumor NGS, ctDNA, TMB, MSI, somatic mutation profiling |
| Pharmacogenomics | CYP2D6, CYP2C19, DPYD, TPMT, SLCO1B1 panels |
| Prenatal | NIPT, karyotype, chromosomal microarray |

Two modes:
- **Patient mode** — zero jargon, warm analogies, emotional sensitivity
- **Doctor mode** — full clinical detail, ACMG classifications, NCCN guidelines

---

## Architecture

```
User query + PDF upload
      │
      ▼
QueryClassifierAgent          gemini-3.1-flash-lite  LOW    ~300ms
(classify report type + query complexity)
      │ QueryProfile
      ▼
ModelSelectorHarness          pure Python            0ms
(tier matrix + 5 override rules → ModelConfig)
      │ SelectionResult
      ▼
DynamicAgentFactory
      │
      ├─ GenomicsAgent         gemini-3.1-pro         HIGH   ACMG/RAG
      ├─ OncologyAgent         gemini-3.1-pro         HIGH   Search grounding
      ├─ RoutineLabAgent       gemini-3-flash         MEDIUM Code execution
      └─ PharmacogenomicsAgent gemini-3.1-pro/flash   HIGH
            │ Technical interpretation
            ▼
      PlainLanguageAgent       gemini-3.1-flash-lite  LOW    Jargon → plain English
            │
            ▼
      Streamed SSE response → React frontend
```

### Adaptive model selection

The harness selects the right Gemini model for every query based on four factors:

| Factor | Signal | Effect |
|---|---|---|
| Complexity tier | Query analysis (TIER 1-4) | Flash-Lite → Flash → Pro |
| Report type | PDF category | Genomics/oncology → Pro minimum |
| Follow-up | Session has prior report | One tier downgrade (cost saving) |
| Override rules | 5 hard safety rules | Genomics never below Flash+MEDIUM |

This means a patient asking "what does hemoglobin mean?" costs ~$0.001 (Flash-Lite, LOW), while "classify this BRCA2 c.5946delT variant under ACMG criteria" costs ~$0.08 (Pro, HIGH). The system self-optimises.

### Native PDF ingestion

Gemini 3.1 Pro reads PDFs natively via its 1M token multimodal context window. No OCR preprocessing, no Document AI for standard reports. The PDF is stored as an ADK Artifact in GCS and loaded as a multimodal `Part` directly into the model's content list — the model processes the full document before seeing the question.

### RAG Engine knowledge bases

The Vertex AI RAG Engine corpus contains:
- ACMG/AMP 2015 variant classification criteria
- ClinGen gene-disease validity curations
- WHO/IFCC reference ranges (CBC, chemistry, lipids, thyroid, LFT)
- NCCN hereditary cancer guidelines (public summaries)
- CPIC pharmacogenomics guidelines
- OncoKB evidence levels
- MedlinePlus patient-facing disease summaries

---

## Google Cloud services used (100% Google-only)

| Service | Purpose |
|---|---|
| **Google ADK** | Multi-agent orchestration framework |
| **Gemini 3.1 Pro** (`gemini-3.1-pro-preview`) | Expert clinical reasoning |
| **Gemini 3 Flash** (`gemini-3-flash-preview`) | Routine lab interpretation |
| **Gemini 3.1 Flash-Lite** (`gemini-3.1-flash-lite-preview`) | Classification + plain language |
| **Vertex AI RAG Engine** | Clinical knowledge base retrieval |
| **Google Search Grounding** | Current FDA approvals, guidelines |
| **Code Execution** | eGFR, LDL, z-score calculations |
| **ADK Artifact Service** | PDF storage (GCS-backed) |
| **ADK Memory Bank** | Cross-session patient history |
| **Cloud Run** | Backend deployment |
| **Cloud Build** | CI/CD pipeline |
| **Firestore** | Session persistence |
| **Artifact Registry** | Container image storage |
| **Secret Manager** | RAG corpus resource names |

---

## Quick start

### Prerequisites
- Python 3.11+
- Google Cloud SDK (`gcloud`)
- GCP project with billing enabled

### 1. Clone and install
```bash
git clone https://github.com/musicofthings/genomespeak.git
cd genomespeak
pip install -e ".[dev]"
```

### 2. GCP setup
```bash
./scripts/gcp_setup.sh YOUR_PROJECT_ID
```

### 3. Configure environment
```bash
cp .env.example .env
# Edit .env with your project ID
```

### 4. Set up RAG corpus
```bash
python scripts/setup_rag_corpus.py --project YOUR_PROJECT_ID
# Copy the output corpus resource name to .env
```

### 5. Run locally
```bash
# Start API
python -m api.main

# Open frontend
open frontend/index.html
```

### 6. Run tests
```bash
pytest tests/ -v
```

### 7. Deploy to Cloud Run
```bash
gcloud builds submit --config=cloudbuild.yaml
```

---

## Project structure

```
genomespeak/
├── genomespeak/
│   ├── agent.py                # ADK root agent + orchestrator
│   ├── harness/
│   │   ├── models.py           # Pydantic types (QueryProfile, ModelConfig)
│   │   ├── registry.py         # Gemini model registry (8 named configs)
│   │   ├── classifier.py       # QueryClassifierAgent (Flash-Lite, LOW)
│   │   └── selector.py         # ModelSelectorHarness (tier matrix + overrides)
│   └── tools/
│       └── pdf_ingest.py       # Native PDF → ADK Artifact → Gemini Part
├── api/
│   └── main.py                 # FastAPI: /upload, /chat (SSE), /session
├── frontend/
│   └── index.html              # Single-file React-like UI with streaming SSE
├── scripts/
│   ├── setup_rag_corpus.py     # One-time RAG corpus setup
│   └── gcp_setup.sh            # One-time GCP project setup
├── tests/
│   └── test_selector.py        # 15 unit tests, no GCP credentials needed
├── Dockerfile                  # Multi-stage, non-root, Cloud Run optimised
├── cloudbuild.yaml             # CI/CD: test → build → push → deploy
├── pyproject.toml
└── .env.example
```

---

## License

MIT License — see LICENSE file.

---

## Author

Dr. Shibichakravarthy Kannan  
Consultant, Medical Genetics · Apollo Diagnostics, Hyderabad  
GitHub: [@musicofthings](https://github.com/musicofthings)
