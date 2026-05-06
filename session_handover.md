# Session Handover
_Generated: 2026-05-06T16:33:04Z_
_Branch: main_
_Triggered by: user request (/handover)_

---

## 🎯 Active Task
**What we're building/fixing:**
GenomeSpeak — a Google ADK multi-agent system that interprets lab and genomics reports in plain language for patients and doctors. The agent pipeline (classify → model-select → specialist → plain-language rewrite) is fully implemented and the Cloud Run deployment is live. Current phase is completing the RAG knowledge base, doing end-to-end testing with real PDFs, and recording the Devpost demo video.

**Phase:** Post-implementation — Demo Prep & Submission
**Progress:** ~70% complete — core agent pipeline ✅, deployment ✅, RAG corpus partially indexed (1/10 sources), demo video ❌, Devpost submission ❌

---

## ✅ Completed This Session
- Created `CLAUDE.md` at project root with commands, architecture overview, type relationships, API layer, PDF handling, MCP tool wiring, and critical env notes
- Ran `/context-health` — identified two issues: missing CLAUDE.md (now fixed) and `active_task = "unknown"` in state.json (being fixed by this handover)
- Confirmed all 7/7 hooks wired, 14/14 hook scripts executable, 8/8 skills present

## ✅ Completed in Prior Sessions
- Full ADK multi-agent pipeline: `QueryClassifierAgent` → `ModelSelectorHarness` → `DynamicAgentFactory` → `GenomeSpeakOrchestrator`
- 15+ unit tests for `ModelSelectorHarness` (no GCP credentials needed)
- FastAPI backend with SSE streaming (`/upload`, `/chat`, `/session`, `/health`)
- Native PDF ingestion via ADK Artifacts + Gemini multimodal Part
- MCP tool registry: PubMed (MCPToolset), ClinVar, gnomAD, CPIC, OncoKB, NCBI Gene (FunctionTools)
- Multi-stage Dockerfile (non-root, Cloud Run optimised)
- Cloud Run deployed: `https://genomespeak-2j73ftnc7q-el.a.run.app` (asia-south1)
- RAG corpus created: `projects/1075013625841/locations/us-west1/ragCorpora/6917529027641081856`
- WHO reference ranges indexed (1/10 knowledge sources)
- Fixed series of Vertex AI issues: `GOOGLE_GENAI_USE_VERTEXAI=1`, `google.genai.types` imports, global location for Gemini 3.x preview models, model string deprecations

---

## 🔄 In Progress (Exact Resume Point)
**File:** `scripts/setup_rag_corpus.py`
**Function/Section:** RAG knowledge source import
**What was happening:** Script was updated to fetch web URLs, strip HTML, stage to GCS, then import (Vertex AI RAG rejects direct URLs). Only WHO reference ranges are currently indexed; 9 remaining sources pending.
**Next immediate action:** Run the re-import command from Cloud Shell (see Task 1 below)

---

## 🚧 Blockers & Known Issues
- **RAG corpus 90% empty**: ACMG, NCCN, CPIC, OncoKB, ClinVar docs not yet indexed — agents will lack static clinical knowledge until Task 1 is complete
- **`mcp_registry.py` not yet committed**: file exists locally but `git status` shows `.claude/` and `CLAUDE.md` as untracked — verify the full tool registry is committed
- **No `.env` file in repo**: developers need to copy `.env.example` and fill in `NCBI_API_KEY`, `ONCOKB_TOKEN`; the example file should exist but verify it's present
- **RAG region vs Vertex AI region mismatch**: RAG corpus is in `us-west1`, agents run in `us-central1`/`global` — this is handled correctly via full resource name but is a footgun if region env vars are changed

---

## 📋 Remaining Work
1. **Re-import 9 RAG knowledge sources** (30 min) — run `setup_rag_corpus.py` with `--corpus-resource` flag from Cloud Shell
2. **Set NCBI_API_KEY + NCBI_EMAIL in Cloud Run** — update env vars on the deployed service
3. **End-to-end test with real PDF** (30 min) — BRCA panel or routine CBC PDF, both patient and doctor modes, verify streaming + model badge
4. **Record demo video** (45 min) — 3-minute screen recording per script below
5. **Submit to Devpost** (30 min) — project is `https://rapid-agent.devpost.com/`, deadline June 11 2026

---

## 🏗 Architecture Decisions Made This Session
| Decision | Rationale | Date |
|----------|-----------|------|
| `GOOGLE_GENAI_USE_VERTEXAI=1` set in `agent.py` via `os.environ.setdefault` | ADK routes to Vertex AI, not Gemini API; avoids API key requirement | 2026-05-06 |
| `GOOGLE_CLOUD_LOCATION=global` for Gemini 3.x preview models | Gemini 3.x preview models only available on global endpoint in Vertex AI | 2026-05-06 |
| `google.genai.types` for `Content`/`Part` in runner | `google.adk.types` does not exist; correct import is `google.genai.types` | 2026-05-06 |
| CLAUDE.md created at project root | Enables context-engineering-kit health checks; documents architecture for future sessions | 2026-05-06 |

---

## 🔧 Commands to Resume
```bash
# Pull latest
git pull origin main

# Install
pip install -e ".[dev]"

# Run tests (no GCP needed)
pytest tests/ -v

# Re-import RAG sources (from Cloud Shell)
python scripts/setup_rag_corpus.py \
  --project genomespeak \
  --location us-west1 \
  --corpus-resource projects/1075013625841/locations/us-west1/ragCorpora/6917529027641081856

# Run API locally
python -m api.main

# Check Cloud Run logs
gcloud run services logs read genomespeak --region asia-south1 --limit 50
```

---

## 📁 Key Files Modified
| File | What changed |
|------|--------------|
| `CLAUDE.md` | **NEW** — commands, full architecture, env notes, ADK entry point |
| `genomespeak/agent.py` | `GOOGLE_GENAI_USE_VERTEXAI=1` env default; `google.genai.types` imports; global location |
| `genomespeak/harness/registry.py` | Model strings updated to confirmed Vertex AI endpoints |
| `genomespeak/harness/classifier.py` | `HARM_CATEGORY_MEDICAL` → `HARM_CATEGORY_HARASSMENT` fix |
| `scripts/setup_rag_corpus.py` | Web URL import: now fetches → strips HTML → stages to GCS → imports |
| `Dockerfile` | Multi-stage venv build; `packaging` added to deps |

---

## ⚠️ Critical Rules for This Project
- **Model strings**: always use `-preview` suffix: `gemini-3.1-pro-preview`, `gemini-3-flash-preview`, `gemini-3.1-flash-lite-preview`. Confirm against `harness/registry.py` before changing anything.
- **`GOOGLE_GENAI_USE_VERTEXAI=1`** must be set in every environment (Cloud Run, local, CI). It is defaulted in `agent.py` but must be explicit in Cloud Run env vars.
- **Vertex AI location**: use `global` for `GOOGLE_CLOUD_LOCATION` — Gemini 3.x preview models require the global endpoint.
- **RAG corpus region**: always `us-west1`. Never change. New GCP projects cannot use `us-central1` for RAG Engine.
- **PDF Part ordering**: `[pdf_part, text_part]` — PDF must come first in the multimodal content list.
- **ADK is async-first**: all new tool functions must be `async def`. The ADK `Runner` is async.
- **Never use `master`**: branch is always `main`.

---

## 🧬 Bioinformatics Context
- Reference genome: GRCh38 (assumed for all variant interpretation)
- Pipeline stage: Report interpretation (upstream sequencing already complete — user uploads finished report PDF)
- ACMG criteria in use: ACMG/AMP 2015 (PS1-PS4, PM1-PM6, PP1-PP5, BA1, BS1-BS4, BP1-BP7) via `GenomicsAgent` system prompt
- Report types handled: germline WES/WGS/panels, somatic tumor NGS/ctDNA, PGx panels, routine labs, prenatal NIPT/karyotype
- Key external databases: ClinVar (pathogenicity), gnomAD (population freq → BA1/BS1/PM2), CPIC (PGx guidelines), OncoKB (therapeutic tiers), PubMed (literature)

---

## 📋 GCP Infrastructure State
| Resource | Value | Status |
|---|---|---|
| GCP Project ID | `genomespeak` | ✅ Active, billing enabled |
| Project Number | `1075013625841` | — |
| RAG Corpus | `projects/1075013625841/locations/us-west1/ragCorpora/6917529027641081856` | ✅ Created, 1/10 sources indexed |
| Cloud Run URL | `https://genomespeak-2j73ftnc7q-el.a.run.app` | ✅ Live (asia-south1) |
| GCS Staging Bucket | `genomespeak-genomespeak-rag-staging` | ✅ Created |

### RAG corpus indexing status
| Knowledge source | Status |
|---|---|
| WHO reference ranges | ✅ Indexed |
| ACMG variant classification criteria (PMC4544753) | ❌ Pending |
| ACMG secondary findings v3.2 (PMC9748286) | ❌ Pending |
| CPIC guidelines overview | ❌ Pending |
| NCCN hereditary cancer (cancer.gov) | ❌ Pending |
| Lynch syndrome MMR (NCBI books NBK1211) | ❌ Pending |
| ClinVar variant significance docs | ❌ Pending |
| OncoKB evidence levels | ❌ Pending |
| NIPT interpretation (PMC6313310) | ❌ Pending |
| MedlinePlus lab tests | ❌ Pending |

---

## 🎬 Demo Video Script (3 min)
- **0:00–0:30** — Problem: show real BRCA report PDF (name blanked). "This is what millions of patients receive. They don't understand it."
- **0:30–1:30** — Patient mode: upload report, ask "Am I going to get cancer?", show warm streaming response
- **1:30–2:15** — Doctor mode: same report, ask "Classify this variant under ACMG/AMP 2015", show technical classification with ClinVar/gnomAD citations
- **2:15–2:45** — Architecture slide: ADK multi-agent diagram, call out MCPToolset → PubMed/ClinVar/gnomAD
- **2:45–3:00** — "Built on 100% Google Cloud ADK — deployable to any diagnostics facility"

---
_Read this file at the start of every session. Update it with /handover before compacting._
