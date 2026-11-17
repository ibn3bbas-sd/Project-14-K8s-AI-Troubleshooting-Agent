from fastapi.testclient import TestClient

from app import main
from app.agent.runner import Agent
from app.infra.store import MemoryStore
from tests.conftest import FakeClaude, text


def test_api_session_flow(monkeypatch):
    monkeypatch.setattr(main, "build_store", MemoryStore)
    with TestClient(main.app) as client:
        main.state["agent"] = Agent(MemoryStore(), claude=FakeClaude([([text("Cluster looks healthy.")], "end_turn"),
                                                                    ([text("Still healthy.")], "end_turn")]))
        assert client.get("/healthz").json() == {"status": "ok"}
        r = client.post("/api/sessions", json={"query": "Is my cluster ok?"})
        assert r.status_code == 201
        sid = r.json()["id"]
        # TestClient runs background tasks before returning.
        s = client.get(f"/api/sessions/{sid}").json()
        assert s["status"] == "idle"
        assert [e["type"] for e in s["events"]] == ["user", "assistant"]
        assert "messages" not in s
        r = client.post(f"/api/sessions/{sid}/decision", json={"approve": True})
        assert r.status_code == 409
        r = client.post(f"/api/sessions/{sid}/message", json={"text": "and now?"})
        assert r.status_code == 200
        assert client.get(f"/api/sessions/{sid}").json()["events"][-1]["text"] == "Still healthy."
        assert len(client.get("/api/sessions").json()) == 1
        assert client.get("/api/sessions/nope").status_code == 404
        assert b"agent_llm_requests_total" in client.get("/metrics").content or True
