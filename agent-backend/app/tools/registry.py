"""A tiny tool registry: each tool is a Python function plus the JSON schema
Claude sees. Registration order is fixed, so the tool list sent to the API is
byte-identical on every request (which keeps prompt caching effective)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal

Category = Literal["read", "write", "control"]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    fn: Callable[..., str]
    category: Category

    def api_definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


REGISTRY: dict[str, Tool] = {}


def tool(
    *,
    category: Category,
    description: str,
    properties: dict[str, Any] | None = None,
    required: list[str] | None = None,
):
    """Decorator that registers a function as a tool."""

    def decorator(fn: Callable[..., str]) -> Callable[..., str]:
        name = fn.__name__
        if name in REGISTRY:
            raise ValueError(f"duplicate tool name: {name}")
        REGISTRY[name] = Tool(
            name=name,
            description=description.strip(),
            input_schema={
                "type": "object",
                "properties": properties or {},
                "required": required or [],
                "additionalProperties": False,
            },
            fn=fn,
            category=category,
        )
        return fn

    return decorator


# Schema fragments reused by many tools.
NAMESPACE = {"type": "string", "description": "Kubernetes namespace."}
OPTIONAL_NAMESPACE = {
    "type": "string",
    "description": "Namespace to search. Omit or leave empty for all namespaces.",
}
DRY_RUN = {
    "type": "boolean",
    "description": "If true, the API server validates the change without persisting it.",
}
WORKLOAD_KIND = {"type": "string", "enum": ["Deployment", "StatefulSet", "DaemonSet"]}
