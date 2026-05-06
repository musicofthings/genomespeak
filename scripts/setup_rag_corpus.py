#!/usr/bin/env python3
"""
scripts/setup_rag_corpus.py
One-time setup: creates and populates the Vertex AI RAG Engine corpus
for GenomeSpeak with all clinical knowledge bases.

Usage:
    python scripts/setup_rag_corpus.py --project YOUR_PROJECT_ID --location us-west1

What it does:
    1. Creates a RAG corpus (or re-uses an existing one with --corpus-resource)
    2. Fetches each web source, strips HTML, stages to GCS, then imports
    3. Inline text (WHO reference ranges) is staged to GCS then imported
    4. Outputs the corpus resource name
"""

from __future__ import annotations

import argparse
import logging
import re
import time
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# WHO reference ranges — defined before KNOWLEDGE_SOURCES which references it
# ---------------------------------------------------------------------------

WHO_REFERENCE_RANGES_TEXT = """
# WHO / IFCC Reference Ranges for Common Laboratory Tests

## Complete Blood Count (CBC)

### Haemoglobin (g/dL)
Adult male: 13.0 – 17.0
Adult female: 12.0 – 15.0
Children 6-12y: 11.5 – 15.5
Pregnancy: 11.0 – 14.0
Severe anaemia (WHO): < 8.0 g/dL
Moderate anaemia: 8.0 – 10.9
Mild anaemia: 11.0 – 11.9 (women), 11.0 – 12.9 (men)

### White Blood Cell Count (×10⁹/L)
Adults: 4.0 – 11.0
Neutrophils (absolute): 1.8 – 7.5
Lymphocytes (absolute): 1.0 – 4.8
Monocytes (absolute): 0.2 – 1.0
Eosinophils (absolute): 0.02 – 0.5
Basophils (absolute): 0.02 – 0.1

### Platelet Count (×10⁹/L)
Adults: 150 – 400
Thrombocytopenia severe: < 50
Thrombocytopenia moderate: 50 – 100

### MCV (fL) — Mean Corpuscular Volume
Adults: 80 – 100
Microcytic: < 80 (iron deficiency, thalassaemia)
Macrocytic: > 100 (B12/folate deficiency, liver disease)

## Metabolic Panel

### Glucose (mmol/L)
Fasting normal: 3.9 – 5.6
Impaired fasting glucose (IFG): 5.6 – 6.9
Diabetes mellitus: ≥ 7.0
Random diabetes threshold: ≥ 11.1
Hypoglycaemia: < 3.9

### HbA1c (%)
Normal: < 5.7
Pre-diabetes: 5.7 – 6.4
Diabetes: ≥ 6.5
Well-controlled diabetes target: < 7.0
High risk: > 9.0

### Creatinine (μmol/L)
Adult male: 62 – 115
Adult female: 44 – 97
eGFR CKD staging:
  G1 (normal): ≥ 90 mL/min/1.73m²
  G2 (mildly reduced): 60 – 89
  G3a: 45 – 59
  G3b: 30 – 44
  G4 (severely reduced): 15 – 29
  G5 (kidney failure): < 15

### Sodium (mmol/L)
Normal: 136 – 145
Hyponatraemia mild: 130 – 135
Hyponatraemia moderate: 120 – 129
Hyponatraemia severe: < 120
Hypernatraemia: > 145

### Potassium (mmol/L)
Normal: 3.5 – 5.0
Hypokalaemia severe: < 3.0
Hyperkalaemia dangerous: > 6.0

## Lipid Panel

### Total Cholesterol (mmol/L)
Desirable: < 5.2
Borderline high: 5.2 – 6.2
High: > 6.2

### LDL Cholesterol (mmol/L)
Optimal: < 2.6
Near optimal: 2.6 – 3.3
Borderline high: 3.4 – 4.1
High: 4.1 – 4.9
Very high: ≥ 4.9
Target for high CV risk: < 1.8

### HDL Cholesterol (mmol/L)
Low (risk factor): < 1.0 (men), < 1.3 (women)
Protective: ≥ 1.6

### Triglycerides (mmol/L)
Normal: < 1.7
Borderline high: 1.7 – 2.2
High: 2.3 – 5.6
Very high (pancreatitis risk): > 5.6

## Thyroid Function

### TSH (mIU/L)
Normal: 0.4 – 4.0
Subclinical hypothyroidism: 4.0 – 10.0
Overt hypothyroidism: > 10.0
Subclinical hyperthyroidism: 0.1 – 0.4
Overt hyperthyroidism: < 0.1

### Free T4 (pmol/L)
Normal: 12 – 22
Low (hypothyroid): < 12
High (hyperthyroid): > 22

## Liver Function Tests

### ALT (U/L)
Normal male: 7 – 56
Normal female: 7 – 45
Mild elevation: 1–3× ULN
Moderate: 3–10× ULN
Severe (acute hepatitis): > 10× ULN

### AST (U/L)
Normal: 10 – 40
AST:ALT > 2:1 suggests alcoholic liver disease

### Bilirubin Total (μmol/L)
Normal: 5 – 21
Jaundice visible: > 34
"""


# ---------------------------------------------------------------------------
# Knowledge sources
# All web_url entries are fetched → HTML stripped → staged to GCS → imported.
# rag.import_files() does NOT accept raw HTTP URLs — only gs:// or Drive URLs.
# ---------------------------------------------------------------------------

KNOWLEDGE_SOURCES = [
    {
        "name": "acmg_variant_classification",
        "description": "ACMG/AMP 2015 variant classification criteria",
        "type": "web_url",
        "uri": "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC4544753/",
        "chunk_size": 512,
        "chunk_overlap": 100,
    },
    {
        "name": "acmg_secondary_findings_v3",
        "description": "ACMG Secondary Findings v3.2 — actionable genes list",
        "type": "web_url",
        "uri": "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC9748286/",
        "chunk_size": 512,
        "chunk_overlap": 100,
    },
    {
        "name": "cpic_guidelines_overview",
        "description": "CPIC pharmacogenomics guidelines — gene-drug pairs, phenotypes, dosing recommendations",
        "type": "cpic_api",
        "chunk_size": 512,
        "chunk_overlap": 100,
    },
    {
        "name": "who_reference_ranges",
        "description": "WHO and IFCC reference intervals for CBC, metabolic panel, lipids, thyroid",
        "type": "inline_text",
        "content": WHO_REFERENCE_RANGES_TEXT,
        "chunk_size": 256,
        "chunk_overlap": 50,
    },
    {
        "name": "nccn_hereditary_cancer_overview",
        "description": "NCCN BRCA hereditary cancer risk management guidelines",
        "type": "web_url",
        "uri": "https://www.cancer.gov/about-cancer/causes-prevention/genetics/brca-fact-sheet",
        "chunk_size": 512,
        "chunk_overlap": 100,
    },
    {
        "name": "lynch_syndrome_mmr",
        "description": "Lynch syndrome — MLH1, MSH2, MSH6, PMS2, EPCAM — clinical management",
        "type": "web_url",
        "uri": "https://www.ncbi.nlm.nih.gov/books/NBK1211/",
        "chunk_size": 512,
        "chunk_overlap": 100,
    },
    {
        "name": "clinvar_variant_summaries",
        "description": "ClinVar variant classification summary — pathogenicity definitions",
        "type": "web_url",
        "uri": "https://www.ncbi.nlm.nih.gov/clinvar/docs/clinsig/",
        "chunk_size": 512,
        "chunk_overlap": 100,
    },
    {
        "name": "oncokb_evidence_levels",
        "description": "OncoKB therapeutic evidence levels — Levels 1-4, R1, R2",
        "type": "web_url",
        "uri": "https://www.oncokb.org/levels",
        "chunk_size": 512,
        "chunk_overlap": 100,
    },
    {
        "name": "nipt_interpretation",
        "description": "NIPT result interpretation — trisomies, sex chromosome aneuploidies",
        "type": "web_url",
        "uri": "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC6313310/",
        "chunk_size": 512,
        "chunk_overlap": 100,
    },
    {
        "name": "medlineplus_common_lab_tests",
        "description": "MedlinePlus patient-facing explanations of common lab tests",
        "type": "web_url",
        "uri": "https://medlineplus.gov/lab-tests/",
        "chunk_size": 256,
        "chunk_overlap": 50,
    },
]


# ---------------------------------------------------------------------------
# HTML → plain text
# ---------------------------------------------------------------------------

class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self._skip = False
        self.chunks: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "nav", "footer", "header"):
            self._skip = True

    def handle_endtag(self, tag):
        if tag in ("script", "style", "nav", "footer", "header"):
            self._skip = False

    def handle_data(self, data):
        if not self._skip:
            stripped = data.strip()
            if stripped:
                self.chunks.append(stripped)


def _html_to_text(html: str) -> str:
    extractor = _TextExtractor()
    extractor.feed(html)
    text = " ".join(extractor.chunks)
    # Collapse runs of whitespace
    return re.sub(r"\s{2,}", " ", text).strip()


def _fetch_url(url: str) -> str:
    """Fetch a URL and return plain text (HTML stripped)."""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "GenomeSpeak/1.0 (genomics-hackathon)"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
        encoding = resp.headers.get_content_charset("utf-8")
        html = raw.decode(encoding, errors="replace")
    return _html_to_text(html)


def _fetch_cpic_api() -> str:
    """
    Pull CPIC data from the public JSON API (no JS rendering needed).
    Combines /guideline and /pair endpoints into structured plain text.
    API docs: https://api.cpicpgx.org/
    """
    import json

    def _get(path: str) -> list:
        url = f"https://api.cpicpgx.org/v1{path}"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())

    lines: list[str] = ["# CPIC Pharmacogenomics Guidelines\n"]

    # --- Guidelines list ---
    logger.info("  Fetching CPIC /guideline ...")
    try:
        guidelines = _get("/guideline?select=name,genes,drugs,url,pharmgkbId&limit=100")
        lines.append("## Published Guidelines\n")
        for g in guidelines:
            genes = ", ".join(g.get("genes") or [])
            drugs = ", ".join(g.get("drugs") or [])
            lines.append(f"### {g.get('name', '')}")
            lines.append(f"Genes: {genes}")
            lines.append(f"Drugs: {drugs}")
            lines.append(f"URL: {g.get('url', '')}\n")
    except Exception as exc:
        logger.warning("  CPIC /guideline fetch failed: %s", exc)

    # --- Gene-drug pairs with recommendations ---
    logger.info("  Fetching CPIC /pair ...")
    try:
        pairs = _get(
            "/pair?select=genesymbol,drugname,guidelinename,cpicstatus,"
            "pgkbcalevel,citations&limit=300"
        )
        lines.append("## Gene-Drug Pairs and Evidence Levels\n")
        for p in pairs:
            lines.append(
                f"Gene: {p.get('genesymbol', '')} | "
                f"Drug: {p.get('drugname', '')} | "
                f"Guideline: {p.get('guidelinename', '')} | "
                f"CPIC Level: {p.get('cpicstatus', '')} | "
                f"PharmGKB Level: {p.get('pgkbcalevel', '')}"
            )
    except Exception as exc:
        logger.warning("  CPIC /pair fetch failed: %s", exc)

    # --- Allele function definitions for key genes ---
    for gene in ("CYP2D6", "CYP2C19", "DPYD", "TPMT", "SLCO1B1", "CYP2C9"):
        logger.info("  Fetching CPIC /gene/%s ...", gene)
        try:
            data = _get(f"/gene/{gene}?select=symbol,functionalalleles,normalalleles,nofunction")
            lines.append(f"\n## {gene} Allele Function Data")
            lines.append(str(data))
        except Exception:
            pass

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# GCS staging helper — shared by both import paths
# ---------------------------------------------------------------------------

def _stage_to_gcs(
    text: str,
    blob_name: str,
    project_id: str,
    bucket_name: str,
    location: str,
) -> str:
    """Upload text to GCS and return the gs:// URI."""
    from google.cloud import storage

    client = storage.Client(project=project_id)
    bucket = client.bucket(bucket_name)
    if not bucket.exists():
        bucket.create(location=location)
        logger.info("  Created staging bucket: gs://%s", bucket_name)

    bucket.blob(blob_name).upload_from_string(text, content_type="text/plain; charset=utf-8")
    gcs_uri = f"gs://{bucket_name}/{blob_name}"
    logger.info("  Staged %d chars → %s", len(text), gcs_uri)
    return gcs_uri


# ---------------------------------------------------------------------------
# Per-source import functions
# ---------------------------------------------------------------------------

def import_source(
    corpus_name: str,
    source: dict,
    project_id: str,
    bucket_name: str,
    location: str,
) -> None:
    """Dispatch a single source to the correct import path."""
    from vertexai.preview import rag

    name = source["name"]

    if source["type"] == "web_url":
        logger.info("Fetching web URL: %s → %s", name, source["uri"])
        try:
            text = _fetch_url(source["uri"])
        except Exception as exc:
            logger.warning("  Fetch failed for %s: %s — skipping", name, exc)
            return
        if len(text) < 200:
            logger.warning("  Page too short (%d chars) — skipping %s", len(text), name)
            return
        content = text

    elif source["type"] == "inline_text":
        logger.info("Staging inline text: %s", name)
        content = source["content"]

    elif source["type"] == "cpic_api":
        logger.info("Fetching CPIC API: %s", name)
        try:
            content = _fetch_cpic_api()
        except Exception as exc:
            logger.warning("  CPIC API fetch failed: %s — skipping", exc)
            return
        if len(content) < 200:
            logger.warning("  CPIC API returned too little content — skipping")
            return

    else:
        logger.warning("Unknown source type '%s' for %s", source["type"], name)
        return

    blob_name = f"knowledge/{name}.txt"
    gcs_uri = _stage_to_gcs(content, blob_name, project_id, bucket_name, location)

    try:
        rag.import_files(
            corpus_name=corpus_name,
            paths=[gcs_uri],
            chunk_size=source.get("chunk_size", 512),
            chunk_overlap=source.get("chunk_overlap", 100),
            max_embedding_requests_per_min=900,
        )
        logger.info("  Import submitted: %s", name)
    except Exception as exc:
        logger.warning("  Import failed for %s: %s", name, exc)


# ---------------------------------------------------------------------------
# Corpus creation
# ---------------------------------------------------------------------------

def create_rag_corpus(project_id: str, location: str, display_name: str) -> str:
    from vertexai.preview import rag

    logger.info("Creating RAG corpus: %s", display_name)
    embedding_config = rag.EmbeddingModelConfig(
        publisher_model="publishers/google/models/text-embedding-005"
    )
    corpus = rag.create_corpus(
        display_name=display_name,
        embedding_model_config=embedding_config,
    )
    logger.info("Corpus created: %s", corpus.name)
    return corpus.name


# ---------------------------------------------------------------------------
# Wait + smoke test
# ---------------------------------------------------------------------------

def wait_for_corpus_ready(corpus_name: str, timeout_seconds: int = 300) -> None:
    from vertexai.preview import rag

    logger.info("Waiting for corpus indexing (up to %ds)...", timeout_seconds)
    start = time.time()
    while time.time() - start < timeout_seconds:
        try:
            files = list(rag.list_files(corpus_name=corpus_name))
            if files:
                logger.info("Corpus ready — %d file(s) indexed", len(files))
                return
        except Exception:
            pass
        time.sleep(15)
    logger.warning("Timeout — files may still be indexing in the background")


def run_smoke_test(corpus_name: str) -> None:
    from vertexai.preview import rag

    logger.info("Running smoke test query...")
    try:
        response = rag.retrieval_query(
            rag_resources=[rag.RagResource(rag_corpus=corpus_name)],
            text="What are the ACMG criteria for classifying a variant as pathogenic?",
            similarity_top_k=3,
        )
        contexts = response.contexts.contexts
        logger.info("Smoke test passed — %d contexts retrieved", len(contexts))
        for i, ctx in enumerate(contexts[:2]):
            logger.info("  [%d] score=%.3f  %s", i, ctx.score, ctx.source_uri[:70])
    except Exception as exc:
        logger.warning("Smoke test failed: %s", exc)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Set up GenomeSpeak RAG corpus")
    parser.add_argument("--project",          required=True, help="GCP project ID")
    parser.add_argument("--location",         default="us-west1", help="Vertex AI + GCS region")
    parser.add_argument("--corpus-name",      default="genomespeak-knowledge")
    parser.add_argument("--corpus-resource",  help="Existing corpus resource name (skip creation)")
    args = parser.parse_args()

    import vertexai
    vertexai.init(project=args.project, location=args.location)

    logger.info("GenomeSpeak RAG Corpus Setup")
    logger.info("Project:  %s", args.project)
    logger.info("Location: %s", args.location)

    # Step 1: corpus
    if args.corpus_resource:
        corpus_name = args.corpus_resource
        logger.info("Using existing corpus: %s", corpus_name)
    else:
        corpus_name = create_rag_corpus(args.project, args.location, args.corpus_name)

    bucket_name = f"{args.project}-genomespeak-rag-staging"

    # Step 2: import all sources
    logger.info("Importing %d knowledge sources...", len(KNOWLEDGE_SOURCES))
    for source in KNOWLEDGE_SOURCES:
        import_source(corpus_name, source, args.project, bucket_name, args.location)
        time.sleep(3)  # avoid rate limit bursts

    # Step 3: wait
    wait_for_corpus_ready(corpus_name)

    # Step 4: smoke test
    run_smoke_test(corpus_name)

    # Step 5: output
    print("\n" + "=" * 60)
    print("RAG corpus setup complete!")
    print(f"GENOMESPEAK_RAG_CORPUS={corpus_name}")
    print("=" * 60)

    Path(".corpus_resource").write_text(corpus_name)


if __name__ == "__main__":
    main()
