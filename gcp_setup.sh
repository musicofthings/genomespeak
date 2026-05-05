#!/bin/bash
# scripts/gcp_setup.sh
# One-time GCP project setup for GenomeSpeak.
# Run once from your local machine with gcloud authenticated.
#
# Usage:
#   chmod +x scripts/gcp_setup.sh
#   ./scripts/gcp_setup.sh YOUR_PROJECT_ID

set -euo pipefail

PROJECT_ID="${1:?Usage: $0 PROJECT_ID}"
REGION="asia-south1"
SA_NAME="genomespeak-sa"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
REPO_NAME="genomespeak-repo"

echo "🧬 GenomeSpeak GCP Setup"
echo "Project: ${PROJECT_ID}  Region: ${REGION}"
echo ""

gcloud config set project "${PROJECT_ID}"

# ── Enable APIs ──────────────────────────────────────────────────────────────
echo "Enabling required APIs..."
gcloud services enable \
    aiplatform.googleapis.com \
    run.googleapis.com \
    cloudbuild.googleapis.com \
    storage.googleapis.com \
    firestore.googleapis.com \
    artifactregistry.googleapis.com \
    secretmanager.googleapis.com \
    documentai.googleapis.com \
    --quiet

echo "APIs enabled ✓"

# ── Create service account ───────────────────────────────────────────────────
echo "Creating service account: ${SA_EMAIL}"
gcloud iam service-accounts create "${SA_NAME}" \
    --display-name="GenomeSpeak Service Account" \
    --quiet 2>/dev/null || echo "Service account already exists"

# Grant required roles
for ROLE in \
    roles/aiplatform.user \
    roles/storage.objectAdmin \
    roles/datastore.user \
    roles/secretmanager.secretAccessor \
    roles/documentai.apiUser; do
    gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
        --member="serviceAccount:${SA_EMAIL}" \
        --role="${ROLE}" \
        --quiet
done

echo "Service account roles granted ✓"

# ── Create Artifact Registry repo ────────────────────────────────────────────
echo "Creating Artifact Registry repository: ${REPO_NAME}"
gcloud artifacts repositories create "${REPO_NAME}" \
    --repository-format=docker \
    --location="${REGION}" \
    --description="GenomeSpeak container images" \
    --quiet 2>/dev/null || echo "Repository already exists"

# ── Create GCS artifact bucket ───────────────────────────────────────────────
BUCKET_NAME="${PROJECT_ID}-genomespeak-artifacts"
echo "Creating GCS bucket: ${BUCKET_NAME}"
gcloud storage buckets create "gs://${BUCKET_NAME}" \
    --location="${REGION}" \
    --uniform-bucket-level-access \
    --quiet 2>/dev/null || echo "Bucket already exists"

gcloud storage buckets add-iam-policy-binding "gs://${BUCKET_NAME}" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="roles/storage.objectAdmin" \
    --quiet

echo "GCS bucket configured ✓"

# ── Set up Firestore ─────────────────────────────────────────────────────────
echo "Creating Firestore database (native mode)..."
gcloud firestore databases create \
    --location="${REGION}" \
    --quiet 2>/dev/null || echo "Firestore database already exists"

echo "Firestore ready ✓"

# ── Secret Manager placeholder for RAG corpus ────────────────────────────────
echo "Creating Secret Manager secret (placeholder — run setup_rag_corpus.py first)"
echo "PLACEHOLDER" | gcloud secrets create genomespeak-rag-corpus \
    --data-file=- \
    --quiet 2>/dev/null || echo "Secret already exists"

echo ""
echo "============================================================"
echo "GCP setup complete!"
echo ""
echo "Next steps:"
echo "  1. Run RAG corpus setup:"
echo "     python scripts/setup_rag_corpus.py --project ${PROJECT_ID}"
echo ""
echo "  2. Update the secret with the real corpus name:"
echo "     echo -n 'projects/.../ragCorpora/...' | \\"
echo "     gcloud secrets versions add genomespeak-rag-corpus --data-file=-"
echo ""
echo "  3. Build and deploy:"
echo "     gcloud builds submit --config=cloudbuild.yaml"
echo "============================================================"
