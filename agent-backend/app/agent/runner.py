"""The agent loop and its approval gate.

Workflow (enforced in code, not only in the prompt):

    diagnosis --propose_remediation--> awaiting_approval --approve--> remediation
        ^                                    |                           |
        |                                 reject /                 report_outcome
        +----------------------------------guidance-------------------+

* Every request sends the same tool list, so the prompt cache stays warm. Write
  tools are *refused at execution time* unless the session is in the
  remediation phase that an operator approval opened.
* propose_remediation pauses the loop with its tool_use unanswered; the
  operator's decision becomes that tool's result when the loop resumes.
* History is append-only: assistant content is stored exactly as returned
  (thinking blocks included) and sent back unchanged.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

from app.agent.prompts import system_prompt
from app.config import settings
from app.infra import metrics
from app.infra.claude import ClaudeClient, block_to_param
from app.infra.store import SessionStore
from app.infra.usage import add_usage, empty_usage
from app.tools import REGISTRY, api_tool_definitions
from app.tools.control import PROPOSE, REPORT
from app.tools.kube import ToolError

log = logging.getLogger(__name__)


class SessionBusy(Exception):
    """The session is running or not in a state that accepts this action."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = text[: limit // 3]
    tail = text[-(limit - len(head)) :]
    return (
        f"{head}\n... [output truncated: {len(text) - limit} characters omitted; "
        f"narrow the query (namespace, selector, tail_lines) for more detail] ...\n{tail}"
    )


def new_session(query: str) -> dict[str, Any]:
    now = _now()
    return {
        "id": uuid.uuid4().hex[:12],
        "title": query.strip().splitlines()[0][:100] if query.strip() else "Session",
        "status": "idle",  # idle | running | awaiting_approval | error
        "phase": "diagnosis",  # diagnosis | remediation
        "created_at": now,
        "updated_at": now,
        "messages": [],
        "events": [],
        "pending": None,
        "approved_issue_ids": [],
        "writes_used": 0,
        "outcome": None,
        "usage": empty_usage(),
        "model": settings.model,
    }


class Agent:
    def __init__(self, store: SessionStore, claude: ClaudeClient | None = None) -> None:
        self.store = store
        self.claude = claude or ClaudeClient()
        self._guard = threading.Lock()
        self._busy: set[str] = set()
        self._tools = api_tool_definitions(include_write=settings.enable_write_tools)
        self._system = system_prompt()
        self._pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="tool")

    # ------------------------------------------------------------------ public

    def create(self, query: str) -> dict[str, Any]:
        session = new_session(query)
        self._event(session, "user", text=query)
        session["status"] = "running"
        self.store.save(session)
        self._claim(session["id"])
        metrics.SESSIONS.inc()
        return session

    def begin_message(self, session_id: str, text: str) -> list[dict]:
        """Validate a follow-up message and return the user content to send."""
        session = self._load(session_id)
        if session["status"] == "awaiting_approval":
            # Free text while a plan is pending counts as guidance on that plan.
            return self.begin_decision(session_id, approve=False, guidance=text)
        self._claim(session_id)
        self._event(session, "user", text=text)
        session["status"] = "running"
        self.store.save(session)
        return [{"type": "text", "text": text}]

    def begin_decision(
        self,
        session_id: str,
        *,
        approve: bool,
        issue_ids: list[str] | None = None,
        guidance: str = "",
    ) -> list[dict]:
        session = self._load(session_id)
        pending = session.get("pending")
        if session["status"] != "awaiting_approval" or not pending:
            raise SessionBusy("This session has no plan waiting for a decision.")
        known = [i["id"] for i in pending["issues"]]
        selected = [i for i in (issue_ids or known) if i in known]
        if approve and not selected:
            raise SessionBusy(f"Select at least one issue to approve. Known issues: {known}")
        self._claim(session_id)

        if approve:
            metrics.APPROVALS.labels(decision="approved").inc()
            if settings.enable_write_tools:
                session["phase"] = "remediation"
                session["approved_issue_ids"] = selected
                session["writes_used"] = 0
                text = (
                    f"The operator APPROVED these issues: {', '.join(selected)}."
                    + (f" Not approved (do not change): {', '.join(i for i in known if i not in selected)}." if len(selected) < len(known) else "")
                    + " Write tools are now unlocked for these fixes only. Apply the approved steps,"
                    " verify with read tools, then call report_outcome."
                )
            else:
                text = (
                    f"The operator accepted issues {', '.join(selected)}, but this deployment is "
                    "read-only. Give exact kubectl commands the operator can run to apply the fix, "
                    "and what to check afterwards."
                )
        else:
            metrics.APPROVALS.labels(decision="rejected").inc()
            session["phase"] = "diagnosis"
            text = "The operator did NOT approve the plan. Do not change anything."
        if guidance.strip():
            text += f"\nOperator guidance: {guidance.strip()}"
        if not approve:
            text += (
                "\nTake the guidance into account: investigate further if needed and propose a "
                "revised plan, or answer the operator if no change is wanted."
            )

        self._event(session, "decision", approved=approve, issue_ids=selected if approve else [], guidance=guidance)
        content = list(pending.get("sibling_results") or [])
        content.append({"type": "tool_result", "tool_use_id": pending["tool_use_id"], "content": text})
        session["pending"] = None
        session["status"] = "running"
        self.store.save(session)
        return content

    def begin_retry(self, session_id: str) -> list[dict] | None:
        session = self._load(session_id)
        if session["status"] == "awaiting_approval":
            raise SessionBusy("A plan is waiting for a decision; approve or reject it instead.")
        self._claim(session_id)
        session["status"] = "running"
        self._event(session, "status", text="Retrying")
        self.store.save(session)
        last = session["messages"][-1] if session["messages"] else None
        if last and last["role"] == "user":
            return None  # the last request failed; resend the same history
        return [{"type": "text", "text": "Please retry: re-check the current state and continue."}]

    def run(self, session_id: str, user_content: list[dict] | None) -> None:
        """Run the loop until it needs the operator. Called in a worker thread."""
        metrics.RUNS_ACTIVE.inc()
        session = self.store.get(session_id)
        try:
            if session is None:
                return
            if user_content:
                session["messages"].append({"role": "user", "content": user_content})
            self._loop(session)
        except Exception as exc:  # noqa: BLE001 - surface every failure in the UI
            log.exception("agent run failed", extra={"session": session_id})
            if session is not None:
                session["status"] = "error"
                self._event(session, "error", text=f"{type(exc).__name__}: {exc}")
        finally:
            if session is not None:
                if session["status"] == "running":
                    session["status"] = "idle"
                self.store.save(session)
            self._release(session_id)
            metrics.RUNS_ACTIVE.dec()

    # ------------------------------------------------------------------ loop

    def _loop(self, session: dict[str, Any]) -> None:
        for _ in range(settings.max_iterations):
            response = self.claude.create(
                system=self._system, tools=self._tools, messages=session["messages"]
            )
            add_usage(session["usage"], response.usage)
            content = [block_to_param(b) for b in response.content]
            session["messages"].append({"role": "assistant", "content": content})
            for block in content:
                if block.get("type") == "text" and block.get("text", "").strip():
                    self._event(session, "assistant", text=block["text"])
                elif block.get("type") == "thinking" and block.get("thinking", "").strip():
                    self._event(session, "thinking", text=block["thinking"])
                elif block.get("type") == "fallback":
                    self._event(session, "status", text="A safety classifier declined; the request was continued on a fallback model.")

            if response.stop_reason == "refusal":
                details = getattr(response, "stop_details", None)
                category = getattr(details, "category", None) if details else None
                session["status"] = "error"
                self._event(session, "error", text=f"Claude declined this request (category: {category}).")
                return

            tool_uses = [b for b in content if b.get("type") == "tool_use"]
            if response.stop_reason == "max_tokens" and not tool_uses:
                self._event(session, "error", text="The response hit max_tokens and was cut off. Send a follow-up to continue.")
                session["status"] = "idle"
                return
            if not tool_uses:
                session["status"] = "idle"
                self.store.save(session)
                return

            results, paused = self._handle_tool_uses(session, tool_uses, truncated=response.stop_reason == "max_tokens")
            if paused:
                return
            session["messages"].append({"role": "user", "content": results})
            self.store.save(session)

        self._event(session, "error", text=f"Stopped after {settings.max_iterations} model turns. Send a message to continue.")
        session["status"] = "idle"

    def _handle_tool_uses(
        self, session: dict[str, Any], tool_uses: list[dict], truncated: bool
    ) -> tuple[list[dict], bool]:
        results: dict[str, dict] = {}
        plan_call: dict | None = None
        runnable: list[dict] = []
        for tu in tool_uses:
            self._event(
                session, "tool_call", id=tu["id"], name=tu["name"],
                category=getattr(REGISTRY.get(tu["name"]), "category", "unknown"), input=tu.get("input", {}),
            )
            if truncated:
                results[tu["id"]] = self._result(tu, "Your response hit max_tokens, so this call may be incomplete. Retry it.", error=True)
            elif tu["name"] == PROPOSE and plan_call is None:
                plan_call = tu
            elif tu["name"] == PROPOSE:
                results[tu["id"]] = self._result(tu, "Only one plan can be proposed at a time.", error=True)
            elif tu["name"] == REPORT:
                results[tu["id"]] = self._record_outcome(session, tu)
            else:
                runnable.append(tu)

        reads = [tu for tu in runnable if getattr(REGISTRY.get(tu["name"]), "category", "") == "read"]
        others = [tu for tu in runnable if tu not in reads]
        for tu, res in zip(reads, self._pool.map(lambda t: self._execute(session, t), reads)):
            results[tu["id"]] = res
        for tu in others:  # writes run one at a time, in the order Claude gave
            results[tu["id"]] = self._execute(session, tu)

        ordered = [results[tu["id"]] for tu in tool_uses if tu["id"] in results]
        if plan_call is not None:
            issues = (plan_call.get("input") or {}).get("issues") or []
            if not issues:
                ordered.append(self._result(plan_call, "A plan needs at least one issue.", error=True))
                return ordered, False
            session["pending"] = {
                "tool_use_id": plan_call["id"],
                "summary": plan_call["input"].get("summary", ""),
                "issues": issues,
                "sibling_results": ordered,
            }
            session["phase"] = "diagnosis"
            session["status"] = "awaiting_approval"
            self._event(session, "plan", tool_use_id=plan_call["id"], summary=plan_call["input"].get("summary", ""), issues=issues)
            self.store.save(session)
            return ordered, True
        return ordered, False

    def _record_outcome(self, session: dict[str, Any], tu: dict) -> dict:
        data = tu.get("input") or {}
        session["outcome"] = {"resolved": bool(data.get("resolved")), "summary": data.get("summary", ""), "at": _now()}
        self._event(session, "outcome", resolved=bool(data.get("resolved")), summary=data.get("summary", ""), remaining=data.get("remaining_issues", ""))
        # Close the write window: any further change needs a new approval.
        session["phase"] = "diagnosis"
        session["approved_issue_ids"] = []
        return self._result(tu, "Outcome recorded. Write tools are locked again.")

    def _execute(self, session: dict[str, Any], tu: dict) -> dict:
        name, args = tu["name"], tu.get("input") or {}
        tool = REGISTRY.get(name)
        if tool is None:
            return self._finish(session, tu, "unknown", f"Unknown tool '{name}'.", error=True)
        if tool.category == "write":
            if not settings.enable_write_tools:
                return self._finish(session, tu, tool.category, "Write tools are disabled in this deployment.", error=True)
            if session["phase"] != "remediation":
                return self._finish(
                    session, tu, tool.category,
                    "Write tools are locked: no approved plan is active. Call propose_remediation and wait for the operator.",
                    error=True,
                )
            if not args.get("dry_run"):
                if session["writes_used"] >= settings.max_writes_per_approval:
                    return self._finish(
                        session, tu, tool.category,
                        f"Write budget for this approval ({settings.max_writes_per_approval}) is used up. Report the outcome or propose a new plan.",
                        error=True,
                    )
                session["writes_used"] += 1
        try:
            output = tool.fn(**args)
            return self._finish(session, tu, tool.category, output)
        except ToolError as exc:
            return self._finish(session, tu, tool.category, str(exc), error=True)
        except TypeError as exc:
            return self._finish(session, tu, tool.category, f"Invalid arguments for {name}: {exc}", error=True)
        except Exception as exc:  # noqa: BLE001
            log.exception("tool failed", extra={"tool": name})
            return self._finish(session, tu, tool.category, f"Unexpected error in {name}: {type(exc).__name__}: {exc}", error=True)

    def _finish(self, session: dict[str, Any], tu: dict, category: str, output: str, error: bool = False) -> dict:
        metrics.TOOL_CALLS.labels(tool=tu["name"], category=category, status="error" if error else "ok").inc()
        output = output if isinstance(output, str) else json.dumps(output, default=str)
        output = _truncate(output, settings.max_tool_result_chars)
        self._event(session, "tool_result", id=tu["id"], name=tu["name"], ok=not error, output=_truncate(output, 6000))
        return self._result(tu, output, error)

    @staticmethod
    def _result(tu: dict, text: str, error: bool = False) -> dict:
        block: dict[str, Any] = {"type": "tool_result", "tool_use_id": tu["id"], "content": text or "(empty)"}
        if error:
            block["is_error"] = True
        return block

    # ------------------------------------------------------------------ helpers

    def _event(self, session: dict[str, Any], kind: str, **data: Any) -> None:
        session["events"].append({"seq": len(session["events"]), "type": kind, "ts": _now(), **data})
        session["updated_at"] = _now()

    def _load(self, session_id: str) -> dict[str, Any]:
        session = self.store.get(session_id)
        if session is None:
            raise KeyError(session_id)
        if session_id in self._busy:
            raise SessionBusy("The agent is still working on this session.")
        return session

    def _claim(self, session_id: str) -> None:
        with self._guard:
            if session_id in self._busy:
                raise SessionBusy("The agent is still working on this session.")
            self._busy.add(session_id)

    def _release(self, session_id: str) -> None:
        with self._guard:
            self._busy.discard(session_id)

    def is_busy(self, session_id: str) -> bool:
        return session_id in self._busy
