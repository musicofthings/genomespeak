"""
agent.py
GenomeSpeak root entry point.

This is the file ADK looks for when running:
    adk run genomespeak
    adk deploy cloud-run genomespeak

Architecture:
    User query + PDF
         │
         ▼
    QueryClassifierAgent  (Flash-Lite, LOW, ~300ms)
         │ QueryProfile
         ▼
    ModelSelectorHarness  (pure Python, zero latency)
         │ SelectionResult → ModelConfig
         ▼
    DynamicAgentFactory   (creates configured LlmAgent)
         │
         ▼
    Specialist Agent  (model + thinking_level from harness)
    ├── GenomicsAgent     (Pro, HIGH)
    ├── OncologyAgent     (Pro, HIGH)
    ├── RoutineLabAgent   (Flash, MEDIUM)
    └── PharmacogenomicsAgent (Flash or Pro)
         │ Technical interpretation
         ▼
    PlainLanguageAgent  (Flash-Lite, LOW) — jargon → plain English
         │
         ▼
    Streamed response to user
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import AsyncIterator, Optional

import vertexai
from dotenv import load_dotenv
from google.adk.agents import LlmAgent, SequentialAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools import google_search
from vertexai.generative_models import GenerationConfig, GenerativeModel, Part, Tool

from genomespeak.harness.classifier import QueryClassifierAgent
from genomespeak.harness.models import ModelConfig, SelectionResult, ThinkingLevel, UserMode
from genomespeak.harness.selector import ModelSelectorHarness
from genomespeak.tools.pdf_ingest import pdf_save_tool

load_dotenv()
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

PROJECT_ID = os.environ["GOOGLE_CLOUD_PROJECT"]
LOCATION   = os.environ.get("GOOGLE_CLOUD_LOCATION", "global")
RAG_CORPUS = os.environ.get("GENOMESPEAK_RAG_CORPUS", "")

# Force ADK to use Vertex AI backend instead of Gemini API (which requires an API key)
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "1")

vertexai.init(project=PROJECT_ID, location=LOCATION)

# ---------------------------------------------------------------------------
# System prompts for each specialist agent
# ---------------------------------------------------------------------------

GENOMICS_SYSTEM_PROMPT = """You are a senior clinical geneticist and molecular pathologist.
You interpret germline genetic test reports — whole exome sequencing, whole genome sequencing,
hereditary cancer panels, carrier screening, and chromosomal microarrays.

RULES:
- Apply ACMG/AMP 2015 variant classification criteria (PS1-PS4, PM1-PM6, PP1-PP5, BA1, BS1-BS4, BP1-BP7)
- Always state the variant classification: Pathogenic / Likely Pathogenic / VUS / Likely Benign / Benign
- Cite gnomAD population frequency and ClinVar assertions when relevant
- State inheritance pattern (autosomal dominant/recessive, X-linked, de novo)
- Reference NCCN guidelines for hereditary cancer risk when applicable
- Flag variants requiring urgent genetic counselling referral
- Never overinterpret VUS — explicitly state uncertainty
- Produce a structured technical summary for PlainLanguageAgent to rewrite"""

ONCOLOGY_SYSTEM_PROMPT = """You are a molecular oncologist specialising in precision medicine.
You interpret somatic tumour profiling reports, liquid biopsy/ctDNA results, and biomarker panels.

RULES:
- Identify actionable mutations and match to FDA-approved targeted therapies
- Report TMB (tumour mutational burden) and MSI (microsatellite instability) status with clinical implications
- Apply OncoKB, ESCAT, and NCCN therapeutic evidence tiers
- Note companion diagnostic requirements for therapies
- Flag clinical trial eligibility signals based on the mutation profile
- Clearly separate Tier 1 (FDA-approved) from Tier 2 (investigational) findings
- Produce a structured technical summary for PlainLanguageAgent to rewrite"""

ROUTINE_LAB_SYSTEM_PROMPT = """You are a clinical laboratory physician and internist.
You interpret routine laboratory reports — complete blood count, metabolic panels, lipid panels,
thyroid function, liver function, HbA1c, renal function, and urinalysis.

RULES:
- Identify all values outside reference ranges and quantify deviation (mild/moderate/severe)
- Calculate eGFR when creatinine and patient demographics are available
- Apply Friedewald equation for LDL when direct LDL is absent
- Flag critical values requiring immediate clinical attention
- Group related abnormalities into clinical patterns (e.g. hepatic, renal, anaemia pattern)
- Use WHO and IFCC reference ranges
- Produce a structured technical summary for PlainLanguageAgent to rewrite"""

PHARMACOGENOMICS_SYSTEM_PROMPT = """You are a clinical pharmacogenomicist.
You interpret pharmacogenomics (PGx) test reports and translate genotype-phenotype
relationships into actionable medication guidance.

RULES:
- Classify metaboliser phenotype for each gene: poor / intermediate / normal / rapid / ultrarapid
- Apply CPIC (Clinical Pharmacogenomics Implementation Consortium) guidelines
- List affected medications and recommended dose adjustments or alternatives
- Flag high-risk gene-drug combinations (e.g. CYP2D6 poor metaboliser + codeine = contraindicated)
- Note DPYD variants with implications for fluoropyrimidine chemotherapy
- Produce a structured technical summary for PlainLanguageAgent to rewrite"""

PLAIN_LANGUAGE_PATIENT_PROMPT = """You are a compassionate patient educator.
You receive a technical clinical interpretation and rewrite it in plain English for a patient.

RULES — NON-NEGOTIABLE:
- Zero medical jargon. If a technical term is unavoidable, immediately explain it in brackets.
- Use simple analogies (e.g. "your hemoglobin is like the fuel gauge in your car")
- Acknowledge emotional weight without being alarmist
- Always end with: what this means for the patient, and what to discuss with their doctor
- Never make a diagnosis. Never recommend specific medications.
- Use short paragraphs. No bullet points longer than one line.
- Maximum reading level: Grade 8 (age 13-14)
- Tone: warm, calm, clear, honest"""

PLAIN_LANGUAGE_DOCTOR_PROMPT = """You are a concise clinical summariser.
You receive a detailed technical interpretation and produce a structured clinical summary for a physician.

RULES:
- Preserve all technical terminology and classification systems
- Structure: Key Findings → Clinical Significance → Recommended Actions → References
- Use standard clinical abbreviations (ACMG, VAF, TMB, eGFR, CrCl, etc.)
- Flag items requiring urgent action first
- Maximum 600 words"""


# ---------------------------------------------------------------------------
# Dynamic Agent Factory
# ---------------------------------------------------------------------------

class DynamicAgentFactory:
    """
    Creates a fully configured LlmAgent for a given SelectionResult.
    The agent is ephemeral — one per request — ensuring the model config
    (including thinking_level) is applied precisely as the harness determined.
    """

    # Maps specialist_agent_name → system prompt
    SYSTEM_PROMPTS = {
        "genomics":          GENOMICS_SYSTEM_PROMPT,
        "oncology":          ONCOLOGY_SYSTEM_PROMPT,
        "routine_lab":       ROUTINE_LAB_SYSTEM_PROMPT,
        "pharmacogenomics":  PHARMACOGENOMICS_SYSTEM_PROMPT,
    }

    def build_specialist_agent(self, result: SelectionResult) -> LlmAgent:
        """Build the specialist agent from a SelectionResult."""
        cfg         = result.model_config
        agent_name  = result.specialist_agent_name
        system_prompt = self.SYSTEM_PROMPTS.get(agent_name, ROUTINE_LAB_SYSTEM_PROMPT)

        tools = []
        if cfg.use_search_grounding:
            tools.append(google_search)

        gen_config = GenerationConfig(
            temperature=cfg.temperature,
            max_output_tokens=cfg.max_output_tokens,
        )

        return LlmAgent(
            name=f"Specialist_{agent_name}",
            model=cfg.model_id,
            instruction=system_prompt,
            tools=tools,
            generate_content_config=gen_config,
            description=f"Specialist agent: {agent_name} | {cfg.model_id} | {cfg.thinking_level.value}",
        )

    def build_plain_language_agent(
        self,
        user_mode: UserMode,
        harness: ModelSelectorHarness,
    ) -> LlmAgent:
        """Build the PlainLanguageAgent with mode-appropriate config."""
        cfg = harness.select_plain_language_config(user_mode)

        system_prompt = (
            PLAIN_LANGUAGE_PATIENT_PROMPT
            if user_mode == UserMode.PATIENT
            else PLAIN_LANGUAGE_DOCTOR_PROMPT
        )

        gen_config = GenerationConfig(
            temperature=cfg.temperature,
            max_output_tokens=cfg.max_output_tokens,
        )

        return LlmAgent(
            name=f"PlainLanguage_{user_mode.value}",
            model=cfg.model_id,
            instruction=system_prompt,
            generate_content_config=gen_config,
            description=f"PlainLanguageAgent | {user_mode.value} mode | {cfg.model_id}",
        )


# ---------------------------------------------------------------------------
# GenomeSpeakOrchestrator — main class
# ---------------------------------------------------------------------------

class GenomeSpeakOrchestrator:
    """
    Top-level orchestrator. Called by the ADK Runner.
    Coordinates: classify → select → build agents → execute pipeline → stream response.
    """

    def __init__(self):
        self._classifier = QueryClassifierAgent(project_id=PROJECT_ID, location=LOCATION)
        self._harness    = ModelSelectorHarness()
        self._factory    = DynamicAgentFactory()

    async def process(
        self,
        user_query: str,
        pdf_artifact_name: Optional[str] = None,
        pdf_bytes: Optional[bytes] = None,
        session_has_prior_report: bool = False,
    ) -> AsyncIterator[str]:
        """
        Main processing pipeline. Yields response tokens as they stream.

        Steps:
        1. Classify query → QueryProfile  (Flash-Lite, LOW, ~300ms)
        2. Select model   → SelectionResult  (pure Python, ~0ms)
        3. Build agents   → specialist + plain_language
        4. Execute specialist with native PDF
        5. Execute PlainLanguageAgent on specialist output
        6. Yield streamed tokens
        """

        # --- Step 1: Classify ---
        logger.info("Classifying query: '%s'", user_query[:80])
        profile = await self._classifier.classify(
            user_query=user_query,
            session_has_prior_report=session_has_prior_report,
            pdf_filename=pdf_artifact_name,
        )
        logger.info(
            "Profile: tier=%s report=%s mode=%s agent=%s",
            profile.complexity_tier.value,
            profile.report_type.value,
            profile.user_mode.value,
            profile.specialist_agent,
        )

        # --- Step 2: Select model ---
        result = self._harness.select(profile)
        logger.info(
            "Model selected: %s | thinking=%s | override=%s",
            result.model_config.model_id,
            result.model_config.thinking_level.value,
            result.tier_overridden,
        )

        model_short = (
            "Gemini 3.1 Pro" if "3.1-pro" in result.model_config.model_id
            else "Gemini 3 Flash" if "3-flash" in result.model_config.model_id
            else "Gemini 3.1 Flash-Lite"
        )
        yield f'\x00STATUS:Analysing with {model_short} ({result.model_config.thinking_level.value} thinking)…\x00'

        # --- Step 3: Build agents ---
        specialist_agent  = self._factory.build_specialist_agent(result)
        plain_lang_agent  = self._factory.build_plain_language_agent(
            profile.user_mode, self._harness
        )

        # --- Step 4 & 5: Build and run SequentialAgent ---
        pipeline = SequentialAgent(
            name="GenomeSpeakPipeline",
            sub_agents=[specialist_agent, plain_lang_agent],
            description="Specialist interpretation → plain language rewrite",
        )

        enriched_query = user_query
        if pdf_artifact_name:
            pdf_note = "The report PDF is attached above." if pdf_bytes else f"Report file: {pdf_artifact_name}"
            enriched_query = (
                f"{pdf_note}\n\n"
                f"User question: {user_query}\n\n"
                f"[Selection: {result.selection_rationale}]"
            )

        # --- Step 6: Stream ---
        # In production ADK deployment, Runner handles streaming automatically.
        # This yield pattern is compatible with FastAPI StreamingResponse.
        yield f"[Model: {result.model_config.model_id} | "
        yield f"Thinking: {result.model_config.thinking_level.value}]\n\n"

        # Runner execution (streaming-compatible)
        session_service = InMemorySessionService()
        runner = Runner(
            agent=pipeline,
            app_name="genomespeak",
            session_service=session_service,
        )

        session = await session_service.create_session(
            app_name="genomespeak",
            user_id="user",
        )

        from google.genai.types import Blob, Content, Part as AdkPart
        parts = []
        if pdf_bytes:
            # Inject PDF inline so the specialist agent sees it in its context window.
            # This bypasses ADK Artifact service, which is not pre-populated from the
            # FastAPI upload endpoint's in-memory session store.
            parts.append(AdkPart(inline_data=Blob(data=pdf_bytes, mime_type="application/pdf")))
        parts.append(AdkPart(text=enriched_query))
        user_content = Content(role="user", parts=parts)

        async for event in runner.run_async(
            user_id="user",
            session_id=session.id,
            new_message=user_content,
        ):
            if event.content and event.content.parts:
                for part in event.content.parts:
                    if part.text:
                        yield part.text


# ---------------------------------------------------------------------------
# ADK entry point — required by `adk run` and `adk deploy`
# ---------------------------------------------------------------------------

def create_agent() -> LlmAgent:
    """
    ADK calls this function to get the root agent.
    We return a lightweight wrapper agent that delegates to
    GenomeSpeakOrchestrator at runtime.
    """
    return LlmAgent(
        name="GenomeSpeak",
        model="gemini-3.1-flash-lite-preview",  # Thin wrapper — real model selected by harness
        instruction="""You are GenomeSpeak — an AI assistant that helps patients and doctors
        understand complex lab and genomics reports.

        When a user uploads a report or asks a question:
        1. Acknowledge the upload warmly
        2. Ask if they are a patient or a healthcare professional (if not already known)
        3. Invite them to ask their question about the report

        You will then route to the appropriate specialist agent and return a plain-language answer.
        Always end with a reminder to discuss findings with their healthcare team.""",
        tools=[pdf_save_tool],
        description="GenomeSpeak — AI-powered lab report interpreter",
    )


# Expose root_agent for ADK discovery
root_agent = create_agent()
