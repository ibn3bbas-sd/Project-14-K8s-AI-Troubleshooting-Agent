"""FastAPI entry point.

Endpoints (all JSON):
  POST /api/sessions                     start a session  {"query": "..."}
  GET  /api/sessions                     recent sessions
  GET  /api/sessions/{id}                full session (events, pending plan, usage)
  POST /api/sessions/{id}/message        follow-up question or guidance {"text": "..."}
  POST /api/sessions/{id}/decision       approve/reject a plan {"approve": bool, "issue_ids": [...], "guidance": ""}
  POST /api/sessions/{id}/retry          retry after an error
  DELETE /api/sessions/{id}
  GET  /api/config                       model and safety settings for the UI
  GET  /healthz, GET /metrics
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, HTTPException, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field

from app.agent.runner import Agent, SessionBusy
from app.config import settings
from app.infra.logging_config import configure_logging
from app.infra.store import build_store

configure_logging(os.environ.get("LOG_LEVEL", "INFO"))

state: dict[str, Agent] = {}


@asynccontextmanager
async def lifespan(_: FastAPI):
    state["agent"] = Agent(build_store())
    yield


app = FastAPI(title="K8s AI Troubleshooting Agent", lifespan=lifespan)


def agent() -> Agent:
    return state["agent"]


class QueryIn(BaseModel):
    query: str = Field(min_length=1, max_length=8000)


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=8000)


class DecisionIn(BaseModel):
    approve: bool
    issue_ids: list[str] | None = None
    guidance: str = Field(default="", max_length=8000)


def _public(session: dict) -> dict:
    """Session as the UI sees it (the raw API history stays server-side)."""
    pending = session.get("pending")
    return {
        "id": session["id"],
        "title": session["title"],
        "status": session["status"],
        "phase": session["phase"],
        "busy": agent().is_busy(session["id"]),
        "created_at": session["created_at"],
        "updated_at": session["updated_at"],
        "events": session["events"],
        "pending": {k: pending[k] for k in ("summary", "issues")} if pending else None,
        "approved_issue_ids": session.get("approved_issue_ids", []),
        "outcome": session.get("outcome"),
        "usage": session["usage"],
        "model": session.get("model"),
    }


def _get(session_id: str) -> dict:
    session = agent().store.get(session_id)
    if session is None:
        raise HTTPException(404, "Session not found")
    return session


def _guarded(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except KeyError:
        raise HTTPException(404, "Session not found") from None
    except SessionBusy as exc:
        raise HTTPException(409, str(exc)) from None


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/api/config")
def get_config() -> dict:
    return {
        "model": settings.model,
        "effort": settings.effort,
        "cluster_name": settings.cluster_name,
        "write_tools_enabled": settings.enable_write_tools,
        "protected_namespaces": sorted(settings.all_protected_namespaces),
    }


@app.post("/api/sessions", status_code=201)
def create_session(body: QueryIn, background: BackgroundTasks) -> dict:
    session = agent().create(body.query)
    background.add_task(agent().run, session["id"], [{"type": "text", "text": body.query}])
    return _public(session)


@app.get("/api/sessions")
def list_sessions(limit: int = 50) -> list[dict]:
    return agent().store.list(limit=min(max(limit, 1), 200))


@app.get("/api/sessions/{session_id}")
def get_session(session_id: str) -> dict:
    return _public(_get(session_id))


@app.post("/api/sessions/{session_id}/message")
def send_message(session_id: str, body: MessageIn, background: BackgroundTasks) -> dict:
    content = _guarded(agent().begin_message, session_id, body.text)
    background.add_task(agent().run, session_id, content)
    return _public(_get(session_id))


@app.post("/api/sessions/{session_id}/decision")
def decide(session_id: str, body: DecisionIn, background: BackgroundTasks) -> dict:
    content = _guarded(
        agent().begin_decision,
        session_id,
        approve=body.approve,
        issue_ids=body.issue_ids,
        guidance=body.guidance,
    )
    background.add_task(agent().run, session_id, content)
    return _public(_get(session_id))


@app.post("/api/sessions/{session_id}/retry")
def retry(session_id: str, background: BackgroundTasks) -> dict:
    content = _guarded(agent().begin_retry, session_id)
    background.add_task(agent().run, session_id, content)
    return _public(_get(session_id))


@app.delete("/api/sessions/{session_id}", status_code=204)
def delete_session(session_id: str) -> Response:
    if agent().is_busy(session_id):
        raise HTTPException(409, "The agent is still working on this session.")
    agent().store.delete(session_id)
    return Response(status_code=204)
