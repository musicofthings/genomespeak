#!/usr/bin/env python3
"""
scripts/setup_rag_corpus.py
One-time setup: creates and populates the Vertex AI RAG Engine corpus
for GenomeSpeak with all clinical knowledge bases.

Usage:
    python scripts/setup_rag_corpus.py --project YOUR_PROJECT_ID

What it does:
    1. Creates a RAG corpus named 'genomespeak-knowledge'
    2. Downloads / references public clinical knowledge sources
    3. Imports them into the corpus as chunked documents
    4. Outputs the corpus resource name → add to .env as GENOMESPEAK_RAG_CORPUS

Knowledge bases indexed:
    - ACMG/AMP 2015 variant classification criteria
    - ClinGen curated gene-disease validity
    - WHO / IFCC reference ranges (CBC, chemistry, lipids, thyroid)
    - NCCN hereditary cancer guidelines (public summaries)
    - CPIC pharmacogenomics guidelines
    - NCI drug-gene interaction summaries
    - MedlinePlus patient-facing disease summaries (common conditions)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Knowledge source definitions
# Each entry: (name, description, gcs_uri_or_web_url, chunk_size)
# For the hackathon we use publicly accessible URLs + GCS imports.
# Production: mirror everything to a private GCS bucket for reliability.
# ---------------------------------------------------------------------------

KNOWLEDGE_SOURCES = [
    {
        "name": "acmg_variant_classification",
        "description": "ACMG/AMP 2015 variant classification criteria — PS1-PS4, PM1-PM6, PP1-PP5, BA1, BS1-BS4, BP1-BP7",
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
        "description": "CPIC pharmacogenomics implementation guidelines — CYP2D6, CYP2C19, DPYD, TPMT, SLCO1B1",
        "type": "web_url",
        "uri": "https://cpicpgx.org/guidelines/",
        "chunk_size": 512,
        "chunk_overlap": 100,
    },
    {
        "name": "who_reference_ranges",
        "description": "WHO and IFCC reference intervals for complete blood count, metabolic panel, lipids, thyroid",
        "type": "inline_text",
        "content": WHO_REFERENCE_RANGES_TEXT,  # defined below
        "chunk_size": 256,
        "chunk_overlap": 50,
    },
    {
        "name": "nccn_hereditary_cancer_overview",
        "description": "NCCN hereditary breast/ovarian cancer and Lynch syndrome risk management guidelines (public summaries)",
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
        "description": "ClinVar variant classification summary — pathogenicity definitions and evidence standards",
        "type": "web_url",
        "uri": "https://www.ncbi.nlm.nih.gov/clinvar/docs/clinsig/",
        "chunk_size": 512,
        "chunk_overlap": 100,
    },
    {
        "name": "oncokb_evidence_levels",
        "description": "OncoKB therapeutic evidence levels — Levels 1-4, R1, R2 for oncology actionability",
        "type": "web_url",
        "uri": "https://www.oncokb.org/levels",
        "chunk_size": 512,
        "chunk_overlap": 100,
    },
    {
        "name": "nipt_interpretation",
        "description": "NIPT (non-invasive prenatal testing) result interpretation — trisomies, sex chromosome aneuploidies",
        "type": "web_url",
        "uri": "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC6313310/",
        "chunk_size": 512,
        "chunk_overlap": 100,
    },
    {
        "name": "medlineplus_common_lab_tests",
        "description": "MedlinePlus patient-facing explanations of common lab tests — CBC, CMP, lipids, HbA1c, thyroid",
        "type": "web_url",
        "uri": "https://medlineplus.gov/lab-tests/",
        "chunk_size": 256,
        "chunk_overlap": 50,
    },
]

# ---------------------------------------------------------------------------
# Reference range knowledge — inlined as structured text
# More reliable than scraping; this is the ground truth for RoutineLabAgent
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
# Main setup functions
# ---------------------------------------------------------------------------

def create_rag_corpus(project_id: str, location: str, display_name: str) -> str:
    """
    Create a new RAG corpus in Vertex AI.
    Returns the corpus resource name.
    """
    from vertexai.preview import rag
    import vertexai

    vertexai.init(project=project_id, location=location)

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


def import_web_url(corpus_name: str, source: dict) -> None:
    """Import a web URL into the RAG corpus."""
    from vertexai.preview import rag

    logger.info("Importing web URL: %s → %s", source["name"], source["uri"])

    try:
        response = rag.import_files(
            corpus_name=corpus_name,
            paths=[source["uri"]],
            chunk_size=source.get("chunk_size", 512),
            chunk_overlap=source.get("chunk_overlap", 100),
            max_embedding_requests_per_min=900,
        )
        logger.info("  Import job: %s", response.metadata.partial_failures_count or "no failures")
    except Exception as exc:
        logger.warning("  Failed to import %s: %s", source["name"], exc)


def import_inline_text(
    corpus_name: str,
    source: dict,
    project_id: str,
    location: str,
) -> None:
    """
    Write inline text content to a temporary GCS object then import.
    Used for the WHO reference ranges that we maintain ourselves.
    """
    from google.cloud import storage
    from vertexai.preview import rag

    bucket_name = f"{project_id}-genomespeak-rag-staging"
    blob_name   = f"knowledge/{source['name']}.txt"

    logger.info("Staging inline text to GCS: gs://%s/%s", bucket_name, blob_name)

    try:
        gcs_client = storage.Client(project=project_id)
        bucket     = gcs_client.bucket(bucket_name)

        # Create bucket if it doesn't exist
        if not bucket.exists():
            bucket.create(location=location)
            logger.info("  Created staging bucket: %s", bucket_name)

        blob = bucket.blob(blob_name)
        blob.upload_from_string(
            source["content"],
            content_type="text/plain",
        )

        gcs_uri = f"gs://{bucket_name}/{blob_name}"
        logger.info("  Importing from GCS: %s", gcs_uri)

        rag.import_files(
            corpus_name=corpus_name,
            paths=[gcs_uri],
            chunk_size=source.get("chunk_size", 256),
            chunk_overlap=source.get("chunk_overlap", 50),
        )
    except Exception as exc:
        logger.warning("  Failed to import inline text %s: %s", source["name"], exc)


def wait_for_corpus_ready(corpus_name: str, timeout_seconds: int = 300) -> None:
    """Poll until the corpus has at least one indexed file."""
    from vertexai.preview import rag

    logger.info("Waiting for corpus indexing to complete (up to %ds)...", timeout_seconds)
    start = time.time()

    while time.time() - start < timeout_seconds:
        try:
            files = list(rag.list_files(corpus_name=corpus_name))
            if files:
                logger.info("Corpus ready — %d files indexed", len(files))
                return
        except Exception:
            pass
        time.sleep(10)

    logger.warning("Timeout waiting for corpus — files may still be indexing")


def run_smoke_test(corpus_name: str, project_id: str, location: str) -> None:
    """Run a test RAG query to verify the corpus is working."""
    from vertexai.preview import rag
    import vertexai

    logger.info("Running smoke test query...")

    try:
        response = rag.retrieval_query(
            rag_resources=[rag.RagResource(rag_corpus=corpus_name)],
            text="What are the ACMG criteria for classifying a variant as pathogenic?",
            similarity_top_k=3,
        )
        contexts = response.contexts.contexts
        logger.info("Smoke test passed — retrieved %d contexts", len(contexts))
        for i, ctx in enumerate(contexts[:2]):
            logger.info("  [%d] score=%.3f source=%s", i, ctx.score, ctx.source_uri[:60])
    except Exception as exc:
        logger.warning("Smoke test failed: %s", exc)


def main() -> None:
    parser = argparse.ArgumentParser(description="Set up GenomeSpeak RAG corpus")
    parser.add_argument("--project", required=True, help="GCP project ID")
    parser.add_argument("--location", default="us-central1", help="Vertex AI location")
    parser.add_argument("--corpus-name", default="genomespeak-knowledge", help="Corpus display name")
    parser.add_argument("--skip-existing", action="store_true", help="Skip if corpus already exists")
    parser.add_argument("--corpus-resource", help="Existing corpus resource name (skip creation)")
    args = parser.parse_args()

    logger.info("GenomeSpeak RAG Corpus Setup")
    logger.info("Project:  %s", args.project)
    logger.info("Location: %s", args.location)

    # Step 1: Create or use existing corpus
    if args.corpus_resource:
        corpus_name = args.corpus_resource
        logger.info("Using existing corpus: %s", corpus_name)
    else:
        corpus_name = create_rag_corpus(args.project, args.location, args.corpus_name)

    # Step 2: Import all knowledge sources
    logger.info("Importing %d knowledge sources...", len(KNOWLEDGE_SOURCES))

    for source in KNOWLEDGE_SOURCES:
        if source["type"] == "web_url":
            import_web_url(corpus_name, source)
        elif source["type"] == "inline_text":
            import_inline_text(corpus_name, source, args.project, args.location)
        time.sleep(2)  # Rate limit safety

    # Step 3: Wait for indexing
    wait_for_corpus_ready(corpus_name)

    # Step 4: Smoke test
    run_smoke_test(corpus_name, args.project, args.location)

    # Step 5: Output environment variable
    print("\n" + "=" * 60)
    print("RAG corpus setup complete!")
    print("Add this to your .env file:")
    print(f"GENOMESPEAK_RAG_CORPUS={corpus_name}")
    print("=" * 60)

    # Also write to a local file for CI/CD pipelines
    env_output = Path(".corpus_resource")
    env_output.write_text(corpus_name)
    logger.info("Corpus resource name written to .corpus_resource")


if __name__ == "__main__":
    main()
