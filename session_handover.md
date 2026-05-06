# GenomeSpeak — Claude Code Session Handover

**Project:** GenomeSpeak — AI-powered lab and genomics report interpreter  
**Repo:** https://github.com/musicofthings/genomespeak (branch: `main`)  
**Hackathon:** Google Cloud Rapid Agent Hackathon · Track 1: Build (Net-New Agents)  
**Deadline:** June 11, 2026  
**Author:** Dr. Shibichakravarthy Kannan · Apollo Diagnostics, Hyderabad  
**Last updated:** May 6, 2026

---

## 1. What this project is

A multi-agent AI system built entirely on Google ADK that explains complex lab and genomics reports in plain language. Patients upload a PDF report and ask questions — the agent answers in jargon-free English. Doctors get the same system in clinical detail mode with ACMG classifications and guideline citations. Live biomedical databases (PubMed, ClinVar, gnomAD, CPIC, OncoKB) are connected via MCP and REST APIs.

---

## 2. GCP infrastructure — current state

| Resource | Value | Status |
|---|---|---|
| GCP Project ID | `genomespeak` | ✅ Active, billing enabled |
| Project Number | `1075013625841` | — |
| RAG Corpus | `projects/1075013625841/locations/us-west1/ragCorpora/6917529027641081856` | ✅ Created |
| RAG Region | `us-west1` | ✅ (us-central1 blocked for new projects) |
| Cloud Run Region | `asia-south1` | ✅ Deployed — https://genomespeak-2j73ftnc7q-el.a.run.app |
| GCS Staging Bucket | `genomespeak-genomespeak-rag-staging` | ✅ Created |
| APIs enabled | aiplatform, run, cloudbuild, storage, firestore, artifactregistry, secretmanager | ✅ All enabled |
| Billing | Linked | ✅ Active |

### RAG corpus indexing status

| Knowledge source | Status |
|---|---|
| WHO reference ranges (CBC, chemistry, lipids, thyroid, LFT) | ✅ Indexed (staged via GCS) |
| ACMG variant classification criteria (PMC4544753) | ❌ Pending re-import |
| ACMG secondary findings v3.2 (PMC9748286) | ❌ Pending re-import |
| CPIC guidelines overview | ❌ Pending re-import |
| NCCN hereditary cancer (cancer.gov) | ❌ Pending re-import |
| Lynch syndrome MMR (NCBI books NBK1211) | ❌ Pending re-import |
| ClinVar variant significance docs | ❌ Pending re-import |
| OncoKB evidence levels | ❌ Pending re-import |
| NIPT interpretation (PMC6313310) | ❌ Pending re-import |
| MedlinePlus lab tests | ❌ Pending re-import |

**Fix:** Run this from Cloud Shell to re-import all pending sources:
```bash
python scripts/setup_rag_corpus.py \
  --project genomespeak \
  --location us-west1 \
  --corpus-resource projects/1075013625841/locations/us-west1/ragCorpora/6917529027641081856
```
The updated `setup_rag_corpus.py` now fetches web URLs, strips HTML, stages to GCS, then imports — the previous version tried to import URLs directly which Vertex AI RAG does not support.

---

## 3. Codebase map

```
genomespeak/
├── genomespeak/
│   ├── agent.py                  ← ADK root agent, DynamicAgentFactory, GenomeSpeakOrchestrator
│   ├── harness/
│   │   ├── models.py             ← Pydantic types: QueryProfile, ModelConfig, SelectionResult
│   │   ├── registry.py           ← 8 named Gemini model configs (pro_high, flash_medium, etc.)
│   │   ├── classifier.py         ← QueryClassifierAgent: Flash-Lite, LOW thinking, ~300ms
│   │   └── selector.py           ← ModelSelectorHarness: tier matrix + 5 override rules
│   └── tools/
│       ├── pdf_ingest.py         ← Native PDF ingestion: GCS Artifact → Gemini multimodal Part
│       └── mcp_registry.py       ← MCPToolset (PubMed) + FunctionTools (ClinVar, gnomAD, CPIC, OncoKB, NCBI Gene)
├── api/
│   └── main.py                   ← FastAPI: POST /upload, POST /chat (SSE), GET /session/{id}, GET /health
├── frontend/
│   └── index.html                ← Self-contained chat UI: drag-drop, patient/doctor toggle, SSE streaming
├── scripts/
│   ├── setup_rag_corpus.py       ← RAG setup script (use --corpus-resource to skip creation)
│   └── gcp_setup.sh              ← One-time GCP setup (IAM, APIs, buckets)
├── tests/
│   └── test_selector.py          ← 15+ unit tests for ModelSelectorHarness, no GCP needed
├── Dockerfile                    ← Multi-stage, non-root, Cloud Run optimised
├── cloudbuild.yaml               ← CI/CD: pytest → docker build → push → gcloud run deploy
└── pyproject.toml                ← deps: google-adk, mcp, httpx, fastapi, pydantic, vertexai
```

---

## 4. Key architectural decisions (do not change without reason)

### Gemini model strings
Always use these exact strings — the old `gemini-3-pro-preview` is deprecated:
```
gemini-3.1-pro-preview       ← genomics, oncology (TIER 3-4)
gemini-3-flash-preview        ← routine labs, parsing (TIER 2-3)
gemini-3.1-flash-lite-preview ← classifier, plain language (TIER 1)
```

### thinking_level parameter
ADK passes this via `GenerationConfig(thinking_config={"thinking_level": "HIGH|MEDIUM|LOW"})`.
HIGH is mandatory for genomics and oncology agents — never lower it for those agents.

### PDF ordering in multimodal content
In `pdf_ingest.py` and `agent.py`, the PDF `Part` must come BEFORE the text prompt in the content list: `[pdf_part, text_part]`. Gemini processes left-to-right — document context before the question improves grounding accuracy.

### RAG corpus region
The corpus is in `us-west1`. When querying it from agents in `us-central1`, pass the full resource name including the region. The SDK handles cross-region calls transparently.

### MCP transport
PubMed uses `StreamableHTTPConnectionParams` (not the older `SseServerParams`). The public endpoint `https://pubmed.caseyjhand.com/mcp` requires no API key. NCBI_API_KEY is for the FunctionTool wrappers (ClinVar, NCBI Gene).

### Git branch
Always `main`. Never `master`.

---

## 5. Immediate next tasks — in priority order

### TASK 1 — Re-import RAG knowledge sources (30 min)
**Why:** Only 1/10 knowledge sources indexed. ACMG/NCCN/CPIC/OncoKB are all missing.
**How:**
```bash
# In Cloud Shell
git clone https://github.com/musicofthings/genomespeak.git && cd genomespeak
pip install google-cloud-aiplatform python-dotenv --quiet
python scripts/setup_rag_corpus.py \
  --project genomespeak \
  --location us-west1 \
  --corpus-resource projects/1075013625841/locations/us-west1/ragCorpora/6917529027641081856
```
**Expected output:** `INFO Corpus ready — 10 files indexed`

---

### TASK 2 — Add .env.example values for NCBI/OncoKB (5 min)
**Why:** `mcp_registry.py` reads `NCBI_API_KEY`, `NCBI_EMAIL`, `ONCOKB_TOKEN` from env.
**How:** Update `.env` in Cloud Shell:
```bash
cat >> .env << 'EOF'
NCBI_API_KEY=your_ncbi_key_here
NCBI_EMAIL=shibi@apollodiagnostics.in
ONCOKB_TOKEN=
GENOMESPEAK_RAG_LOCATION=us-west1
EOF
```
NCBI key: https://www.ncbi.nlm.nih.gov/account/  
OncoKB token: https://www.oncokb.org/account/settings (optional, public tier works)

---

### TASK 3 — Deploy to Cloud Run (20 min)
**Why:** Need a live URL for the Devpost submission.
**How (from Cloud Shell):**
```bash
cd genomespeak
gcloud run deploy genomespeak \
  --source . \
  --region asia-south1 \
  --allow-unauthenticated \
  --memory 2Gi \
  --cpu 2 \
  --concurrency 80 \
  --min-instances 0 \
  --max-instances 10 \
  --set-env-vars GOOGLE_CLOUD_PROJECT=genomespeak \
  --set-env-vars GOOGLE_CLOUD_LOCATION=us-central1 \
  --set-env-vars GENOMESPEAK_RAG_CORPUS=projects/1075013625841/locations/us-west1/ragCorpora/6917529027641081856 \
  --set-env-vars GENOMESPEAK_RAG_LOCATION=us-west1 \
  --set-env-vars NCBI_API_KEY=YOUR_KEY \
  --set-env-vars NCBI_EMAIL=YOUR_EMAIL \
  --project genomespeak
```
**Expected output:** `Service URL: https://genomespeak-xxxx-el.a.run.app`  
Copy this URL — it goes into the Devpost submission as the hosted project URL.

---

### TASK 4 — Test end-to-end with a real PDF (30 min)
**Why:** Must verify the full agent pipeline before recording the demo video.
**How:**
1. Open the Cloud Run URL
2. Upload a de-identified BRCA panel report PDF (get one from Apollo lab or use a synthetic one)
3. Select Patient mode → type "What does this mean for me?"
4. Verify: streaming response, plain language, no jargon, model badge shows `3.1 Pro`
5. Switch to Doctor mode → type "Apply ACMG/AMP 2015 criteria to the reported variants"
6. Verify: technical classification, ACMG criteria listed, ClinVar/gnomAD citations

If agents fail to load, check Cloud Run logs:
```bash
gcloud run services logs read genomespeak --region asia-south1 --limit 50
```

---

### TASK 5 — Record demo video (45 min)
**Why:** Required for Devpost submission. Judges watch this first.
**Script (3 minutes):**
- 0:00–0:30 — Problem statement: show a real BRCA report PDF (blank out patient name). "This is what millions of patients receive. They don't understand it."
- 0:30–1:30 — Patient mode demo: upload the report, ask "Am I going to get cancer?", show the warm plain-language response streaming in
- 1:30–2:15 — Doctor mode demo: same report, ask "Classify this variant under ACMG/AMP 2015", show technical classification with ClinVar evidence
- 2:15–2:45 — Architecture slide (30 sec): show the ADK multi-agent diagram, call out MCPToolset connecting to PubMed, ClinVar, gnomAD
- 2:45–3:00 — "Built on 100% Google Cloud ADK — deployable to any Apollo Diagnostics facility in India"

**Tools:** OBS Studio or Loom. 1080p. No face required — screen recording is fine.

---

### TASK 6 — Devpost submission (30 min)
**Required fields:**
- Project name: `GenomeSpeak`
- Tagline: `Every patient deserves to understand their own lab report`
- Description: (see README — copy the problem, architecture, MCP integration sections)
- Track: Track 1 — Build (Net-New Agents)
- Demo URL: Cloud Run URL from Task 3
- GitHub: https://github.com/musicofthings/genomespeak (must be public)
- Video: uploaded from Task 5
- Technologies: Google ADK, Gemini 3.1 Pro, MCPToolset, Vertex AI RAG Engine, Cloud Run, PubMed MCP, ClinVar, gnomAD, CPIC, OncoKB

**MCP framing sentence for description:**
> GenomeSpeak uses ADK's MCPToolset with Streamable HTTP transport to connect Gemini agents to a live PubMed MCP server, plus FunctionTool wrappers for ClinVar, gnomAD, CPIC, and OncoKB — giving agents real-time access to the world's biomedical knowledge base. Every tool call is authenticated via GCP IAM.

---

## 6. Known issues and fixes

| Issue | Root cause | Fix |
|---|---|---|
| `setup_rag_corpus.py` NameError on `WHO_REFERENCE_RANGES_TEXT` | String defined after list that references it | Fixed — string now defined at line 46 |
| RAG corpus creation fails in `us-central1` | Spanner capacity restricted for new projects | Fixed — use `--location us-west1` |
| Web URL imports fail with "path must be GCS uri" | Vertex AI RAG doesn't accept direct URLs | Fixed — `import_web_url` now fetches, strips HTML, stages to GCS first |
| PowerShell line continuation uses backtick not backslash | Windows PowerShell syntax | Use `` ` `` not `\` for line continuation |
| `gemini-3-pro-preview` deprecated | Google deprecated March 26, 2026 | Use `gemini-3.1-pro-preview` |
| `HARM_CATEGORY_MEDICAL` not a valid HarmCategory | Not a standard safety category | Fixed in `classifier.py` — use `HARM_CATEGORY_HARASSMENT` |

---

## 7. Files to commit after this session

The following files were created or modified in this session and need to be committed:

```bash
git add genomespeak/tools/mcp_registry.py   # NEW — full MCP tool registry
git add genomespeak/agent.py                 # MODIFIED — DynamicAgentFactory wires MCP tools
git add pyproject.toml                       # MODIFIED — added mcp>=1.0.0 dependency
git add README.md                            # MODIFIED — full rewrite with MCP docs
git add session_handover.md                  # NEW — this file
git commit -m "feat: MCP tool integration — PubMed, ClinVar, gnomAD, CPIC, OncoKB, NCBI Gene"
git push origin main
```

---

## 8. Environment variables reference

```bash
# Required
GOOGLE_CLOUD_PROJECT=genomespeak
GOOGLE_CLOUD_LOCATION=us-central1
GENOMESPEAK_RAG_CORPUS=projects/1075013625841/locations/us-west1/ragCorpora/6917529027641081856
GENOMESPEAK_RAG_LOCATION=us-west1

# Strongly recommended
NCBI_API_KEY=your_key          # 10 req/s vs 3 req/s without
NCBI_EMAIL=your@email.com      # NCBI usage policy requirement

# Optional
ONCOKB_TOKEN=your_token        # Public tier works without this
GENOMESPEAK_MAX_PDF_MB=20
GENOMESPEAK_LOG_LEVEL=INFO
PORT=8080
```

---

## 9. Claude Code tips for this repo

- **All Python must target 3.11+** — use `match` statements, `X | Y` union types, `tomllib` etc. freely.
- **Never use `master` as branch name** — always `main`.
- **Async everywhere in agents** — ADK is async-first. Any new tool functions should be `async def`.
- **Test with `pytest tests/ -v`** — the selector tests need no GCP credentials and run in <5 seconds.
- **Vertex AI calls need ADC** — `gcloud auth application-default login` must be run before any local test that hits GCP.
- **RAG corpus re-import** — always use `--corpus-resource` flag to avoid creating a new corpus accidentally.
- **Model strings are version-sensitive** — double check against `genomespeak/harness/registry.py` before using a model string anywhere.

---

## 10. Contacts and accounts

- GCP Console: https://console.cloud.google.com/home/dashboard?project=genomespeak
- Cloud Shell: https://shell.cloud.google.com/?project=genomespeak
- Hackathon: https://rapid-agent.devpost.com/
- GitHub: https://github.com/musicofthings/genomespeak
- NCBI account: register at https://www.ncbi.nlm.nih.gov/account/
- OncoKB: https://www.oncokb.org/account/settings
