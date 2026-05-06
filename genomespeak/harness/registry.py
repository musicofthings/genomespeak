"""
harness/registry.py
Central registry of every Gemini model configuration used in GenomeSpeak.

All model strings are confirmed available on Vertex AI as of May 2026.
Reference: https://docs.cloud.google.com/vertex-ai/generative-ai/docs/models/
"""

from __future__ import annotations

from genomespeak.harness.models import ModelConfig, ThinkingLevel


# ---------------------------------------------------------------------------
# Model string constants (Vertex AI endpoint identifiers)
# ---------------------------------------------------------------------------

GEMINI_31_PRO          = "gemini-3.1-pro-preview"        # Flagship — reasoning depth
GEMINI_3_FLASH         = "gemini-3-flash-preview"         # Speed + quality balance
GEMINI_31_FLASH_LITE   = "gemini-3.1-flash-lite-preview"  # Cost-optimised, high volume


# ---------------------------------------------------------------------------
# Config presets — named by (model, thinking_level)
# ---------------------------------------------------------------------------

MODEL_REGISTRY: dict[str, ModelConfig] = {

    # --- Gemini 3.1 Pro ---

    "pro_high": ModelConfig(
        model_id=GEMINI_31_PRO,
        thinking_level=ThinkingLevel.HIGH,
        temperature=0.05,          # Near-deterministic for clinical reasoning
        max_output_tokens=16384,   # Complex variant reports need long output
        use_search_grounding=True,
        use_code_execution=True,
        use_rag=True,
        cost_tier="high",
    ),

    "pro_medium": ModelConfig(
        model_id=GEMINI_31_PRO,
        thinking_level=ThinkingLevel.MEDIUM,
        temperature=0.1,
        max_output_tokens=8192,
        use_search_grounding=True,
        use_code_execution=False,
        use_rag=True,
        cost_tier="high",
    ),

    "pro_low": ModelConfig(
        model_id=GEMINI_31_PRO,
        thinking_level=ThinkingLevel.LOW,
        temperature=0.2,
        max_output_tokens=4096,
        use_search_grounding=False,
        use_code_execution=False,
        use_rag=False,
        cost_tier="high",
    ),

    # --- Gemini 3 Flash ---

    "flash_high": ModelConfig(
        model_id=GEMINI_3_FLASH,
        thinking_level=ThinkingLevel.HIGH,
        temperature=0.1,
        max_output_tokens=8192,
        use_search_grounding=True,
        use_code_execution=True,
        use_rag=True,
        cost_tier="medium",
    ),

    "flash_medium": ModelConfig(
        model_id=GEMINI_3_FLASH,
        thinking_level=ThinkingLevel.MEDIUM,
        temperature=0.15,
        max_output_tokens=4096,
        use_search_grounding=False,
        use_code_execution=True,
        use_rag=True,
        cost_tier="medium",
    ),

    "flash_low": ModelConfig(
        model_id=GEMINI_3_FLASH,
        thinking_level=ThinkingLevel.LOW,
        temperature=0.2,
        max_output_tokens=2048,
        use_search_grounding=False,
        use_code_execution=False,
        use_rag=False,
        cost_tier="low",
    ),

    # --- Gemini 3.1 Flash-Lite ---

    "flash_lite_medium": ModelConfig(
        model_id=GEMINI_31_FLASH_LITE,
        thinking_level=ThinkingLevel.MEDIUM,
        temperature=0.2,
        max_output_tokens=2048,
        use_search_grounding=False,
        use_code_execution=False,
        use_rag=False,
        cost_tier="low",
    ),

    "flash_lite_low": ModelConfig(
        model_id=GEMINI_31_FLASH_LITE,
        thinking_level=ThinkingLevel.LOW,
        temperature=0.3,
        max_output_tokens=1024,
        use_search_grounding=False,
        use_code_execution=False,
        use_rag=False,
        cost_tier="low",
    ),
}


def get_config(key: str) -> ModelConfig:
    """Retrieve a named model config. Raises KeyError on unknown key."""
    if key not in MODEL_REGISTRY:
        raise KeyError(
            f"Unknown model config '{key}'. "
            f"Valid keys: {sorted(MODEL_REGISTRY.keys())}"
        )
    return MODEL_REGISTRY[key]
