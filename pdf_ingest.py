"""
tools/pdf_ingest.py
Native PDF ingestion — no Document AI, no OCR preprocessing.

Gemini 3.1 Pro natively reads PDFs up to the 1M token context window.
This tool loads an ADK Artifact from GCS and returns it as a Part
ready for direct multimodal injection into any Gemini call.

Design decision: We skip Document AI for standard lab reports because
Gemini 3.1 Pro's native PDF parsing handles mixed text+table+image PDFs
with equivalent accuracy and lower latency. Document AI would only add
value for handwritten or severely degraded scan quality documents.
"""

from __future__ import annotations

import base64
import logging
import mimetypes
from pathlib import Path

from google.adk.tools import FunctionTool, ToolContext
from vertexai.generative_models import Part

logger = logging.getLogger(__name__)

# Maximum PDF size we accept (20 MB — covers even dense WGS reports)
MAX_PDF_BYTES = 20 * 1024 * 1024


async def load_pdf_as_part(
    artifact_name: str,
    tool_context: ToolContext,
) -> dict:
    """
    ADK FunctionTool: Load a stored PDF Artifact and return metadata.
    The actual Part object is stored in session state for the calling agent.

    Args:
        artifact_name: The ADK artifact key (e.g. "report_20260505.pdf")
        tool_context:  Injected by ADK — provides access to Artifact service

    Returns:
        dict with keys: success, filename, size_bytes, mime_type, page_hint
    """
    try:
        artifact = await tool_context.load_artifact(artifact_name)

        if artifact is None:
            return {
                "success": False,
                "error": f"Artifact '{artifact_name}' not found in session store.",
            }

        raw_bytes: bytes = artifact.inline_data.data
        mime_type: str   = artifact.inline_data.mime_type or "application/pdf"

        if len(raw_bytes) > MAX_PDF_BYTES:
            return {
                "success": False,
                "error": (
                    f"PDF size {len(raw_bytes) / 1024 / 1024:.1f} MB exceeds "
                    f"20 MB limit. Please upload a compressed version."
                ),
            }

        # Build the Part — this is what gets injected into Gemini's content list
        pdf_part = Part.from_data(data=raw_bytes, mime_type=mime_type)

        # Store the Part in session state so the calling agent can retrieve it
        # without re-loading the artifact from GCS
        tool_context.state["_pdf_part"]      = pdf_part
        tool_context.state["_pdf_name"]      = artifact_name
        tool_context.state["_pdf_size_bytes"] = len(raw_bytes)

        logger.info(
            "PDF loaded: %s  size=%.1f KB  mime=%s",
            artifact_name,
            len(raw_bytes) / 1024,
            mime_type,
        )

        return {
            "success":    True,
            "filename":   artifact_name,
            "size_bytes": len(raw_bytes),
            "mime_type":  mime_type,
            # Rough token estimate: ~1000 tokens per PDF page at standard density
            "page_hint":  f"Estimated {len(raw_bytes) // 4000} pages",
        }

    except Exception as exc:
        logger.exception("PDF load failed for artifact '%s'", artifact_name)
        return {"success": False, "error": str(exc)}


async def save_pdf_artifact(
    pdf_bytes: bytes,
    filename: str,
    tool_context: ToolContext,
) -> dict:
    """
    ADK FunctionTool: Store an uploaded PDF as a session artifact in GCS.
    Called at upload time before any agent processes the file.

    Args:
        pdf_bytes:    Raw PDF bytes from the upload endpoint
        filename:     Original filename from the user's upload
        tool_context: Injected by ADK

    Returns:
        dict with artifact_name (the key to use in load_pdf_as_part)
    """
    from google.adk.artifacts import Artifact, InlineData

    if not filename.lower().endswith(".pdf"):
        return {"success": False, "error": "Only PDF files are supported."}

    if len(pdf_bytes) > MAX_PDF_BYTES:
        return {
            "success": False,
            "error": f"File too large ({len(pdf_bytes) / 1024 / 1024:.1f} MB). Max 20 MB.",
        }

    artifact = Artifact(
        inline_data=InlineData(
            data=pdf_bytes,
            mime_type="application/pdf",
        )
    )

    await tool_context.save_artifact(filename, artifact)
    logger.info("Artifact saved: %s  (%d bytes)", filename, len(pdf_bytes))

    return {
        "success":       True,
        "artifact_name": filename,
        "size_bytes":    len(pdf_bytes),
    }


def get_pdf_part_from_state(tool_context: ToolContext) -> Part | None:
    """
    Utility: retrieve the cached PDF Part from session state.
    Used by specialist agents to build their multimodal content list.
    """
    return tool_context.state.get("_pdf_part")


def build_multimodal_content(
    text_prompt: str,
    tool_context: ToolContext,
) -> list:
    """
    Build the content list for a Gemini multimodal call:
      [PDF Part, text prompt Part]

    Gemini processes the PDF first (left-to-right in the content list),
    then reads the instruction. This ordering improves grounding accuracy
    because the model has already processed the document context before
    seeing the question.
    """
    pdf_part = get_pdf_part_from_state(tool_context)
    text_part = Part.from_text(text_prompt)

    if pdf_part is None:
        logger.warning("No PDF Part in session state — returning text-only content")
        return [text_part]

    return [pdf_part, text_part]


# ---------------------------------------------------------------------------
# Register as ADK FunctionTools
# ---------------------------------------------------------------------------

pdf_load_tool = FunctionTool(func=load_pdf_as_part)
pdf_save_tool = FunctionTool(func=save_pdf_artifact)
