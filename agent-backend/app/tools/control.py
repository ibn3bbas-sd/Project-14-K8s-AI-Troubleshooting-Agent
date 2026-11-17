"""Control tools: they change the agent's workflow, not the cluster.

The runner intercepts these by name; the functions below only run if
something calls them directly.
"""

from __future__ import annotations

from app.tools.registry import tool

PROPOSE = "propose_remediation"
REPORT = "report_outcome"


@tool(
    category="control",
    description="""Submit your diagnosis and a remediation plan to the human operator and pause
until they decide. Call this once the root cause is supported by evidence and a change is
needed. Write tools stay locked until the operator approves; the result of this call is
the operator's decision (approved issue ids, rejection, or guidance). List each distinct
problem as its own issue so the operator can approve them separately.""",
    properties={
        "summary": {"type": "string", "description": "Two to four sentences on what is wrong."},
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Short stable id, e.g. issue-1."},
                    "title": {"type": "string"},
                    "severity": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
                    "resource": {"type": "string", "description": "kind/namespace/name affected."},
                    "root_cause": {"type": "string"},
                    "evidence": {"type": "string", "description": "Key facts from tool output."},
                    "fix_steps": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Exact changes, naming the write tool and arguments.",
                    },
                    "risk": {"type": "string", "description": "Impact and how to undo it."},
                },
                "required": ["id", "title", "severity", "resource", "root_cause", "fix_steps", "risk"],
                "additionalProperties": False,
            },
        },
    },
    required=["summary", "issues"],
)
def propose_remediation(summary: str, issues: list[dict]) -> str:
    return "Plan recorded; waiting for the operator."


@tool(
    category="control",
    description="""Report the verified result after applying approved fixes. Call this only after
re-checking the affected resources with read tools. If resolved is false, explain what
remains; you may then investigate further and propose a new plan.""",
    properties={
        "resolved": {"type": "boolean"},
        "summary": {"type": "string", "description": "What was changed and what you observed."},
        "remaining_issues": {"type": "string"},
    },
    required=["resolved", "summary"],
)
def report_outcome(resolved: bool, summary: str, remaining_issues: str = "") -> str:
    return "Outcome recorded."
