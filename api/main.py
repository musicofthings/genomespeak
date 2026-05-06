"""
api/main.py
GenomeSpeak FastAPI backend.

Endpoints:
    POST /upload          — receive PDF, store as ADK Artifact, return artifact_name
    POST /chat            — send query + artifact_name, stream SSE response tokens
    GET  /session/{id}    — retrieve session metadata (report type, mode, history)
    GET  /health          — Cloud Run health check

Streaming: Server-Sent Events (SSE) via StreamingResponse.
Frontend connects with EventSource and appends tokens as they arrive.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import AsyncIterator

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

load_dotenv()
logger = logging.getLogger(__name__)
logging.basicConfig(level=os.getenv("GENOMESPEAK_LOG_LEVEL", "INFO"))

# ---------------------------------------------------------------------------
# Singleton orchestrator — created once at startup, reused across all requests.
# Avoids re-running vertexai.init() and re-constructing GenerativeModel per call.
# ---------------------------------------------------------------------------

_orchestrator = None

def get_orchestrator():
    global _orchestrator
    if _orchestrator is None:
        from genomespeak.agent import GenomeSpeakOrchestrator
        _orchestrator = GenomeSpeakOrchestrator()
    return _orchestrator

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="GenomeSpeak API",
    description="AI-powered lab and genomics report interpreter",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # tighten in production
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# In-memory session store (replace with Firestore in production)
# ---------------------------------------------------------------------------

SESSION_STORE: dict[str, dict] = {}


def get_or_create_session(session_id: str | None) -> tuple[str, dict]:
    if session_id and session_id in SESSION_STORE:
        return session_id, SESSION_STORE[session_id]
    sid = session_id or str(uuid.uuid4())
    SESSION_STORE[sid] = {
        "id":                 sid,
        "created_at":         time.time(),
        "artifact_name":      None,
        "report_type":        None,
        "user_mode":          None,
        "has_prior_report":   False,
        "turn_count":         0,
        "history":            [],
    }
    return sid, SESSION_STORE[sid]


# ---------------------------------------------------------------------------
# PDF upload endpoint
# ---------------------------------------------------------------------------

@app.post("/upload")
async def upload_report(
    file:       UploadFile = File(...),
    session_id: str        = Form(default=None),
    user_mode:  str        = Form(default="patient"),  # "patient" | "doctor"
):
    """
    Receive a PDF upload. Store bytes in session store (in prod: ADK Artifact → GCS).
    Returns: { session_id, artifact_name, filename, size_bytes }
    """
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted.")

    max_bytes = int(os.getenv("GENOMESPEAK_MAX_PDF_MB", 20)) * 1024 * 1024
    pdf_bytes = await file.read()

    if len(pdf_bytes) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum size is {max_bytes // 1024 // 1024} MB.",
        )

    sid, session = get_or_create_session(session_id)

    # Sanitise filename
    safe_name = "".join(c for c in file.filename if c.isalnum() or c in "._-")
    artifact_name = f"{sid[:8]}_{safe_name}"

    # Store in session (production: await tool_context.save_artifact(artifact_name, ...))
    session["artifact_name"]    = artifact_name
    session["pdf_bytes"]        = pdf_bytes
    session["user_mode"]        = user_mode
    session["has_prior_report"] = False

    logger.info(
        "PDF uploaded: session=%s artifact=%s size=%.1f KB mode=%s",
        sid, artifact_name, len(pdf_bytes) / 1024, user_mode,
    )

    return JSONResponse({
        "session_id":    sid,
        "artifact_name": artifact_name,
        "filename":      file.filename,
        "size_bytes":    len(pdf_bytes),
        "user_mode":     user_mode,
    })


# ---------------------------------------------------------------------------
# SSE streaming chat endpoint
# ---------------------------------------------------------------------------

@app.post("/chat")
async def chat(request: Request):
    """
    Accept JSON body: { session_id, query, user_mode? }
    Stream SSE response: data: {"token": "...", "type": "token"}\n\n
    Final event:        data: {"type": "done", "model": "...", "thinking": "..."}\n\n
    """
    body = await request.json()

    session_id = body.get("session_id")
    query      = body.get("query", "").strip()
    user_mode  = body.get("user_mode", "patient")

    if not query:
        raise HTTPException(status_code=400, detail="query is required")

    sid, session = get_or_create_session(session_id)
    session["turn_count"] += 1
    has_prior = session["has_prior_report"]

    # Update user_mode if provided
    if user_mode:
        session["user_mode"] = user_mode

    # Mark that after this turn the session has an interpreted report
    artifact_name = session.get("artifact_name")

    async def event_stream() -> AsyncIterator[bytes]:
        """Yield SSE-formatted bytes to the client."""

        try:
            orchestrator = get_orchestrator()

            # Metadata event — lets the frontend show session context
            meta_event = {
                "type":     "meta",
                "session":  sid,
                "artifact": artifact_name,
                "turn":     session["turn_count"],
            }
            yield f"data: {json.dumps(meta_event)}\n\n".encode()

            # Immediate status so the UI shows activity before any LLM call
            yield f"data: {json.dumps({'type': 'status', 'message': 'Classifying your report…'})}\n\n".encode()

            full_response = []

            async for token in orchestrator.process(
                user_query=query,
                pdf_artifact_name=artifact_name,
                pdf_bytes=session.get("pdf_bytes"),
                session_has_prior_report=has_prior,
            ):
                # Intercept status sentinels emitted by the orchestrator
                if token.startswith('\x00STATUS:') and token.endswith('\x00'):
                    msg = token[8:-1]
                    yield f"data: {json.dumps({'type': 'status', 'message': msg})}\n\n".encode()
                    continue
                full_response.append(token)
                token_event = {"type": "token", "token": token}
                yield f"data: {json.dumps(token_event)}\n\n".encode()
                await asyncio.sleep(0)

            # Mark session as having processed a report
            session["has_prior_report"] = True
            full_text = "".join(full_response)
            session["history"].append({
                "role":    "user",
                "content": query,
            })
            session["history"].append({
                "role":    "assistant",
                "content": full_text,
            })

            done_event = {
                "type":       "done",
                "total_chars": len(full_text),
            }
            yield f"data: {json.dumps(done_event)}\n\n".encode()

        except Exception as exc:
            logger.exception("Chat stream error: %s", exc)
            error_event = {
                "type":    "error",
                "message": "Something went wrong processing your report. Please try again.",
            }
            yield f"data: {json.dumps(error_event)}\n\n".encode()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":       "no-cache",
            "X-Accel-Buffering":   "no",   # nginx: disable buffering for SSE
        },
    )


# ---------------------------------------------------------------------------
# Session metadata endpoint
# ---------------------------------------------------------------------------

@app.get("/session/{session_id}")
async def get_session(session_id: str):
    if session_id not in SESSION_STORE:
        raise HTTPException(status_code=404, detail="Session not found")
    session = SESSION_STORE[session_id]
    # Don't expose raw pdf_bytes in the API response
    safe = {k: v for k, v in session.items() if k != "pdf_bytes"}
    return JSONResponse(safe)


# ---------------------------------------------------------------------------
# Health check (Cloud Run requires 200 on /)
# ---------------------------------------------------------------------------

@app.get("/")
async def root():
    index = Path(__file__).parent.parent / "frontend" / "index.html"
    if index.exists():
        return FileResponse(str(index), media_type="text/html")
    return {"status": "ok", "service": "genomespeak", "version": "1.0.0"}


@app.get("/health")
async def health():
    return {"status": "ok", "service": "genomespeak", "version": "1.0.0"}


# ---------------------------------------------------------------------------
# Local dev entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "api.main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8080)),
        reload=True,
        log_level="info",
    )
