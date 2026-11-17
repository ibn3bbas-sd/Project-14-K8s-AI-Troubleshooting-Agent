"""Tool package. Importing it registers every tool in a fixed order."""

from app.tools import control, read, write  # noqa: F401  (registration side effects)
from app.tools.registry import REGISTRY, Tool

__all__ = ["REGISTRY", "Tool", "api_tool_definitions"]


def api_tool_definitions(include_write: bool = True) -> list[dict]:
    """Tool definitions for the Messages API, in a stable order."""
    return [
        t.api_definition()
        for t in REGISTRY.values()
        if include_write or t.category != "write"
    ]
