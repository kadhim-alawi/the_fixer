#!/usr/bin/env bash
# Deploy The Fixer to Cloud Run.
#
# Builds with Cloud Build, so a local Docker daemon is not needed.
#
#   PROJECT=my-project ./scripts/deploy.sh
#
# Uses Vertex AI for inference, which keeps the model inside Google Cloud and
# produces Vertex logs -- one of the proofs of Google Cloud the submission video
# is required to show.

set -euo pipefail

PROJECT="${PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
REGION="${REGION:-us-central1}"        # where Cloud Run runs
# Gemini 3.x is served from the "global" Vertex location, not from a
# regional endpoint. These are two different things and must not be
# conflated -- passing the Cloud Run region here yields a 404.
VERTEX_LOCATION="${VERTEX_LOCATION:-global}"
SERVICE="${SERVICE:-the-fixer}"
MODEL="${FIXER_MODEL:-gemini-3.5-flash}"

if [ -z "${PROJECT}" ]; then
  echo "No project set. Use: PROJECT=your-project ./scripts/deploy.sh" >&2
  exit 1
fi

echo "project : ${PROJECT}"
echo "region  : ${REGION}  (vertex: ${VERTEX_LOCATION})"
echo "service : ${SERVICE}"
echo "model   : ${MODEL}"
echo

echo "==> enabling required APIs"
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  aiplatform.googleapis.com \
  --project "${PROJECT}"

echo "==> granting the runtime service account access to Vertex AI"
PROJECT_NUMBER="$(gcloud projects describe "${PROJECT}" --format='value(projectNumber)')"
gcloud projects add-iam-policy-binding "${PROJECT}" \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role="roles/aiplatform.user" \
  --condition=None \
  --quiet > /dev/null

echo "==> building and deploying"
# 2Gi: /tmp is memory-backed and holds the simulated warehouse per mission.
# Concurrency 4: missions are long-lived SSE streams, not cheap requests.
gcloud run deploy "${SERVICE}" \
  --source . \
  --project "${PROJECT}" \
  --region "${REGION}" \
  --platform managed \
  --allow-unauthenticated \
  --memory 2Gi \
  --cpu 2 \
  --timeout 3600 \
  --concurrency 4 \
  --max-instances 3 \
  --set-env-vars "GOOGLE_GENAI_USE_VERTEXAI=1,GOOGLE_CLOUD_PROJECT=${PROJECT},GOOGLE_CLOUD_LOCATION=${VERTEX_LOCATION},FIXER_MODEL=${MODEL},FIXER_MISSION_DIR=/tmp/missions"

URL="$(gcloud run services describe "${SERVICE}" --project "${PROJECT}" --region "${REGION}" --format='value(status.url)')"
echo
echo "deployed: ${URL}"
echo "checking health ..."
curl -fsS "${URL}/api/health" && echo
