"""Runtime configuration, read once from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name)
    if not raw:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def _env_list(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in _env(name, default).split(",") if item.strip()]


@dataclass
class Settings:
    # --- Claude ---------------------------------------------------------------
    # ANTHROPIC_API_KEY is read by the SDK itself; it is never stored here.
    model: str = field(default_factory=lambda: _env("CLAUDE_MODEL", "claude-opus-5-5"))
    effort: str = field(default_factory=lambda: _env("CLAUDE_EFFORT", "high"))
    max_tokens: int = field(default_factory=lambda: int(_env("CLAUDE_MAX_TOKENS", "16000")))
    # "default" -> server-side refusal fallback routed by Anthropic; "off" -> disabled.
    fallbacks: str = field(default_factory=lambda: _env("CLAUDE_FALLBACKS", "default"))
    max_iterations: int = field(default_factory=lambda: int(_env("AGENT_MAX_ITERATIONS", "40")))
    max_tool_result_chars: int = field(
        default_factory=lambda: int(_env("AGENT_MAX_TOOL_RESULT_CHARS", "16000"))
    )

    # Prices in USD per million tokens, used only for the cost estimate in the UI.
    price_input: float = field(default_factory=lambda: float(_env("PRICE_INPUT_PER_MTOK", "4.00")))
    price_output: float = field(default_factory=lambda: float(_env("PRICE_OUTPUT_PER_MTOK", "20.00")))
    price_cache_write: float = field(
        default_factory=lambda: float(_env("PRICE_CACHE_WRITE_PER_MTOK", "5.00"))
    )
    price_cache_read: float = field(
        default_factory=lambda: float(_env("PRICE_CACHE_READ_PER_MTOK", "0.20"))
    )

    # --- Cluster / safety -------------------------------------------------------
    cluster_name: str = field(default_factory=lambda: _env("CLUSTER_NAME", "huawei-cce"))
    agent_namespace: str = field(default_factory=lambda: _env("AGENT_NAMESPACE", "ai-agent"))
    enable_write_tools: bool = field(default_factory=lambda: _env_bool("ENABLE_WRITE_TOOLS", True))
    protected_namespaces: list[str] = field(
        default_factory=lambda: _env_list(
            "PROTECTED_NAMESPACES", "kube-system,kube-public,kube-node-lease,monitoring"
        )
    )
    max_replicas: int = field(default_factory=lambda: int(_env("MAX_REPLICAS", "20")))
    max_writes_per_approval: int = field(
        default_factory=lambda: int(_env("MAX_WRITES_PER_APPROVAL", "10"))
    )
    # Empty list -> any registry allowed for set_container_image.
    allowed_image_registries: list[str] = field(
        default_factory=lambda: _env_list("ALLOWED_IMAGE_REGISTRIES")
    )

    # --- Storage ----------------------------------------------------------------
    database_url: str = field(default_factory=lambda: _env("DATABASE_URL"))

    @property
    def all_protected_namespaces(self) -> set[str]:
        return set(self.protected_namespaces) | {self.agent_namespace}


settings = Settings()
