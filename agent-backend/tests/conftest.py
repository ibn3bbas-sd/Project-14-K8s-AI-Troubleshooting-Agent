import dataclasses
from types import SimpleNamespace

import pytest

from app.tools import REGISTRY


class FakeClaude:
    """Returns scripted responses and records every request it receives."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def create(self, *, system, tools, messages):
        self.requests.append({"system": system, "tools": tools, "messages": [dict(m) for m in messages]})
        content, stop = self.responses.pop(0)
        return SimpleNamespace(
            content=content,
            stop_reason=stop,
            stop_details=None,
            model="claude-opus-5-5",
            usage=SimpleNamespace(
                input_tokens=100, output_tokens=50, cache_read_input_tokens=0, cache_creation_input_tokens=0
            ),
        )


def tool_use(id_, tool_name, /, **inputs):
    return {"type": "tool_use", "id": id_, "name": tool_name, "input": inputs}


def text(t):
    return {"type": "text", "text": t}


@pytest.fixture
def fake_tools(monkeypatch):
    """Replace the cluster-touching tools used in tests with recorders."""
    calls = []

    def make(name):
        def fn(**kwargs):
            calls.append((name, kwargs))
            return f"{name} ok"

        return fn

    for name in ("find_unhealthy_pods", "describe_pod", "label_node", "recreate_pod"):
        monkeypatch.setitem(REGISTRY, name, dataclasses.replace(REGISTRY[name], fn=make(name)))
    return calls
