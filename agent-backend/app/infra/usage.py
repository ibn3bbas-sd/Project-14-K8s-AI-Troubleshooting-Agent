"""Token accounting and a cost estimate shown in the UI."""

from __future__ import annotations

from typing import Any

from app.config import settings
from app.infra import metrics


def empty_usage() -> dict[str, float]:
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "requests": 0,
        "cost_usd": 0.0,
    }


def add_usage(total: dict[str, float], usage: Any) -> dict[str, float]:
    """Accumulate one response's usage into a session total."""
    fields = {
        "input_tokens": getattr(usage, "input_tokens", 0) or 0,
        "output_tokens": getattr(usage, "output_tokens", 0) or 0,
        "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
        "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", 0) or 0,
    }
    cost = (
        fields["input_tokens"] * settings.price_input
        + fields["output_tokens"] * settings.price_output
        + fields["cache_read_input_tokens"] * settings.price_cache_read
        + fields["cache_creation_input_tokens"] * settings.price_cache_write
    ) / 1_000_000
    for key, value in fields.items():
        total[key] = total.get(key, 0) + value
        metrics.TOKENS.labels(type=key).inc(value)
    total["requests"] = total.get("requests", 0) + 1
    total["cost_usd"] = round(total.get("cost_usd", 0.0) + cost, 6)
    metrics.COST.inc(cost)
    return total
