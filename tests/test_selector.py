"""
tests/test_selector.py
Unit tests for ModelSelectorHarness — verify override rules and tier matrix.
Run with: pytest tests/test_selector.py -v
No GCP credentials needed — pure Python logic.
"""

import pytest

from genomespeak.harness.models import (
    ComplexityTier,
    QueryProfile,
    ReportType,
    ThinkingLevel,
    UserMode,
)
from genomespeak.harness.selector import ModelSelectorHarness


def make_profile(**overrides) -> QueryProfile:
    """Test helper: build a QueryProfile with sensible defaults."""
    defaults = dict(
        complexity_tier=ComplexityTier.TIER_2,
        report_type=ReportType.ROUTINE_LAB,
        user_mode=UserMode.PATIENT,
        requires_search_grounding=False,
        requires_code_execution=False,
        is_followup_question=False,
        specialist_agent="routine_lab",
        classifier_reasoning="test",
    )
    defaults.update(overrides)
    return QueryProfile(**defaults)


class TestTierMatrix:
    harness = ModelSelectorHarness()

    def test_routine_tier1_gets_flash_lite(self):
        profile = make_profile(
            complexity_tier=ComplexityTier.TIER_1,
            report_type=ReportType.ROUTINE_LAB,
        )
        result = self.harness.select(profile)
        assert "flash-lite" in result.model_config.model_id
        assert result.model_config.thinking_level == ThinkingLevel.LOW

    def test_routine_tier4_gets_flash_high(self):
        # TIER_4 override fires before the matrix — routine lab TIER_4 → pro_high
        profile = make_profile(
            complexity_tier=ComplexityTier.TIER_4,
            report_type=ReportType.ROUTINE_LAB,
        )
        result = self.harness.select(profile)
        assert "pro" in result.model_config.model_id
        assert result.model_config.thinking_level == ThinkingLevel.HIGH
        assert result.tier_overridden is True

    def test_routine_tier2_gets_flash_medium(self):
        profile = make_profile(
            complexity_tier=ComplexityTier.TIER_2,
            report_type=ReportType.ROUTINE_LAB,
        )
        result = self.harness.select(profile)
        assert result.model_config.thinking_level == ThinkingLevel.MEDIUM


class TestOverrideRules:
    harness = ModelSelectorHarness()

    def test_tier4_always_pro_high(self):
        """Rule 1: Any TIER_4 query → pro_high, regardless of report type."""
        for report_type in ReportType:
            profile = make_profile(
                complexity_tier=ComplexityTier.TIER_4,
                report_type=report_type,
            )
            result = self.harness.select(profile)
            assert "pro" in result.model_config.model_id, f"Failed for {report_type}"
            assert result.model_config.thinking_level == ThinkingLevel.HIGH
            assert result.tier_overridden is True

    def test_genomics_tier1_never_flash_lite(self):
        """Rule 2: Germline genomics TIER_1 → flash_medium minimum."""
        profile = make_profile(
            complexity_tier=ComplexityTier.TIER_1,
            report_type=ReportType.GENOMICS_GERMLINE,
        )
        result = self.harness.select(profile)
        assert "lite" not in result.model_config.model_id
        assert result.model_config.thinking_level != ThinkingLevel.LOW
        assert result.tier_overridden is True

    def test_oncology_tier1_gets_pro(self):
        """Rule 3: Oncology somatic TIER_1 → pro minimum."""
        profile = make_profile(
            complexity_tier=ComplexityTier.TIER_1,
            report_type=ReportType.ONCOLOGY_SOMATIC,
        )
        result = self.harness.select(profile)
        assert "pro" in result.model_config.model_id
        assert result.tier_overridden is True

    def test_followup_downgrades_tier(self):
        """Rule 4: Follow-up questions get a cheaper model."""
        normal = make_profile(
            complexity_tier=ComplexityTier.TIER_3,
            report_type=ReportType.ROUTINE_LAB,
            is_followup_question=False,
        )
        followup = make_profile(
            complexity_tier=ComplexityTier.TIER_3,
            report_type=ReportType.ROUTINE_LAB,
            is_followup_question=True,
        )
        r_normal  = self.harness.select(normal)
        r_followup = self.harness.select(followup)
        # Follow-up should not be more expensive than the non-followup
        cost_rank = {"low": 0, "medium": 1, "high": 2}
        assert (
            cost_rank[r_followup.model_config.cost_tier]
            <= cost_rank[r_normal.model_config.cost_tier]
        )

    def test_followup_genomics_never_below_flash_medium(self):
        """Rule 4 safety: genomics follow-up never drops below flash_medium."""
        profile = make_profile(
            complexity_tier=ComplexityTier.TIER_2,
            report_type=ReportType.GENOMICS_GERMLINE,
            is_followup_question=True,
        )
        result = self.harness.select(profile)
        assert "lite" not in result.model_config.model_id

    def test_search_grounding_flag_propagates(self):
        """Rule 5: requires_search_grounding forces the flag on even if config had it off."""
        profile = make_profile(
            complexity_tier=ComplexityTier.TIER_1,
            report_type=ReportType.ROUTINE_LAB,
            requires_search_grounding=True,
        )
        result = self.harness.select(profile)
        assert result.model_config.use_search_grounding is True

    def test_code_execution_flag_propagates(self):
        """Rule 5: requires_code_execution forces upgrade to at least flash_medium."""
        profile = make_profile(
            complexity_tier=ComplexityTier.TIER_1,
            report_type=ReportType.ROUTINE_LAB,
            requires_code_execution=True,
        )
        result = self.harness.select(profile)
        assert result.model_config.use_code_execution is True


class TestPlainLanguageConfig:
    harness = ModelSelectorHarness()

    def test_patient_mode_gets_flash_lite(self):
        cfg = self.harness.select_plain_language_config(UserMode.PATIENT)
        assert "lite" in cfg.model_id
        assert cfg.thinking_level == ThinkingLevel.LOW

    def test_doctor_mode_gets_flash_low(self):
        cfg = self.harness.select_plain_language_config(UserMode.DOCTOR)
        assert "lite" not in cfg.model_id  # flash, not flash-lite
        assert cfg.thinking_level == ThinkingLevel.LOW
