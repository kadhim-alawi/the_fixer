"""Model backend resolution.

Two supported paths, both satisfying the hackathon's "Gemini 3.5 or newer"
requirement:

* **Vertex AI** -- set GOOGLE_GENAI_USE_VERTEXAI=1, GOOGLE_CLOUD_PROJECT and
  GOOGLE_CLOUD_LOCATION. Preferred for the submission: it keeps inference
  inside Google Cloud alongside Cloud Run and Cloud SQL.
* **Gemini API** -- set GOOGLE_API_KEY. Faster to get going locally.

google-genai reads these variables itself, so nothing here passes credentials
around; this module only reports which path is configured and fails loudly and
early when neither is.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# gemini-3.5-flash is the model id available as of August 2026. 3.5 Pro has not
# shipped a public id. Kept in one place so the Day 9 deploy and the evaluation
# harness cannot drift apart.
DEFAULT_MODEL = "gemini-3.5-flash"


@dataclass(frozen=True)
class Backend:
    kind: str  # "vertex" | "api_key" | "none"
    model: str
    detail: str

    @property
    def ready(self) -> bool:
        return self.kind != "none"


def resolve(model: str | None = None) -> Backend:
    model = model or os.environ.get("FIXER_MODEL", DEFAULT_MODEL)

    use_vertex = os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").lower() in ("1", "true", "yes")
    project = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "")
    api_key = os.environ.get("GOOGLE_API_KEY", "")

    if use_vertex and project:
        return Backend(
            "vertex", model, f"Vertex AI - project={project} location={location or 'us-central1'}"
        )
    if api_key:
        return Backend("api_key", model, f"Gemini API - key ...{api_key[-4:]}")
    return Backend(
        "none",
        model,
        "No credentials found. Set GOOGLE_API_KEY, or set GOOGLE_GENAI_USE_VERTEXAI=1 "
        "with GOOGLE_CLOUD_PROJECT and GOOGLE_CLOUD_LOCATION.",
    )


def require() -> Backend:
    b = resolve()
    if not b.ready:
        raise RuntimeError(b.detail)
    return b
