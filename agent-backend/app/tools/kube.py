"""Kubernetes API access shared by the read and write tools.

Inside the cluster the pod's ServiceAccount token and CA are used (RBAC is the
outer safety boundary). Outside the cluster the local kubeconfig is used, which
is handy for development against a CCE cluster from a laptop.
"""

from __future__ import annotations

import copy
import json
import threading
from datetime import datetime, timezone
from typing import Any

import yaml
from kubernetes import client, config
from kubernetes.client.exceptions import ApiException
from kubernetes.dynamic import DynamicClient
from kubernetes.dynamic.exceptions import DynamicApiError

# Kinds the agent may read. Secrets are deliberately absent: the agent never
# sees secret data, and RBAC does not grant it either.
KINDS: dict[str, tuple[str, bool]] = {
    # kind: (apiVersion, namespaced)
    "Pod": ("v1", True),
    "Service": ("v1", True),
    "Endpoints": ("v1", True),
    "ConfigMap": ("v1", True),
    "PersistentVolumeClaim": ("v1", True),
    "PersistentVolume": ("v1", False),
    "Node": ("v1", False),
    "Namespace": ("v1", False),
    "Event": ("v1", True),
    "ServiceAccount": ("v1", True),
    "ResourceQuota": ("v1", True),
    "LimitRange": ("v1", True),
    "Deployment": ("apps/v1", True),
    "ReplicaSet": ("apps/v1", True),
    "StatefulSet": ("apps/v1", True),
    "DaemonSet": ("apps/v1", True),
    "Job": ("batch/v1", True),
    "CronJob": ("batch/v1", True),
    "Ingress": ("networking.k8s.io/v1", True),
    "NetworkPolicy": ("networking.k8s.io/v1", True),
    "HorizontalPodAutoscaler": ("autoscaling/v2", True),
    "PodDisruptionBudget": ("policy/v1", True),
    "StorageClass": ("storage.k8s.io/v1", False),
}

_ALIASES = {k.lower(): k for k in KINDS}
_ALIASES.update(
    {
        "pods": "Pod", "po": "Pod", "svc": "Service", "services": "Service",
        "ep": "Endpoints", "cm": "ConfigMap", "configmaps": "ConfigMap",
        "pvc": "PersistentVolumeClaim", "pv": "PersistentVolume", "nodes": "Node",
        "no": "Node", "ns": "Namespace", "namespaces": "Namespace", "events": "Event",
        "sa": "ServiceAccount", "deploy": "Deployment", "deployments": "Deployment",
        "rs": "ReplicaSet", "sts": "StatefulSet", "statefulsets": "StatefulSet",
        "ds": "DaemonSet", "daemonsets": "DaemonSet", "jobs": "Job", "cronjobs": "CronJob",
        "ing": "Ingress", "ingresses": "Ingress", "netpol": "NetworkPolicy",
        "hpa": "HorizontalPodAutoscaler", "pdb": "PodDisruptionBudget", "sc": "StorageClass",
    }
)


class ToolError(Exception):
    """An error whose message is meant to be read by the model."""


def normalize_kind(kind: str) -> str:
    resolved = _ALIASES.get(kind.strip().lower())
    if not resolved:
        raise ToolError(
            f"Unsupported kind '{kind}'. Supported kinds: {', '.join(sorted(KINDS))}."
        )
    return resolved


class Kube:
    """Lazily initialised Kubernetes clients (thread-safe)."""

    _lock = threading.Lock()
    _api_client: client.ApiClient | None = None
    _dynamic: DynamicClient | None = None

    @classmethod
    def api_client(cls) -> client.ApiClient:
        with cls._lock:
            if cls._api_client is None:
                try:
                    config.load_incluster_config()
                except config.ConfigException:
                    config.load_kube_config()
                cls._api_client = client.ApiClient()
            return cls._api_client

    @classmethod
    def core(cls) -> client.CoreV1Api:
        return client.CoreV1Api(cls.api_client())

    @classmethod
    def apps(cls) -> client.AppsV1Api:
        return client.AppsV1Api(cls.api_client())

    @classmethod
    def custom(cls) -> client.CustomObjectsApi:
        return client.CustomObjectsApi(cls.api_client())

    @classmethod
    def dynamic(cls) -> DynamicClient:
        api = cls.api_client()
        with cls._lock:
            if cls._dynamic is None:
                cls._dynamic = DynamicClient(api)
            return cls._dynamic

    @classmethod
    def resource(cls, kind: str):
        kind = normalize_kind(kind)
        api_version, _ = KINDS[kind]
        return cls.dynamic().resources.get(api_version=api_version, kind=kind)


def is_namespaced(kind: str) -> bool:
    return KINDS[normalize_kind(kind)][1]


def describe_api_error(exc: Exception) -> str:
    """Turn raw Kubernetes API errors into short text the model can act on."""
    status = getattr(exc, "status", None)
    body = getattr(exc, "body", None)
    message = ""
    if body:
        try:
            parsed = json.loads(body) if isinstance(body, (str, bytes)) else body
            message = parsed.get("message", "") if isinstance(parsed, dict) else str(parsed)
        except (ValueError, TypeError):
            message = str(body)[:500]
    message = message or getattr(exc, "reason", "") or str(exc)
    hints = {
        401: "The agent's credentials were rejected.",
        403: "Forbidden by RBAC: the agent's ServiceAccount lacks this permission. "
        "Do not retry; report it to the operator.",
        404: "Not found. Check the name, namespace and kind (list resources first).",
        409: "Conflict: the object changed or already exists. Re-read it and retry once.",
        422: "Invalid request: the API server rejected the change as invalid.",
    }
    hint = hints.get(status, "")
    return f"Kubernetes API error {status or ''}: {message} {hint}".strip()


def call(fn, *args, **kwargs):
    """Run a Kubernetes call and convert API failures into ToolError."""
    try:
        return fn(*args, **kwargs)
    except (ApiException, DynamicApiError) as exc:
        raise ToolError(describe_api_error(exc)) from exc


def to_dict(obj: Any) -> dict:
    """Convert typed client objects / ResourceInstance to plain dicts."""
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "to_dict") and not isinstance(obj, client.ApiClient):
        try:
            # ResourceInstance.to_dict() already returns camelCase JSON shape.
            return obj.to_dict()
        except TypeError:
            pass
    return Kube.api_client().sanitize_for_serialization(obj)


def sanitize(obj: dict) -> dict:
    """Drop noisy server-managed fields before showing an object to the model."""
    obj = copy.deepcopy(obj)
    meta = obj.get("metadata") or {}
    meta.pop("managedFields", None)
    annotations = meta.get("annotations") or {}
    annotations.pop("kubectl.kubernetes.io/last-applied-configuration", None)
    if not annotations:
        meta.pop("annotations", None)
    return obj


def to_yaml(obj: Any) -> str:
    return yaml.safe_dump(obj, sort_keys=False, default_flow_style=False)


def age(timestamp: Any) -> str:
    if not timestamp:
        return "?"
    if isinstance(timestamp, str):
        try:
            timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError:
            return "?"
    delta = datetime.now(timezone.utc) - timestamp
    seconds = int(delta.total_seconds())
    if seconds < 120:
        return f"{seconds}s"
    if seconds < 7200:
        return f"{seconds // 60}m"
    if seconds < 172800:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def merge_patch(target: Any, patch: Any) -> Any:
    """RFC 7386 JSON merge patch (null deletes a key)."""
    if not isinstance(patch, dict):
        return copy.deepcopy(patch)
    result = copy.deepcopy(target) if isinstance(target, dict) else {}
    for key, value in patch.items():
        if value is None:
            result.pop(key, None)
        else:
            result[key] = merge_patch(result.get(key), value)
    return result


def parse_quantity(value: str | None) -> float:
    """Parse a Kubernetes quantity to a float (CPU in cores, memory in bytes)."""
    if value is None or value == "":
        return 0.0
    value = str(value)
    suffixes = {
        "Ki": 1024, "Mi": 1024**2, "Gi": 1024**3, "Ti": 1024**4, "Pi": 1024**5,
        "n": 1e-9, "u": 1e-6, "m": 1e-3, "k": 1e3, "K": 1e3, "M": 1e6, "G": 1e9, "T": 1e12,
    }
    for suffix in sorted(suffixes, key=len, reverse=True):
        if value.endswith(suffix):
            try:
                return float(value[: -len(suffix)]) * suffixes[suffix]
            except ValueError:
                return 0.0
    try:
        return float(value)
    except ValueError:
        return 0.0
