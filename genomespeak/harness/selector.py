"""
harness/selector.py
ModelSelectorHarness — the adaptive model selection engine.

Decision flow:
  QueryProfile  →  override_rules()  →  tier_matrix lookup  →  ModelConfig

Override rules take precedence over the tier matrix. They encode hard clinical
constraints: genomics always gets at least Pro+MEDIUM regardless of query
simplicity, and follow-up clarifications drop one tier to save cost.
"""

from __future__ import annotations

import logging
from typing import Optional

from genomespeak.harness.models import (
    ComplexityTier,
    ModelConfig,
    QueryProfile,
    ReportType,
    SelectionResult,
    ThinkingLevel,
    UserMode,
)
from genomespeak.harness.registry import MODEL_REGISTRY, get_config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tier matrix
# Rows: ComplexityTier  |  Columns: ReportType  |  Value: registry key
# ---------------------------------------------------------------------------
# Design principle: err on the side of capability for clinical data.
# It is worse to under-interpret a genomics result than to over-spend.
# ---------------------------------------------------------------------------

TIER_MATRIX: dict[tuple[ComplexityTier, ReportType], str] = {

    # --- ROUTINE LAB (CBC, CMP, lipids, thyroid, LFT) ---
    (ComplexityTier.TIER_1, ReportType.ROUTINE_LAB):       "flash_lite_low",
    (ComplexityTier.TIER_2, ReportType.ROUTINE_LAB):       "flash_medium",
    (ComplexityTier.TIER_3, ReportType.ROUTINE_LAB):       "flash_high",
    (ComplexityTier.TIER_4, ReportType.ROUTINE_LAB):       "flash_high",

    # --- GENOMICS GERMLINE (WES, WGS, panels, carrier) ---
    # Never below Flash+MEDIUM even for simple questions — clinical safety margin
    (ComplexityTier.TIER_1, ReportType.GENOMICS_GERMLINE): "flash_medium",
    (ComplexityTier.TIER_2, ReportType.GENOMICS_GERMLINE): "pro_medium",
    (ComplexityTier.TIER_3, ReportType.GENOMICS_GERMLINE): "pro_high",
    (ComplexityTier.TIER_4, ReportType.GENOMICS_GERMLINE): "pro_high",

    # --- ONCOLOGY SOMATIC (tumor NGS, ctDNA, TMB/MSI) ---
    # TIER_1 gets Pro+LOW because even "simple" oncology questions carry weight
    (ComplexityTier.TIER_1, ReportType.ONCOLOGY_SOMATIC):  "pro_low",
    (ComplexityTier.TIER_2, ReportType.ONCOLOGY_SOMATIC):  "pro_medium",
    (ComplexityTier.TIER_3, ReportType.ONCOLOGY_SOMATIC):  "pro_high",
    (ComplexityTier.TIER_4, ReportType.ONCOLOGY_SOMATIC):  "pro_high",

    # --- PHARMACOGENOMICS (CYP2D6, CYP2C19, DPYD, TPMT…) ---
    (ComplexityTier.TIER_1, ReportType.PHARMACOGENOMICS):  "flash_medium",
    (ComplexityTier.TIER_2, ReportType.PHARMACOGENOMICS):  "flash_high",
    (ComplexityTier.TIER_3, ReportType.PHARMACOGENOMICS):  "pro_medium",
    (ComplexityTier.TIER_4, ReportType.PHARMACOGENOMICS):  "pro_high",

    # --- PRENATAL (NIPT, karyotype, amniocentesis) ---
    (ComplexityTier.TIER_1, ReportType.PRENATAL):          "flash_medium",
    (ComplexityTier.TIER_2, ReportType.PRENATAL):          "pro_medium",
    (ComplexityTier.TIER_3, ReportType.PRENATAL):          "pro_high",
    (ComplexityTier.TIER_4, ReportType.PRENATAL):          "pro_high",

    # --- CHROMOSOMAL (array CGH, FISH, structural variants) ---
    (ComplexityTier.TIER_1, ReportType.CHROMOSOMAL):       "flash_medium",
    (ComplexityTier.TIER_2, ReportType.CHROMOSOMAL):       "pro_medium",
    (ComplexityTier.TIER_3, ReportType.CHROMOSOMAL):       "pro_high",
    (ComplexityTier.TIER_4, ReportType.CHROMOSOMAL):       "pro_high",

    # --- UNKNOWN — conservative defaults ---
    (ComplexityTier.TIER_1, ReportType.UNKNOWN):           "flash_medium",
    (ComplexityTier.TIER_2, ReportType.UNKNOWN):           "flash_high",
    (ComplexityTier.TIER_3, ReportType.UNKNOWN):           "pro_medium",
    (ComplexityTier.TIER_4, ReportType.UNKNOWN):           "pro_high",
}

# ---------------------------------------------------------------------------
# PlainLanguageAgent always runs Flash-Lite for patient mode,
# Flash-Low for doctor mode (jargon rewrite, no new reasoning needed)
# ---------------------------------------------------------------------------

PLAIN_LANGUAGE_CONFIGS: dict[UserMode, str] = {
    UserMode.PATIENT: "flash_lite_low",
    UserMode.DOCTOR:  "flash_low",
}


class ModelSelectorHarness:
    """
    Stateless harness that maps a QueryProfile to a ModelConfig.

    Usage:
        harness = ModelSelectorHarness()
        result = harness.select(query_profile)
        # result.model_config has the fully resolved config for this request
    """

    def select(self, profile: QueryProfile) -> SelectionResult:
        """
        Main entry point. Apply override rules first, then tier matrix.
        Returns a SelectionResult with model config + audit trail.
        """
        tier_overridden  = False
        override_reason: Optional[str] = None

        # Step 1: override rules (hard constraints, clinical safety)
        config_key, tier_overridden, override_reason = self._apply_overrides(profile)

        # Step 2: tier matrix fallback when no override matched
        if config_key is None:
            matrix_key = (profile.complexity_tier, profile.report_type)
            config_key = TIER_MATRIX.get(matrix_key, "pro_medium")

        # Step 3: resolve from registry
        config = get_config(config_key)

        # Step 4: apply dynamic flag overrides from the profile
        config = self._apply_profile_flags(config, profile)

        rationale = self._build_rationale(
            profile, config_key, tier_overridden, override_reason
        )

        logger.info(
            "ModelSelectorHarness: %s → %s (%s thinking) | override=%s",
            f"{profile.complexity_tier.value}/{profile.report_type.value}",
            config.model_id,
            config.thinking_level.value,
            tier_overridden,
        )

        return SelectionResult(
            query_profile=profile,
            model_config=config,
            specialist_agent_name=profile.specialist_agent,
            selection_rationale=rationale,
            tier_overridden=tier_overridden,
            override_reason=override_reason,
        )

    def select_plain_language_config(self, user_mode: UserMode) -> ModelConfig:
        """
        Separate selector for the PlainLanguageAgent — always runs after the
        specialist agent and only needs to rewrite, not reason.
        """
        return get_config(PLAIN_LANGUAGE_CONFIGS[user_mode])

    # -----------------------------------------------------------------------
    # Override rules — evaluated in priority order, first match wins
    # -----------------------------------------------------------------------

    def _apply_overrides(
        self,
        profile: QueryProfile,
    ) -> tuple[Optional[str], bool, Optional[str]]:
        """
        Returns (config_key | None, was_overridden, reason).
        None means no override — fall through to tier matrix.
        """

        # Rule 1: TIER_4 always gets Pro+HIGH, no exceptions
        if profile.complexity_tier == ComplexityTier.TIER_4:
            return (
                "pro_high",
                True,
                "TIER_4 queries always use Gemini 3.1 Pro at HIGH thinking — "
                "clinical expert-level reasoning required",
            )

        # Rule 2: Genomics germline + TIER_1 cannot go below Flash+MEDIUM
        # A patient asking "what does pathogenic mean?" about their BRCA2 result
        # still needs a model that understands variant classification context.
        if (
            profile.report_type == ReportType.GENOMICS_GERMLINE
            and profile.complexity_tier == ComplexityTier.TIER_1
        ):
            return (
                "flash_medium",
                True,
                "Germline genomics: minimum Flash+MEDIUM enforced for "
                "clinical safety even on simple queries",
            )

        # Rule 3: Oncology somatic never below Pro — treatment implications
        if (
            profile.report_type == ReportType.ONCOLOGY_SOMATIC
            and profile.complexity_tier in (ComplexityTier.TIER_1, ComplexityTier.TIER_2)
        ):
            return (
                "pro_medium",
                True,
                "Oncology somatic: minimum Pro+MEDIUM enforced — "
                "treatment and therapy matching requires flagship reasoning",
            )

        # Rule 4: Follow-up clarification on already-interpreted report
        # Drop one tier to save cost — the model already has session context
        if profile.is_followup_question:
            downgraded = self._downgrade_tier(profile)
            if downgraded:
                return (
                    downgraded,
                    True,
                    "Follow-up clarification: tier downgraded one level — "
                    "session context already contains interpreted report",
                )

        # Rule 5: Code execution required → ensure model supports it
        if profile.requires_code_execution:
            config_key = self._ensure_code_execution_capable(profile)
            if config_key:
                return (
                    config_key,
                    True,
                    "Code execution required: upgraded to Flash+MEDIUM minimum",
                )

        return None, False, None

    def _downgrade_tier(self, profile: QueryProfile) -> Optional[str]:
        """
        Drop the tier by one level for follow-up queries.
        Does not downgrade below flash_lite_low.
        Does not downgrade genomics below flash_medium.
        """
        downgrade_map: dict[str, str] = {
            "pro_high":         "pro_medium",
            "pro_medium":       "flash_high",
            "flash_high":       "flash_medium",
            "flash_medium":     "flash_low",
            "flash_low":        "flash_lite_low",
            "flash_lite_low":   "flash_lite_low",  # floor
        }
        matrix_key = (profile.complexity_tier, profile.report_type)
        base_key = TIER_MATRIX.get(matrix_key, "flash_medium")

        downgraded = downgrade_map.get(base_key)

        # Safety: genomics follow-ups never below flash_medium
        if (
            profile.report_type in (ReportType.GENOMICS_GERMLINE, ReportType.PRENATAL)
            and downgraded in ("flash_low", "flash_lite_low", "flash_lite_medium")
        ):
            return "flash_medium"

        return downgraded

    def _ensure_code_execution_capable(self, profile: QueryProfile) -> Optional[str]:
        """
        If the tier-matrix would give a Flash-Lite config (no code execution),
        upgrade to flash_medium which has code execution enabled.
        """
        matrix_key = (profile.complexity_tier, profile.report_type)
        base_key = TIER_MATRIX.get(matrix_key, "flash_medium")
        base_config = MODEL_REGISTRY.get(base_key)

        if base_config and not base_config.use_code_execution:
            return "flash_medium"
        return None

    def _apply_profile_flags(
        self,
        config: ModelConfig,
        profile: QueryProfile,
    ) -> ModelConfig:
        """
        Dynamically override individual flags on the resolved config
        based on specific profile signals. Returns a mutated copy.
        """
        from dataclasses import replace

        overrides: dict = {}

        # Always enable search grounding when profile requests it
        if profile.requires_search_grounding and not config.use_search_grounding:
            overrides["use_search_grounding"] = True

        # Always enable code execution when profile requests it
        if profile.requires_code_execution and not config.use_code_execution:
            overrides["use_code_execution"] = True

        return replace(config, **overrides) if overrides else config

    @staticmethod
    def _build_rationale(
        profile: QueryProfile,
        config_key: str,
        overridden: bool,
        override_reason: Optional[str],
    ) -> str:
        """Build a human-readable audit string for Cloud Trace logging."""
        parts = [
            f"query='{profile.complexity_tier.value}' "
            f"report='{profile.report_type.value}' "
            f"mode='{profile.user_mode.value}' "
            f"→ config='{config_key}'"
        ]
        if overridden and override_reason:
            parts.append(f"[OVERRIDE: {override_reason}]")
        if profile.is_followup_question:
            parts.append("[follow-up: cost-optimised]")
        parts.append(f"classifier: {profile.classifier_reasoning}")
        return " | ".join(parts)
