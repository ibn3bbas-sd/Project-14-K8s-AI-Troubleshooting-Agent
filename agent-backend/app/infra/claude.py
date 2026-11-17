"""Claude API access (replaces the Bedrock client of the original design).

Uses the official Anthropic Python SDK. The API key comes from the
ANTHROPIC_API_KEY environment variable (a Kubernetes Secret in-cluster).
"""

from __future__ import annotations

import logging
import time
from typing import Any

import anthropic

from app.config import settings
from app.infra import metrics

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class ClaudeClient:
    def __init__(self, client: anthropic.Anthropic | None = None) -> None:
        # max_retries covers 429/5xx/connection errors with backoff.
        self._client = client or anthropic.Anthropic(max_retries=4, timeout=600.0)

    def create(self, *, system: str, tools: list[dict], messages: list[dict]) -> Any:
        kwargs: dict[str, Any] = {
            "model": settings.model,
            "max_tokens": settings.max_tokens,
            "system": system,
            "tools": tools,
            "messages": messages,
            # Adaptive thinking is always on for Claude Opus 5.5; effort sets its depth.
            # "summarized" returns readable reasoning summaries for the UI.
            "thinking": {"type": "adaptive", "display": "summarized"},
            "output_config": {"effort": settings.effort},
            # Auto-cache the growing conversation prefix (tools + system + history).
            "cache_control": {"type": "ephemeral"},
        }
        betas: list[str] = []
        if settings.fallbacks and settings.fallbacks.lower() != "off":
            # On a safety-classifier decline, the API reruns the request on a
            # fallback model inside the same call instead of returning a refusal.
            betas.append(FALLBACK_BETA)
            kwargs["fallbacks"] = "default"
        started = time.monotonic()
        try:
            if betas:
                response = self._client.beta.messages.create(betas=betas, **kwargs)
            else:
                response = self._client.beta.messages.create(**kwargs)
        except anthropic.APIError:
            metrics.LLM_REQUESTS.labels(status="error").inc()
            raise
        finally:
            metrics.LLM_LATENCY.observe(time.monotonic() - started)
        metrics.LLM_REQUESTS.labels(status=response.stop_reason or "unknown").inc()
        log.info(
            "claude response",
            extra={
                "request_id": getattr(response, "_request_id", None),
                "stop_reason": response.stop_reason,
                "model": response.model,
            },
        )
        return response


def block_to_param(block: Any) -> dict:
    """Serialize a response content block so it can be stored (JSON) and sent
    back unchanged on the next request (thinking signatures included)."""
    if isinstance(block, dict):
        return block
    return block.to_dict(mode="json", exclude_none=True)
