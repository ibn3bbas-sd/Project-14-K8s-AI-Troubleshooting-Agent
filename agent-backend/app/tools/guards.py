"""Guardrails applied in code before any write reaches the API server.

These run in addition to (not instead of) Kubernetes RBAC and the
approval gate in the agent runner. A refused write raises ToolError, whose
message goes back to the model so it can explain or choose another fix.
"""

from __future__ import annotations

from typing import Any, Iterable

from app.config import settings
from app.tools.kube import ToolError

# Keys that would escalate privileges or change identity if patched in.
FORBIDDEN_PATCH_KEYS = {
    "privileged",
    "allowPrivilegeEscalation",
    "hostNetwork",
    "hostPID",
    "hostIPC",
    "hostPath",
    "serviceAccountName",
    "serviceAccount",
    "automountServiceAccountToken",
    "ownerReferences",
    "finalizers",
    "capabilities",
}

# Node label prefixes owned by Kubernetes or CCE; the agent must not edit them.
PROTECTED_LABEL_MARKERS = ("kubernetes.io/", "k8s.io/", "cce.cloud.com/", "cce.io/", "huawei")
PROTECTED_LABEL_KEYS = {"os.architecture", "os.name", "os.version"}

PATCHABLE_KINDS = {
    "Deployment",
    "StatefulSet",
    "DaemonSet",
    "Service",
    "ConfigMap",
    "PersistentVolumeClaim",
    "HorizontalPodAutoscaler",
    "CronJob",
    "Job",
    "Ingress",
}


def check_namespace(namespace: str) -> None:
    if not namespace:
        raise ToolError("A namespace is required for this change.")
    if namespace in settings.all_protected_namespaces:
        raise ToolError(
            f"Namespace '{namespace}' is protected (system or agent namespace). "
            "The agent does not modify it; recommend a manual change to the operator instead."
        )


def _walk_keys(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        for key, inner in value.items():
            yield key
            yield from _walk_keys(inner)
    elif isinstance(value, list):
        for inner in value:
            yield from _walk_keys(inner)


def check_patch_body(body: Any) -> None:
    bad = sorted(set(_walk_keys(body)) & FORBIDDEN_PATCH_KEYS)
    if bad:
        raise ToolError(
            f"Patch touches security-sensitive fields {bad}; this is blocked for the agent."
        )


def check_patchable_kind(kind: str) -> None:
    if kind not in PATCHABLE_KINDS:
        raise ToolError(
            f"Patching {kind} is not allowed. Patchable kinds: {', '.join(sorted(PATCHABLE_KINDS))}."
        )


def check_replicas(replicas: int) -> None:
    if replicas < 0:
        raise ToolError("replicas must be >= 0.")
    if replicas > settings.max_replicas:
        raise ToolError(
            f"replicas={replicas} exceeds the configured MAX_REPLICAS={settings.max_replicas}."
        )


def check_image(image: str) -> None:
    if not image or " " in image:
        raise ToolError("Invalid image reference.")
    allowed = settings.allowed_image_registries
    if allowed and not any(image.startswith(prefix) for prefix in allowed):
        raise ToolError(
            f"Image '{image}' is not from an allowed registry ({', '.join(allowed)})."
        )


def check_node_labels(labels: dict[str, Any]) -> None:
    for key in labels:
        if key in PROTECTED_LABEL_KEYS or any(m in key for m in PROTECTED_LABEL_MARKERS):
            raise ToolError(f"Label '{key}' is managed by Kubernetes/CCE and cannot be changed.")


def check_cordon(node_name: str, nodes: list[dict]) -> None:
    """Refuse to cordon the last schedulable Ready node."""

    def schedulable(n: dict) -> bool:
        ready = any(
            c.get("type") == "Ready" and c.get("status") == "True"
            for c in (n.get("status") or {}).get("conditions") or []
        )
        return ready and not (n.get("spec") or {}).get("unschedulable")

    remaining = [n for n in nodes if schedulable(n) and n["metadata"]["name"] != node_name]
    if not remaining:
        raise ToolError(
            f"Cordoning {node_name} would leave no schedulable Ready nodes; blocked."
        )


def check_resources(requests: dict | None, limits: dict | None) -> None:
    for section in (requests or {}, limits or {}):
        for key in section:
            if key not in ("cpu", "memory", "ephemeral-storage"):
                raise ToolError(f"Unsupported resource '{key}'; use cpu, memory or ephemeral-storage.")
