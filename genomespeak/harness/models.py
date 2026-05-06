"""
harness/models.py
Pydantic schemas for query classification and adaptive model selection.
Every agent decision in GenomeSpeak flows through these types.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class ComplexityTier(str, Enum):
    """
    Four-tier complexity scale for incoming queries.

    TIER_1  Simple / factual
            "What does hemoglobin mean?"
            "Is my blood pressure reading normal?"
            → Model: Flash-Lite  |  Thinking: LOW

    TIER_2  Moderate — needs reference range context
            "Which of my values are outside the normal range?"
            "Is my cholesterol dangerous for someone my age?"
            → Model: Flash  |  Thinking: MEDIUM

    TIER_3  Complex — multi-marker synthesis, follow-up reasoning
            "Explain all my abnormal values and what I should do next."
            "How does my HbA1c connect to my lipid panel results?"
            → Model: Flash  |  Thinking: HIGH

    TIER_4  Expert clinical — variant interpretation, ACMG, NCCN, oncology
            "Classify this BRCA2 variant under ACMG/AMP 2015 criteria."
            "What targeted therapies match my tumor's somatic profile?"
            → Model: 3.1 Pro  |  Thinking: HIGH
    """
    TIER_1 = "tier_1"
    TIER_2 = "tier_2"
    TIER_3 = "tier_3"
    TIER_4 = "tier_4"


class ReportType(str, Enum):
    """Detected report category. Used for model selection override rules."""
    ROUTINE_LAB        = "routine_lab"        # CBC, CMP, BMP, lipids, HbA1c, thyroid, LFT
    GENOMICS_GERMLINE  = "genomics_germline"  # WES, WGS, gene panels, carrier screening
    ONCOLOGY_SOMATIC   = "oncology_somatic"   # Tumor NGS, ctDNA, TMB, MSI, PDL1
    PHARMACOGENOMICS   = "pharmacogenomics"   # PGx panels (CYP2D6, CYP2C19, DPYD…)
    PRENATAL           = "prenatal"           # NIPT, karyotype, microarray, amnio
    CHROMOSOMAL        = "chromosomal"        # Array CGH, FISH, structural variants
    UNKNOWN            = "unknown"            # Cannot classify — default to safe tier


class UserMode(str, Enum):
    """Output persona: plain English for patients, clinical detail for doctors."""
    PATIENT = "patient"
    DOCTOR  = "doctor"


class ThinkingLevel(str, Enum):
    """Maps directly to Gemini 3.x thinking_level parameter."""
    LOW    = "LOW"
    MEDIUM = "MEDIUM"
    HIGH   = "HIGH"


# ---------------------------------------------------------------------------
# Query classification output (produced by QueryClassifierAgent)
# ---------------------------------------------------------------------------

class QueryProfile(BaseModel):
    """
    Structured classification of a single user query + report upload.
    Produced by the lightweight QueryClassifierAgent (Flash-Lite, LOW).
    Consumed by ModelSelectorHarness to build a ModelConfig.
    """
    complexity_tier: ComplexityTier = Field(
        description="Four-tier complexity scale for this specific query"
    )
    report_type: ReportType = Field(
        description="Detected report category from the uploaded PDF"
    )
    user_mode: UserMode = Field(
        description="Patient (plain language) or doctor (clinical detail) persona"
    )
    requires_search_grounding: bool = Field(
        default=False,
        description="True when query needs real-time info (drug approvals, guidelines updates)"
    )
    requires_code_execution: bool = Field(
        default=False,
        description="True when numeric calculation is needed (eGFR, LDL-Friedewald, z-scores)"
    )
    is_followup_question: bool = Field(
        default=False,
        description="True when session already has an interpreted report — cheaper model viable"
    )
    specialist_agent: str = Field(
        description="Which sub-agent should handle interpretation: "
                    "routine_lab | genomics | oncology | pharmacogenomics | plain_language"
    )
    classifier_reasoning: str = Field(
        description="One-sentence rationale for the tier + agent assignment"
    )


# ---------------------------------------------------------------------------
# Model configuration (produced by ModelSelectorHarness)
# ---------------------------------------------------------------------------

@dataclass
class ModelConfig:
    """
    Fully resolved configuration for one agent invocation.
    Created by ModelSelectorHarness from a QueryProfile.
    """
    model_id: str
    thinking_level: ThinkingLevel
    temperature: float              = 0.1
    max_output_tokens: int          = 8192
    use_search_grounding: bool      = False
    use_code_execution: bool        = False
    use_rag: bool                   = True
    # PDF is always passed natively — no Document AI
    use_native_pdf: bool            = True
    # Estimated cost tier for logging / analytics
    cost_tier: str                  = "medium"   # low | medium | high


@dataclass
class SelectionResult:
    """
    Complete output of ModelSelectorHarness for a single request.
    Contains the model config plus all routing metadata for tracing.
    """
    query_profile: QueryProfile
    model_config: ModelConfig
    specialist_agent_name: str
    selection_rationale: str
    # Override flags — set when hard rules override tier-based selection
    tier_overridden: bool           = False
    override_reason: Optional[str]  = None
