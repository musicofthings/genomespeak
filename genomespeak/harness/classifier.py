"""
harness/classifier.py
QueryClassifierAgent — the lightweight first-pass classifier.

Runs on Gemini 3.1 Flash-Lite at LOW thinking.
Produces a structured QueryProfile in ~300ms.
This is the only agent that sees raw user input before model selection.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

import vertexai
from vertexai.generative_models import (
    GenerationConfig,
    GenerativeModel,
    Part,
    SafetySetting,
    HarmCategory,
    HarmBlockThreshold,
)

from genomespeak.harness.models import (
    ComplexityTier,
    QueryProfile,
    ReportType,
    UserMode,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Classifier system prompt
# ---------------------------------------------------------------------------

CLASSIFIER_SYSTEM_PROMPT = """You are a medical query classifier for GenomeSpeak.
Your sole job is to analyse an incoming user query and produce a JSON classification.

You are NOT answering the medical question — only classifying it.

COMPLEXITY TIERS:
- tier_1: Simple definition or yes/no. No synthesis needed.
  Examples: "What is hemoglobin?", "Is 7.2 a normal HbA1c?"
- tier_2: Moderate — needs reference range context or single-marker explanation.
  Examples: "Which values are abnormal?", "Is my LDL dangerous?"
- tier_3: Complex — cross-marker synthesis, action planning, multi-step reasoning.
  Examples: "Explain all my abnormal values and what I should do.",
            "How does my HbA1c relate to my lipid panel?"
- tier_4: Expert clinical — variant interpretation, ACMG criteria, NCCN guidelines,
          targeted therapy matching, somatic mutation analysis.
  Examples: "Classify this BRCA2 variant.", "What therapies match my TMB-High result?"

REPORT TYPES:
- routine_lab: CBC, CMP, BMP, lipid panel, HbA1c, thyroid (TSH/T4), liver function, urine
- genomics_germline: WES, WGS, hereditary gene panels, BRCA, Lynch, carrier screening
- oncology_somatic: Tumor NGS, ctDNA, somatic mutations, TMB, MSI, PDL1, liquid biopsy
- pharmacogenomics: PGx panels — CYP2D6, CYP2C19, CYP2C9, DPYD, TPMT, SLCO1B1, UGT1A1
- prenatal: NIPT, karyotype, amniocentesis, CVS, microarray (prenatal context)
- chromosomal: Array CGH, FISH, chromosomal microarray (postnatal), structural variants
- unknown: Cannot determine from context

USER MODE SIGNALS:
- If the user uses medical abbreviations (ACMG, VAF, TMB, eGFR, NCCN) → doctor
- If the user uses plain language ("my blood test", "what does this mean") → patient
- Default to patient when uncertain

SPECIALIST AGENT:
- routine_lab → "routine_lab"
- genomics_germline → "genomics"
- oncology_somatic → "oncology"
- pharmacogenomics → "pharmacogenomics"
- prenatal | chromosomal → "genomics"
- unknown → "routine_lab"

SEARCH GROUNDING needed when:
- Query asks about "latest", "current", "approved", "new drug", "guidelines"
- Oncology therapy matching (FDA approvals change frequently)

CODE EXECUTION needed when:
- Query asks for eGFR calculation, LDL-Friedewald, BMI, z-score, percentile
- Any query asking "calculate", "what is my score", "how much"

Return ONLY valid JSON matching this schema — no markdown, no preamble:
{
  "complexity_tier": "tier_1" | "tier_2" | "tier_3" | "tier_4",
  "report_type": "routine_lab" | "genomics_germline" | "oncology_somatic" | "pharmacogenomics" | "prenatal" | "chromosomal" | "unknown",
  "user_mode": "patient" | "doctor",
  "requires_search_grounding": true | false,
  "requires_code_execution": true | false,
  "is_followup_question": true | false,
  "specialist_agent": "routine_lab" | "genomics" | "oncology" | "pharmacogenomics" | "plain_language",
  "classifier_reasoning": "one sentence explanation"
}"""


# ---------------------------------------------------------------------------
# Safety settings — permissive for medical content (it's a healthcare tool)
# ---------------------------------------------------------------------------

SAFETY_SETTINGS = [
    SafetySetting(
        category=HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
        threshold=HarmBlockThreshold.BLOCK_ONLY_HIGH,
    ),
    SafetySetting(
        category=HarmCategory.HARM_CATEGORY_HARASSMENT,
        threshold=HarmBlockThreshold.BLOCK_ONLY_HIGH,
    ),
]


class QueryClassifierAgent:
    """
    Stateless classifier that runs on gemini-3.1-flash-lite-preview at LOW thinking.
    Executes in ~300ms. Returns a fully populated QueryProfile.
    """

    MODEL_ID = "gemini-2.5-flash-lite"

    def __init__(self, project_id: str, location: str = "us-central1"):
        self._project_id = project_id
        self._location = location
        vertexai.init(project=project_id, location=location)
        self._model = GenerativeModel(
            model_name=self.MODEL_ID,
            system_instruction=CLASSIFIER_SYSTEM_PROMPT,
        )
        self._gen_config = GenerationConfig(
            temperature=0.0,
            max_output_tokens=512,
            response_mime_type="application/json",
        )

    async def classify(
        self,
        user_query: str,
        session_has_prior_report: bool = False,
        pdf_filename: Optional[str] = None,
    ) -> QueryProfile:
        """
        Classify a user query. Returns a QueryProfile.
        Runs synchronously on the calling thread — wrap in asyncio.to_thread
        if calling from an async context.
        """
        prompt_parts = [self._build_prompt(user_query, session_has_prior_report, pdf_filename)]

        try:
            response = self._model.generate_content(
                prompt_parts,
                generation_config=self._gen_config,
                safety_settings=SAFETY_SETTINGS,
            )
            raw = response.text.strip()
            data = json.loads(raw)
            profile = self._validate_and_build(data)
            logger.debug("Classifier output: %s", profile.model_dump())
            return profile

        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            logger.warning("Classifier parse error: %s — using safe defaults", exc)
            return self._safe_default(user_query)

    @staticmethod
    def _build_prompt(
        query: str,
        has_prior_report: bool,
        pdf_filename: Optional[str],
    ) -> str:
        parts = [f"USER QUERY: {query}"]
        if pdf_filename:
            parts.append(f"UPLOADED FILE: {pdf_filename}")
        if has_prior_report:
            parts.append(
                "SESSION CONTEXT: The user has already uploaded and interpreted a report "
                "in this session. This may be a follow-up clarification."
            )
        return "\n".join(parts)

    @staticmethod
    def _validate_and_build(data: dict) -> QueryProfile:
        """Parse the raw JSON dict into a validated QueryProfile."""
        return QueryProfile(
            complexity_tier=ComplexityTier(data["complexity_tier"]),
            report_type=ReportType(data["report_type"]),
            user_mode=UserMode(data["user_mode"]),
            requires_search_grounding=bool(data.get("requires_search_grounding", False)),
            requires_code_execution=bool(data.get("requires_code_execution", False)),
            is_followup_question=bool(data.get("is_followup_question", False)),
            specialist_agent=data.get("specialist_agent", "routine_lab"),
            classifier_reasoning=data.get("classifier_reasoning", ""),
        )

    @staticmethod
    def _safe_default(query: str) -> QueryProfile:
        """
        Conservative fallback when classifier fails.
        Defaults to Pro+MEDIUM via UNKNOWN report type + TIER_3.
        """
        return QueryProfile(
            complexity_tier=ComplexityTier.TIER_3,
            report_type=ReportType.UNKNOWN,
            user_mode=UserMode.PATIENT,
            requires_search_grounding=False,
            requires_code_execution=False,
            is_followup_question=False,
            specialist_agent="routine_lab",
            classifier_reasoning="Safe default — classifier failed to parse response",
        )
