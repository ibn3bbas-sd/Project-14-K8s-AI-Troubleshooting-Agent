from app.agent.runner import Agent, SessionBusy
from app.infra.store import MemoryStore
from tests.conftest import FakeClaude, text, tool_use

import pytest

PLAN = {
    "summary": "Pod web-service is Pending: nodeSelector type=web matches no node.",
    "issues": [
        {
            "id": "issue-1",
            "title": "Unsatisfiable nodeSelector",
            "severity": "medium",
            "resource": "Pod/default/web-service",
            "root_cause": "No node has label type=web",
            "fix_steps": ["recreate_pod name=web-service namespace=default spec_patch={nodeSelector: null}"],
            "risk": "Pod restarts once.",
        }
    ],
}


def start(agent, query="Why is web-service pending?"):
    session = agent.create(query)
    agent.run(session["id"], [{"type": "text", "text": query}])
    return agent.store.get(session["id"])


def test_full_approval_cycle(fake_tools):
    claude = FakeClaude(
        [
            ([tool_use("t1", "find_unhealthy_pods"), tool_use("t2", "describe_pod", name="web-service", namespace="default")], "tool_use"),
            # A write before approval must be refused, not executed.
            ([tool_use("t3", "recreate_pod", name="web-service", namespace="default", spec_patch={"nodeSelector": None})], "tool_use"),
            ([text("Here is my plan."), tool_use("t4", "propose_remediation", **PLAN)], "tool_use"),
            # after approval
            ([tool_use("t5", "recreate_pod", name="web-service", namespace="default", spec_patch={"nodeSelector": None})], "tool_use"),
            ([tool_use("t6", "describe_pod", name="web-service", namespace="default")], "tool_use"),
            ([tool_use("t7", "report_outcome", resolved=True, summary="Pod is Running.")], "tool_use"),
            ([text("Fixed: the pod is Running.")], "end_turn"),
        ]
    )
    agent = Agent(MemoryStore(), claude=claude)
    session = start(agent)

    assert session["status"] == "awaiting_approval"
    assert session["pending"]["issues"][0]["id"] == "issue-1"
    # recreate_pod was blocked during diagnosis
    assert [c[0] for c in fake_tools] == ["find_unhealthy_pods", "describe_pod"]
    blocked = session["messages"][4]["content"][0]
    assert blocked["is_error"] and "locked" in blocked["content"]

    content = agent.begin_decision(session["id"], approve=True, issue_ids=["issue-1"])
    agent.run(session["id"], content)
    session = agent.store.get(session["id"])

    assert [c[0] for c in fake_tools][2:] == ["recreate_pod", "describe_pod"]
    assert session["outcome"]["resolved"] is True
    assert session["phase"] == "diagnosis"  # write window closed again
    assert session["status"] == "idle"
    # The plan's tool_use got the operator decision as its tool_result.
    decision_msg = claude.requests[3]["messages"][-1]
    assert decision_msg["role"] == "user"
    assert decision_msg["content"][-1]["tool_use_id"] == "t4"
    assert "APPROVED" in decision_msg["content"][-1]["content"]
    # Messages alternate user/assistant correctly.
    roles = [m["role"] for m in session["messages"]]
    assert all(a != b for a, b in zip(roles, roles[1:]))
    assert session["usage"]["requests"] == 7
    # Same tools on every request (prompt cache stays valid).
    assert all(r["tools"] == claude.requests[0]["tools"] for r in claude.requests)


def test_rejection_keeps_writes_locked(fake_tools):
    claude = FakeClaude(
        [
            ([tool_use("t1", "propose_remediation", **PLAN)], "tool_use"),
            ([tool_use("t2", "recreate_pod", name="web-service", namespace="default", spec_patch={})], "tool_use"),
            ([text("Understood, no changes made.")], "end_turn"),
        ]
    )
    agent = Agent(MemoryStore(), claude=claude)
    session = start(agent)
    content = agent.begin_decision(session["id"], approve=False, guidance="Label a node instead")
    assert "NOT approve" in content[-1]["content"] and "Label a node" in content[-1]["content"]
    agent.run(session["id"], content)
    session = agent.store.get(session["id"])
    assert fake_tools == []
    assert session["status"] == "idle"


def test_message_while_pending_counts_as_guidance(fake_tools):
    claude = FakeClaude([([tool_use("t1", "propose_remediation", **PLAN)], "tool_use")])
    agent = Agent(MemoryStore(), claude=claude)
    session = start(agent)
    content = agent.begin_message(session["id"], "Use a different fix")
    assert content[-1]["tool_use_id"] == "t1"
    assert "Use a different fix" in content[-1]["content"]


def test_decision_without_plan_is_rejected(fake_tools):
    claude = FakeClaude([([text("All healthy.")], "end_turn")])
    agent = Agent(MemoryStore(), claude=claude)
    session = start(agent)
    with pytest.raises(SessionBusy):
        agent.begin_decision(session["id"], approve=True)


def test_api_error_marks_session_failed_and_retry_resends(fake_tools):
    class Boom(FakeClaude):
        def create(self, **kw):
            if not self.requests:
                self.requests.append(kw)
                raise RuntimeError("network down")
            return super().create(**kw)

    claude = Boom([([text("Recovered.")], "end_turn")])
    agent = Agent(MemoryStore(), claude=claude)
    session = start(agent)
    assert session["status"] == "error"
    content = agent.begin_retry(session["id"])
    assert content is None  # last message is the user's; resend as-is
    agent.run(session["id"], content)
    session = agent.store.get(session["id"])
    assert session["status"] == "idle"
    assert session["messages"][-1]["content"][0]["text"] == "Recovered."


def test_write_budget(fake_tools, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "max_writes_per_approval", 1, raising=False)
    claude = FakeClaude(
        [
            ([tool_use("t1", "propose_remediation", **PLAN)], "tool_use"),
            ([tool_use("t2", "label_node", name="n1", labels={"type": "web"}),
              tool_use("t3", "label_node", name="n2", labels={"type": "web"})], "tool_use"),
            ([text("done")], "end_turn"),
        ]
    )
    agent = Agent(MemoryStore(), claude=claude)
    session = start(agent)
    agent.run(session["id"], agent.begin_decision(session["id"], approve=True))
    session = agent.store.get(session["id"])
    assert [c[0] for c in fake_tools] == ["label_node"]
    assert "budget" in session["messages"][-2]["content"][1]["content"]
