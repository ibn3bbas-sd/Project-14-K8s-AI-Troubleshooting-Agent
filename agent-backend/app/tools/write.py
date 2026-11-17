"""Write (remediation) tools.

The runner only executes these after the operator approves a plan; every call
also passes through guards.py, and RBAC is the final boundary. Each tool
accepts dry_run so the model can validate a change server-side first.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from app.tools import guards
from app.tools.kube import Kube, ToolError, call, merge_patch, normalize_kind, sanitize, to_dict, to_yaml
from app.tools.read import get_object, list_objects
from app.tools.registry import DRY_RUN, NAMESPACE, WORKLOAD_KIND, tool

STRATEGIC = "application/strategic-merge-patch+json"
MERGE = "application/merge-patch+json"
JSON_PATCH = "application/json-patch+json"


def _dry(dry_run: bool) -> dict[str, Any]:
    return {"dry_run": "All"} if dry_run else {}


def _label(dry_run: bool) -> str:
    return "[DRY RUN - nothing persisted] " if dry_run else ""


def _patch(kind: str, name: str, namespace: str | None, body: Any, content_type: str, dry_run: bool) -> dict:
    resource = Kube.resource(kind)
    return to_dict(
        call(
            Kube.dynamic().patch,
            resource,
            body=body,
            name=name,
            namespace=namespace,
            content_type=content_type,
            field_manager="k8s-ai-agent",
            **_dry(dry_run),
        )
    )


def _container_names(obj: dict) -> list[str]:
    spec = obj.get("spec") or {}
    template = (spec.get("template") or {}).get("spec") or spec
    return [c["name"] for c in template.get("containers") or []]


def _require_container(obj: dict, container: str) -> None:
    names = _container_names(obj)
    if container not in names:
        # A strategic-merge patch with an unknown name would ADD a container.
        raise ToolError(f"Container '{container}' not found. Containers: {', '.join(names)}")


@tool(
    category="write",
    description="""Delete a pod. If it is owned by a controller (Deployment, StatefulSet, ...)
a fresh replacement is created automatically. Do NOT use on bare pods unless the
plan says the pod should be removed; use recreate_pod to change a bare pod.""",
    properties={"name": {"type": "string"}, "namespace": NAMESPACE, "dry_run": DRY_RUN},
    required=["name", "namespace"],
)
def delete_pod(name: str, namespace: str, dry_run: bool = False) -> str:
    guards.check_namespace(namespace)
    pod = get_object("Pod", name, namespace)
    owners = [f"{o['kind']}/{o['name']}" for o in pod["metadata"].get("ownerReferences") or []]
    call(Kube.dynamic().delete, Kube.resource("Pod"), name=name, namespace=namespace, **_dry(dry_run))
    note = f"owned by {', '.join(owners)} so it will be recreated" if owners else "bare pod: it will NOT come back"
    return f"{_label(dry_run)}Deleted pod {namespace}/{name} ({note})."


@tool(
    category="write",
    description="""Rolling restart of a Deployment/StatefulSet/DaemonSet (same as
`kubectl rollout restart`). Use after fixing a ConfigMap/Secret the pods read at start.""",
    properties={"kind": WORKLOAD_KIND, "name": {"type": "string"}, "namespace": NAMESPACE, "dry_run": DRY_RUN},
    required=["kind", "name", "namespace"],
)
def restart_workload(kind: str, name: str, namespace: str, dry_run: bool = False) -> str:
    guards.check_namespace(namespace)
    kind = normalize_kind(kind)
    stamp = datetime.now(timezone.utc).isoformat()
    body = {"spec": {"template": {"metadata": {"annotations": {"kubectl.kubernetes.io/restartedAt": stamp}}}}}
    _patch(kind, name, namespace, body, STRATEGIC, dry_run)
    return f"{_label(dry_run)}Triggered rolling restart of {kind} {namespace}/{name}."


@tool(
    category="write",
    description="Set the replica count of a Deployment or StatefulSet.",
    properties={
        "kind": {"type": "string", "enum": ["Deployment", "StatefulSet"]},
        "name": {"type": "string"},
        "namespace": NAMESPACE,
        "replicas": {"type": "integer"},
        "dry_run": DRY_RUN,
    },
    required=["kind", "name", "namespace", "replicas"],
)
def scale_workload(kind: str, name: str, namespace: str, replicas: int, dry_run: bool = False) -> str:
    guards.check_namespace(namespace)
    guards.check_replicas(int(replicas))
    kind = normalize_kind(kind)
    before = get_object(kind, name, namespace).get("spec", {}).get("replicas")
    _patch(kind, name, namespace, {"spec": {"replicas": int(replicas)}}, MERGE, dry_run)
    return f"{_label(dry_run)}Scaled {kind} {namespace}/{name} from {before} to {replicas} replicas."


@tool(
    category="write",
    description="""Change one container's image in a workload (e.g. fix a typo'd tag that causes
ImagePullBackOff). Triggers a rollout.""",
    properties={
        "kind": WORKLOAD_KIND,
        "name": {"type": "string"},
        "namespace": NAMESPACE,
        "container": {"type": "string"},
        "image": {"type": "string"},
        "dry_run": DRY_RUN,
    },
    required=["kind", "name", "namespace", "container", "image"],
)
def set_container_image(
    kind: str, name: str, namespace: str, container: str, image: str, dry_run: bool = False
) -> str:
    guards.check_namespace(namespace)
    guards.check_image(image)
    kind = normalize_kind(kind)
    obj = get_object(kind, name, namespace)
    _require_container(obj, container)
    old = next(c.get("image") for c in obj["spec"]["template"]["spec"]["containers"] if c["name"] == container)
    body = {"spec": {"template": {"spec": {"containers": [{"name": container, "image": image}]}}}}
    _patch(kind, name, namespace, body, STRATEGIC, dry_run)
    return f"{_label(dry_run)}{kind} {namespace}/{name} container {container}: image {old} -> {image}."


@tool(
    category="write",
    description="""Set CPU/memory requests and/or limits of one container in a workload
(e.g. raise a memory limit after OOMKilled, or lower requests that cannot be scheduled).
Values use Kubernetes quantities such as "250m", "512Mi". Triggers a rollout.""",
    properties={
        "kind": WORKLOAD_KIND,
        "name": {"type": "string"},
        "namespace": NAMESPACE,
        "container": {"type": "string"},
        "requests": {"type": "object", "description": 'e.g. {"cpu": "100m", "memory": "256Mi"}'},
        "limits": {"type": "object", "description": 'e.g. {"memory": "512Mi"}'},
        "dry_run": DRY_RUN,
    },
    required=["kind", "name", "namespace", "container"],
)
def set_container_resources(
    kind: str,
    name: str,
    namespace: str,
    container: str,
    requests: dict | None = None,
    limits: dict | None = None,
    dry_run: bool = False,
) -> str:
    guards.check_namespace(namespace)
    guards.check_resources(requests, limits)
    if not requests and not limits:
        raise ToolError("Pass requests and/or limits.")
    kind = normalize_kind(kind)
    obj = get_object(kind, name, namespace)
    _require_container(obj, container)
    resources: dict[str, Any] = {}
    if requests:
        resources["requests"] = {k: str(v) for k, v in requests.items()}
    if limits:
        resources["limits"] = {k: str(v) for k, v in limits.items()}
    body = {"spec": {"template": {"spec": {"containers": [{"name": container, "resources": resources}]}}}}
    _patch(kind, name, namespace, body, STRATEGIC, dry_run)
    return f"{_label(dry_run)}{kind} {namespace}/{name} container {container}: resources set to {resources}."


@tool(
    category="write",
    description=f"""Apply a patch to one object. patch_type "strategic" (default; lists of
containers merge by name) or "merge" (RFC 7386, null deletes a key). Allowed kinds:
{", ".join(sorted(guards.PATCHABLE_KINDS))}. Security-sensitive fields (privileged,
hostNetwork, hostPath, serviceAccountName, capabilities, ...) are rejected.""",
    properties={
        "kind": {"type": "string"},
        "name": {"type": "string"},
        "namespace": NAMESPACE,
        "patch": {"type": "object", "description": "The patch body."},
        "patch_type": {"type": "string", "enum": ["strategic", "merge"]},
        "dry_run": DRY_RUN,
    },
    required=["kind", "name", "namespace", "patch"],
)
def patch_resource(
    kind: str, name: str, namespace: str, patch: dict, patch_type: str = "strategic", dry_run: bool = False
) -> str:
    guards.check_namespace(namespace)
    kind = normalize_kind(kind)
    guards.check_patchable_kind(kind)
    guards.check_patch_body(patch)
    content_type = MERGE if patch_type == "merge" else STRATEGIC
    result = _patch(kind, name, namespace, patch, content_type, dry_run)
    spec = sanitize(result).get("spec") or result.get("data")
    return f"{_label(dry_run)}Patched {kind} {namespace}/{name}. Resulting spec/data:\n{to_yaml(spec)}"


@tool(
    category="write",
    description="""Update keys in a ConfigMap. Set a value to null to delete that key. Pods
that read the ConfigMap at start-up need restart_workload afterwards.""",
    properties={
        "name": {"type": "string"},
        "namespace": NAMESPACE,
        "data": {"type": "object", "description": 'e.g. {"LOG_LEVEL": "info", "OLD_KEY": null}'},
        "dry_run": DRY_RUN,
    },
    required=["name", "namespace", "data"],
)
def update_configmap(name: str, namespace: str, data: dict, dry_run: bool = False) -> str:
    guards.check_namespace(namespace)
    if not data:
        raise ToolError("data must contain at least one key.")
    body = {"data": {k: (None if v is None else str(v)) for k, v in data.items()}}
    _patch("ConfigMap", name, namespace, body, MERGE, dry_run)
    return f"{_label(dry_run)}Updated ConfigMap {namespace}/{name} keys: {', '.join(data)}."


@tool(
    category="write",
    description="""Fix a BARE pod (no owner controller) by deleting it and creating it again with
a merge patch applied to its spec. Needed because most pod spec fields (nodeSelector,
env, resources, volumes, ...) cannot be changed on a running pod. Example spec_patch:
{"nodeSelector": null} removes the nodeSelector. Refuses pods owned by a controller:
patch the controller instead.""",
    properties={
        "name": {"type": "string"},
        "namespace": NAMESPACE,
        "spec_patch": {"type": "object", "description": "RFC 7386 merge patch applied to pod.spec."},
        "dry_run": DRY_RUN,
    },
    required=["name", "namespace", "spec_patch"],
)
def recreate_pod(name: str, namespace: str, spec_patch: dict, dry_run: bool = False) -> str:
    guards.check_namespace(namespace)
    guards.check_patch_body(spec_patch)
    pod = get_object("Pod", name, namespace)
    meta = pod["metadata"]
    if meta.get("ownerReferences"):
        owners = ", ".join(f"{o['kind']}/{o['name']}" for o in meta["ownerReferences"])
        raise ToolError(f"Pod is owned by {owners}; patch the owner instead of recreating the pod.")
    spec = merge_patch(pod.get("spec") or {}, spec_patch)
    if "nodeName" not in spec_patch:
        spec.pop("nodeName", None)  # let the scheduler place the new pod
    new_pod = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": meta.get("labels") or {},
            "annotations": {
                k: v
                for k, v in (meta.get("annotations") or {}).items()
                if k != "kubectl.kubernetes.io/last-applied-configuration"
            },
        },
        "spec": spec,
    }
    resource = Kube.resource("Pod")
    if dry_run:
        return (
            f"{_label(True)}Would delete and recreate pod {namespace}/{name} with spec:\n"
            f"{to_yaml(spec)}"
        )
    call(Kube.dynamic().delete, resource, name=name, namespace=namespace)
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            get_object("Pod", name, namespace)
        except ToolError as exc:
            if "404" in str(exc):
                break
            raise
        time.sleep(2)
    else:
        raise ToolError(f"Pod {namespace}/{name} did not terminate within 90s; it was not recreated.")
    call(Kube.dynamic().create, resource, body=new_pod, namespace=namespace)
    return f"Recreated pod {namespace}/{name} with the patched spec. Verify it with describe_pod."


@tool(
    category="write",
    description="""Roll a Deployment back to an earlier ReplicaSet revision (default: the one
before the current). Use get_rollout_status first to see revisions and images.""",
    properties={
        "name": {"type": "string"},
        "namespace": NAMESPACE,
        "to_revision": {"type": "integer", "description": "0 or omitted = previous revision."},
        "dry_run": DRY_RUN,
    },
    required=["name", "namespace"],
)
def rollback_deployment(name: str, namespace: str, to_revision: int = 0, dry_run: bool = False) -> str:
    guards.check_namespace(namespace)
    dep = get_object("Deployment", name, namespace)
    uid = dep["metadata"]["uid"]
    current = int((dep["metadata"].get("annotations") or {}).get("deployment.kubernetes.io/revision", "0"))
    revisions: dict[int, dict] = {}
    for rs in list_objects("ReplicaSet", namespace):
        if any(o.get("uid") == uid for o in rs["metadata"].get("ownerReferences") or []):
            rev = int((rs["metadata"].get("annotations") or {}).get("deployment.kubernetes.io/revision", "0"))
            revisions[rev] = rs
    if to_revision:
        target = int(to_revision)
    else:
        older = sorted(r for r in revisions if r < current)
        if not older:
            raise ToolError(f"No revision older than the current one ({current}) is available.")
        target = older[-1]
    if target not in revisions:
        raise ToolError(f"Revision {target} not found. Available: {sorted(revisions)}")
    template = revisions[target]["spec"]["template"]
    labels = dict((template.get("metadata") or {}).get("labels") or {})
    labels.pop("pod-template-hash", None)
    template = {**template, "metadata": {**(template.get("metadata") or {}), "labels": labels}}
    body = [{"op": "replace", "path": "/spec/template", "value": template}]
    _patch("Deployment", name, namespace, body, JSON_PATCH, dry_run)
    images = [c.get("image") for c in template["spec"].get("containers") or []]
    return f"{_label(dry_run)}Rolled back Deployment {namespace}/{name} from revision {current} to template of revision {target} (images {images})."


@tool(
    category="write",
    description="""Cordon (cordon=true) or uncordon (cordon=false) a node. Cordoning only stops
new pods from being scheduled; it does not evict running pods.""",
    properties={"name": {"type": "string"}, "cordon": {"type": "boolean"}, "dry_run": DRY_RUN},
    required=["name", "cordon"],
)
def cordon_node(name: str, cordon: bool, dry_run: bool = False) -> str:
    if cordon:
        guards.check_cordon(name, list_objects("Node"))
    _patch("Node", name, None, {"spec": {"unschedulable": bool(cordon)}}, MERGE, dry_run)
    return f"{_label(dry_run)}Node {name} {'cordoned' if cordon else 'uncordoned'}."


@tool(
    category="write",
    description="""Add, change or remove (value null) custom labels on a node, e.g. to satisfy a
nodeSelector. Labels managed by Kubernetes or CCE cannot be changed. Prefer changing the
workload's selector when the label request looks like a typo.""",
    properties={
        "name": {"type": "string"},
        "labels": {"type": "object", "description": 'e.g. {"type": "web"}'},
        "dry_run": DRY_RUN,
    },
    required=["name", "labels"],
)
def label_node(name: str, labels: dict, dry_run: bool = False) -> str:
    if not labels:
        raise ToolError("labels must not be empty.")
    guards.check_node_labels(labels)
    body = {"metadata": {"labels": {k: (None if v is None else str(v)) for k, v in labels.items()}}}
    _patch("Node", name, None, body, MERGE, dry_run)
    return f"{_label(dry_run)}Node {name} labels updated: {labels}."
