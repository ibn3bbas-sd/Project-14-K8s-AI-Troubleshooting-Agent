"""Read-only diagnostic tools. None of these change cluster state."""

from __future__ import annotations

from collections import Counter
from typing import Any

from kubernetes import client

from app.tools.kube import (
    KINDS,
    Kube,
    ToolError,
    age,
    call,
    is_namespaced,
    normalize_kind,
    parse_quantity,
    sanitize,
    to_dict,
    to_yaml,
)
from app.tools.registry import NAMESPACE, OPTIONAL_NAMESPACE, tool

# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------


def list_objects(kind: str, namespace: str = "", **kwargs: Any) -> list[dict]:
    kind = normalize_kind(kind)
    resource = Kube.resource(kind)
    ns = (namespace or None) if is_namespaced(kind) else None
    result = call(Kube.dynamic().get, resource, namespace=ns, **kwargs)
    return to_dict(result).get("items", []) or []


def get_object(kind: str, name: str, namespace: str = "") -> dict:
    kind = normalize_kind(kind)
    if is_namespaced(kind) and not namespace:
        raise ToolError(f"{kind} is namespaced: pass a namespace.")
    resource = Kube.resource(kind)
    ns = namespace if is_namespaced(kind) else None
    return to_dict(call(Kube.dynamic().get, resource, name=name, namespace=ns))


def pod_summary(pod: dict) -> dict:
    meta = pod.get("metadata", {})
    spec = pod.get("spec", {})
    status = pod.get("status", {})
    statuses = status.get("containerStatuses") or []
    init_statuses = status.get("initContainerStatuses") or []
    total = len(spec.get("containers") or [])
    ready = sum(1 for c in statuses if c.get("ready"))
    restarts = sum(c.get("restartCount", 0) for c in statuses)
    phase = status.get("phase", "Unknown")
    reason = status.get("reason") or phase
    for c in init_statuses:
        state = c.get("state") or {}
        if "waiting" in state and state["waiting"].get("reason"):
            reason = f"Init:{state['waiting']['reason']}"
            break
        term = state.get("terminated") or {}
        if term and term.get("exitCode", 0) != 0:
            reason = f"Init:{term.get('reason') or 'Error'}"
            break
    else:
        for c in statuses:
            state = c.get("state") or {}
            if "waiting" in state and state["waiting"].get("reason"):
                reason = state["waiting"]["reason"]
                break
            term = state.get("terminated") or {}
            if term and phase != "Succeeded":
                reason = term.get("reason") or "Terminated"
                break
    if phase == "Pending" and reason == "Pending":
        for cond in status.get("conditions") or []:
            if cond.get("type") == "PodScheduled" and cond.get("status") == "False":
                reason = cond.get("reason") or "Unschedulable"
    if meta.get("deletionTimestamp"):
        reason = "Terminating"
    last_term = ""
    for c in statuses:
        lt = (c.get("lastState") or {}).get("terminated")
        if lt:
            last_term = f"{c['name']}: {lt.get('reason')} (exit {lt.get('exitCode')})"
            break
    return {
        "namespace": meta.get("namespace", ""),
        "name": meta.get("name", ""),
        "ready": f"{ready}/{total}",
        "ready_ok": total > 0 and ready == total,
        "phase": phase,
        "reason": reason,
        "restarts": restarts,
        "node": spec.get("nodeName", ""),
        "age": age(meta.get("creationTimestamp")),
        "last_termination": last_term,
    }


def is_unhealthy(summary: dict) -> bool:
    if summary["phase"] == "Succeeded":
        return False
    if summary["reason"] in ("Running",) and summary["ready_ok"] and summary["restarts"] < 3:
        return False
    return True


def node_ready(node: dict) -> str:
    for cond in (node.get("status") or {}).get("conditions") or []:
        if cond.get("type") == "Ready":
            return {"True": "Ready", "False": "NotReady"}.get(cond.get("status"), "Unknown")
    return "Unknown"


def _event_time(ev: dict) -> str:
    return (
        ev.get("lastTimestamp")
        or ev.get("eventTime")
        or (ev.get("series") or {}).get("lastObservedTime")
        or ev.get("firstTimestamp")
        or (ev.get("metadata") or {}).get("creationTimestamp")
        or ""
    )


def format_events(events: list[dict], limit: int) -> str:
    events = sorted(events, key=_event_time, reverse=True)[:limit]
    if not events:
        return "No matching events."
    lines = []
    for ev in events:
        obj = ev.get("involvedObject") or {}
        count = ev.get("count") or (ev.get("series") or {}).get("count") or 1
        lines.append(
            f"[{age(_event_time(ev))} ago] {ev.get('type', '')} {ev.get('reason', '')} "
            f"{obj.get('kind', '')}/{obj.get('namespace', '') + '/' if obj.get('namespace') else ''}"
            f"{obj.get('name', '')} (x{count}): {(ev.get('message') or '').strip()}"
        )
    return "\n".join(lines)


def pod_requests(pod: dict) -> tuple[float, float]:
    cpu = mem = 0.0
    spec = pod.get("spec", {})
    for c in spec.get("containers") or []:
        req = (c.get("resources") or {}).get("requests") or {}
        cpu += parse_quantity(req.get("cpu"))
        mem += parse_quantity(req.get("memory"))
    # init containers run sequentially; the effective request is the max.
    for c in spec.get("initContainers") or []:
        req = (c.get("resources") or {}).get("requests") or {}
        cpu = max(cpu, parse_quantity(req.get("cpu")))
        mem = max(mem, parse_quantity(req.get("memory")))
    return cpu, mem


def _fmt_cpu(cores: float) -> str:
    return f"{int(round(cores * 1000))}m"


def _fmt_mem(num_bytes: float) -> str:
    return f"{num_bytes / 1024**2:.0f}Mi"


def tolerates(tolerations: list[dict], taint: dict) -> bool:
    for tol in tolerations or []:
        op = tol.get("operator", "Equal")
        key_ok = (not tol.get("key") and op == "Exists") or tol.get("key") == taint.get("key")
        value_ok = op == "Exists" or tol.get("value", "") == taint.get("value", "")
        effect_ok = not tol.get("effect") or tol.get("effect") == taint.get("effect")
        if key_ok and value_ok and effect_ok:
            return True
    return False


def _match_expression(labels: dict, expr: dict) -> bool:
    key, op, values = expr.get("key"), expr.get("operator"), expr.get("values") or []
    if op == "In":
        return labels.get(key) in values
    if op == "NotIn":
        return labels.get(key) not in values
    if op == "Exists":
        return key in labels
    if op == "DoesNotExist":
        return key not in labels
    if op in ("Gt", "Lt") and key in labels and values:
        try:
            a, b = int(labels[key]), int(values[0])
        except ValueError:
            return False
        return a > b if op == "Gt" else a < b
    return False


def required_affinity_ok(pod_spec: dict, labels: dict) -> bool | None:
    """Evaluate requiredDuringScheduling node affinity. None = no rule."""
    terms = (
        ((pod_spec.get("affinity") or {}).get("nodeAffinity") or {})
        .get("requiredDuringSchedulingIgnoredDuringExecution", {})
        .get("nodeSelectorTerms")
    )
    if not terms:
        return None
    return any(
        all(_match_expression(labels, e) for e in (t.get("matchExpressions") or []))
        for t in terms
    )


def _active_pods_on_node(node_name: str) -> list[dict]:
    return [
        p
        for p in list_objects("Pod", field_selector=f"spec.nodeName={node_name}")
        if (p.get("status") or {}).get("phase") not in ("Succeeded", "Failed")
    ]


# ----------------------------------------------------------------------------
# tools
# ----------------------------------------------------------------------------


@tool(
    category="read",
    description="""High-level health snapshot of the whole cluster: Kubernetes version,
node readiness, pod counts by phase, namespaces with unhealthy pods, and recent
warning-event volume. Good first call when the request is vague
("something is broken", "check my cluster").""",
)
def cluster_overview() -> str:
    lines = []
    try:
        version = call(client.VersionApi(Kube.api_client()).get_code)
        lines.append(f"Kubernetes version: {version.git_version}")
    except ToolError as exc:
        lines.append(f"Kubernetes version: unavailable ({exc})")

    nodes = list_objects("Node")
    states = Counter(node_ready(n) for n in nodes)
    lines.append(f"Nodes: {len(nodes)} total, " + ", ".join(f"{k}={v}" for k, v in states.items()))
    for n in nodes:
        if node_ready(n) != "Ready" or (n.get("spec") or {}).get("unschedulable"):
            flag = " (cordoned)" if (n.get("spec") or {}).get("unschedulable") else ""
            lines.append(f"  - {n['metadata']['name']}: {node_ready(n)}{flag}")

    pods = [pod_summary(p) for p in list_objects("Pod")]
    lines.append(
        f"Pods: {len(pods)} total, "
        + ", ".join(f"{k}={v}" for k, v in Counter(p["phase"] for p in pods).items())
    )
    unhealthy = [p for p in pods if is_unhealthy(p)]
    by_ns = Counter(p["namespace"] for p in unhealthy)
    if by_ns:
        lines.append("Namespaces with unhealthy pods (use find_unhealthy_pods for details):")
        for ns, count in by_ns.most_common(15):
            reasons = Counter(p["reason"] for p in unhealthy if p["namespace"] == ns)
            lines.append(f"  - {ns}: {count} ({', '.join(f'{r}={c}' for r, c in reasons.items())})")
    else:
        lines.append("No unhealthy pods detected.")

    warnings = list_objects("Event", field_selector="type=Warning", limit=500)
    reasons = Counter(ev.get("reason", "") for ev in warnings)
    lines.append(
        f"Warning events retained by the API server: {len(warnings)}"
        + (" (top reasons: " + ", ".join(f"{r}={c}" for r, c in reasons.most_common(6)) + ")" if reasons else "")
    )
    return "\n".join(lines)


@tool(
    category="read",
    description=f"""List objects of one kind with a one-line status per object (like
`kubectl get`). Supported kinds: {", ".join(sorted(KINDS))}. Secrets are not readable.""",
    properties={
        "kind": {"type": "string", "description": "Resource kind, e.g. Pod, Deployment, Node."},
        "namespace": OPTIONAL_NAMESPACE,
        "label_selector": {"type": "string", "description": "e.g. app=web,tier!=cache"},
        "field_selector": {"type": "string", "description": "e.g. status.phase=Pending"},
        "limit": {"type": "integer", "description": "Max objects to return (default 100)."},
    },
    required=["kind"],
)
def list_resources(
    kind: str,
    namespace: str = "",
    label_selector: str = "",
    field_selector: str = "",
    limit: int = 100,
) -> str:
    kind = normalize_kind(kind)
    kwargs: dict[str, Any] = {}
    if label_selector:
        kwargs["label_selector"] = label_selector
    if field_selector:
        kwargs["field_selector"] = field_selector
    items = list_objects(kind, namespace, **kwargs)
    limit = max(1, min(int(limit or 100), 500))
    if not items:
        return f"No {kind} objects found."
    rows = []
    for obj in items[:limit]:
        meta = obj.get("metadata", {})
        spec = obj.get("spec") or {}
        st = obj.get("status") or {}
        prefix = f"{meta.get('namespace')}/" if meta.get("namespace") else ""
        detail = ""
        if kind == "Pod":
            s = pod_summary(obj)
            detail = f"ready={s['ready']} status={s['reason']} restarts={s['restarts']} node={s['node'] or '-'}"
        elif kind in ("Deployment", "StatefulSet"):
            detail = f"ready={st.get('readyReplicas', 0)}/{spec.get('replicas', 0)} updated={st.get('updatedReplicas', 0)}"
        elif kind == "DaemonSet":
            detail = f"ready={st.get('numberReady', 0)}/{st.get('desiredNumberScheduled', 0)}"
        elif kind == "ReplicaSet":
            detail = f"ready={st.get('readyReplicas', 0)}/{spec.get('replicas', 0)}"
        elif kind == "Node":
            detail = f"{node_ready(obj)}{' SchedulingDisabled' if spec.get('unschedulable') else ''}"
        elif kind == "PersistentVolumeClaim":
            detail = f"{st.get('phase')} sc={spec.get('storageClassName')} size={(st.get('capacity') or {}).get('storage', '-')}"
        elif kind == "PersistentVolume":
            detail = f"{st.get('phase')} claim={(spec.get('claimRef') or {}).get('name', '-')}"
        elif kind == "Service":
            ports = ",".join(f"{p.get('port')}->{p.get('targetPort')}" for p in spec.get("ports") or [])
            detail = f"type={spec.get('type')} clusterIP={spec.get('clusterIP')} ports={ports}"
        elif kind == "Job":
            detail = f"succeeded={st.get('succeeded', 0)} failed={st.get('failed', 0)} active={st.get('active', 0)}"
        elif kind == "Event":
            detail = f"{obj.get('type')} {obj.get('reason')}: {(obj.get('message') or '')[:160]}"
        elif kind == "Namespace":
            detail = st.get("phase", "")
        rows.append(f"{prefix}{meta.get('name')}  age={age(meta.get('creationTimestamp'))}  {detail}".rstrip())
    more = f"\n... {len(items) - limit} more not shown" if len(items) > limit else ""
    return f"{len(items)} {kind} object(s):\n" + "\n".join(rows) + more


@tool(
    category="read",
    description="""Return the full manifest (YAML, with managedFields stripped) of one object,
including spec and status. Use it to inspect exact configuration.""",
    properties={
        "kind": {"type": "string"},
        "name": {"type": "string"},
        "namespace": {"type": "string", "description": "Required for namespaced kinds."},
    },
    required=["kind", "name"],
)
def get_resource(kind: str, name: str, namespace: str = "") -> str:
    return to_yaml(sanitize(get_object(kind, name, namespace)))


@tool(
    category="read",
    description="""Find pods that are not healthy: Pending, CrashLoopBackOff, ImagePullBackOff,
Error, OOMKilled, not ready, or restarting repeatedly. Shows the reason, restarts,
node and the last termination reason.""",
    properties={"namespace": OPTIONAL_NAMESPACE},
)
def find_unhealthy_pods(namespace: str = "") -> str:
    pods = [pod_summary(p) for p in list_objects("Pod", namespace)]
    bad = [p for p in pods if is_unhealthy(p)]
    if not bad:
        return f"All {len(pods)} pod(s) look healthy."
    lines = [f"{len(bad)} unhealthy pod(s) out of {len(pods)}:"]
    for p in bad[:100]:
        lines.append(
            f"- {p['namespace']}/{p['name']}: {p['reason']} ready={p['ready']} restarts={p['restarts']} "
            f"node={p['node'] or '-'} age={p['age']}"
            + (f" last_termination=[{p['last_termination']}]" if p["last_termination"] else "")
        )
    return "\n".join(lines)


@tool(
    category="read",
    description="""Condensed `kubectl describe pod`: owner, node, conditions, each container's
image/state/last state/restarts/resources/probes, referenced ConfigMaps/Secrets/PVCs
(names only), scheduling constraints, and the pod's recent events.""",
    properties={"name": {"type": "string"}, "namespace": NAMESPACE},
    required=["name", "namespace"],
)
def describe_pod(name: str, namespace: str) -> str:
    pod = get_object("Pod", name, namespace)
    meta, spec, status = pod.get("metadata", {}), pod.get("spec", {}), pod.get("status", {})
    s = pod_summary(pod)
    out = [
        f"Pod {namespace}/{name}",
        f"Status: {s['reason']} (phase {s['phase']}), ready {s['ready']}, restarts {s['restarts']}, age {s['age']}",
        f"Node: {spec.get('nodeName') or '<not scheduled>'}  ServiceAccount: {spec.get('serviceAccountName')}",
        "Owners: " + (", ".join(f"{o['kind']}/{o['name']}" for o in meta.get("ownerReferences") or []) or "none (bare pod)"),
        "Labels: " + ", ".join(f"{k}={v}" for k, v in (meta.get("labels") or {}).items()),
    ]
    if status.get("message"):
        out.append(f"Message: {status['message']}")
    out.append("Conditions:")
    for c in status.get("conditions") or []:
        out.append(
            f"  {c.get('type')}={c.get('status')}"
            + (f" reason={c.get('reason')}" if c.get("reason") else "")
            + (f" message={c.get('message')}" if c.get("message") else "")
        )
    statuses = {c["name"]: c for c in (status.get("containerStatuses") or []) + (status.get("initContainerStatuses") or [])}
    for kind_label, containers in (("Init container", spec.get("initContainers") or []), ("Container", spec.get("containers") or [])):
        for c in containers:
            cs = statuses.get(c["name"], {})
            out.append(f"{kind_label} {c['name']}:")
            out.append(f"  image: {c.get('image')}  pullPolicy: {c.get('imagePullPolicy')}")
            if c.get("command") or c.get("args"):
                out.append(f"  command: {c.get('command')} args: {c.get('args')}")
            out.append(f"  state: {cs.get('state')}")
            if cs.get("lastState"):
                out.append(f"  lastState: {cs.get('lastState')}")
            out.append(f"  ready: {cs.get('ready')} restarts: {cs.get('restartCount', 0)}")
            out.append(f"  resources: {c.get('resources') or {}}")
            for probe in ("livenessProbe", "readinessProbe", "startupProbe"):
                if c.get(probe):
                    out.append(f"  {probe}: {c[probe]}")
            if c.get("ports"):
                out.append("  ports: " + ", ".join(f"{p.get('name', '')}:{p.get('containerPort')}/{p.get('protocol', 'TCP')}" for p in c["ports"]))
            refs = []
            for ef in c.get("envFrom") or []:
                if ef.get("configMapRef"):
                    refs.append(f"configMap/{ef['configMapRef']['name']}")
                if ef.get("secretRef"):
                    refs.append(f"secret/{ef['secretRef']['name']}")
            for env in c.get("env") or []:
                vf = env.get("valueFrom") or {}
                if vf.get("configMapKeyRef"):
                    refs.append(f"configMap/{vf['configMapKeyRef'].get('name')}[{vf['configMapKeyRef'].get('key')}]")
                if vf.get("secretKeyRef"):
                    refs.append(f"secret/{vf['secretKeyRef'].get('name')}[{vf['secretKeyRef'].get('key')}]")
            if refs:
                out.append("  env references: " + ", ".join(refs))
    vols = []
    for v in spec.get("volumes") or []:
        if v.get("persistentVolumeClaim"):
            vols.append(f"{v['name']}=pvc/{v['persistentVolumeClaim'].get('claimName')}")
        elif v.get("configMap"):
            vols.append(f"{v['name']}=configMap/{v['configMap'].get('name')}")
        elif v.get("secret"):
            vols.append(f"{v['name']}=secret/{v['secret'].get('secretName')}")
    if vols:
        out.append("Volumes: " + ", ".join(vols))
    if spec.get("nodeSelector"):
        out.append(f"nodeSelector: {spec['nodeSelector']}")
    if spec.get("affinity"):
        out.append(f"affinity: {spec['affinity']}")
    if spec.get("tolerations"):
        out.append(f"tolerations: {spec['tolerations']}")
    if spec.get("imagePullSecrets"):
        out.append("imagePullSecrets: " + ", ".join(s.get("name", "") for s in spec["imagePullSecrets"]))
    events = list_objects(
        "Event", namespace, field_selector=f"involvedObject.name={name},involvedObject.kind=Pod"
    )
    out.append("Events:\n" + format_events(events, 15))
    return "\n".join(out)


@tool(
    category="read",
    description="""Read container logs. Set previous=true to read the logs of the last crashed
instance (essential for CrashLoopBackOff). For multi-container pods pass container.""",
    properties={
        "name": {"type": "string", "description": "Pod name."},
        "namespace": NAMESPACE,
        "container": {"type": "string"},
        "previous": {"type": "boolean"},
        "tail_lines": {"type": "integer", "description": "Default 200, max 1000."},
        "since_seconds": {"type": "integer"},
    },
    required=["name", "namespace"],
)
def get_pod_logs(
    name: str,
    namespace: str,
    container: str = "",
    previous: bool = False,
    tail_lines: int = 200,
    since_seconds: int = 0,
) -> str:
    if not container:
        pod = get_object("Pod", name, namespace)
        names = [c["name"] for c in pod.get("spec", {}).get("containers") or []]
        if len(names) > 1:
            raise ToolError(f"Pod has several containers; pass one of: {', '.join(names)}")
        container = names[0] if names else ""
    kwargs: dict[str, Any] = {
        "container": container,
        "previous": bool(previous),
        "tail_lines": max(1, min(int(tail_lines or 200), 1000)),
        "timestamps": True,
    }
    if since_seconds:
        kwargs["since_seconds"] = int(since_seconds)
    logs = call(Kube.core().read_namespaced_pod_log, name, namespace, **kwargs)
    return logs.strip() or "(no log output)"


@tool(
    category="read",
    description="""List recent Kubernetes events, newest first. Filter by namespace and/or
involved object. Warnings only by default.""",
    properties={
        "namespace": OPTIONAL_NAMESPACE,
        "involved_name": {"type": "string", "description": "Name of the involved object."},
        "involved_kind": {"type": "string", "description": "Kind of the involved object."},
        "warnings_only": {"type": "boolean", "description": "Default true."},
        "limit": {"type": "integer", "description": "Default 40."},
    },
)
def list_events(
    namespace: str = "",
    involved_name: str = "",
    involved_kind: str = "",
    warnings_only: bool = True,
    limit: int = 40,
) -> str:
    selectors = []
    if involved_name:
        selectors.append(f"involvedObject.name={involved_name}")
    if involved_kind:
        selectors.append(f"involvedObject.kind={normalize_kind(involved_kind)}")
    if warnings_only:
        selectors.append("type=Warning")
    kwargs = {"field_selector": ",".join(selectors)} if selectors else {}
    events = list_objects("Event", namespace, **kwargs)
    return format_events(events, max(1, min(int(limit or 40), 200)))


@tool(
    category="read",
    description="""List nodes with readiness, schedulability, CCE node pool, availability zone,
instance flavor, kubelet version, allocatable CPU/memory and taints.""",
)
def list_nodes() -> str:
    nodes = list_objects("Node")
    if not nodes:
        return "No nodes found."
    lines = []
    for n in nodes:
        meta, spec, st = n["metadata"], n.get("spec") or {}, n.get("status") or {}
        labels = meta.get("labels") or {}
        alloc = st.get("allocatable") or {}
        taints = ", ".join(f"{t.get('key')}={t.get('value', '')}:{t.get('effect')}" for t in spec.get("taints") or [])
        lines.append(
            f"- {meta['name']}: {node_ready(n)}{' SchedulingDisabled' if spec.get('unschedulable') else ''} "
            f"pool={labels.get('cce.cloud.com/cce-nodepool', '-')} "
            f"zone={labels.get('topology.kubernetes.io/zone') or labels.get('failure-domain.beta.kubernetes.io/zone', '-')} "
            f"flavor={labels.get('node.kubernetes.io/instance-type', '-')} "
            f"kubelet={(st.get('nodeInfo') or {}).get('kubeletVersion', '-')} "
            f"alloc_cpu={alloc.get('cpu')} alloc_mem={alloc.get('memory')} alloc_pods={alloc.get('pods')}"
            + (f" taints=[{taints}]" if taints else "")
        )
    return "\n".join(lines)


@tool(
    category="read",
    description="""Details for one node: conditions (memory/disk/PID pressure, readiness), taints,
labels, capacity vs allocatable, and the CPU/memory already requested by pods on it.""",
    properties={"name": {"type": "string"}},
    required=["name"],
)
def describe_node(name: str) -> str:
    node = get_object("Node", name)
    meta, spec, st = node["metadata"], node.get("spec") or {}, node.get("status") or {}
    out = [f"Node {name}: {node_ready(node)}{' (cordoned)' if spec.get('unschedulable') else ''}"]
    out.append("Conditions:")
    for c in st.get("conditions") or []:
        out.append(f"  {c.get('type')}={c.get('status')} {c.get('reason', '')}: {c.get('message', '')}")
    out.append("Taints: " + (", ".join(f"{t.get('key')}={t.get('value', '')}:{t.get('effect')}" for t in spec.get("taints") or []) or "none"))
    out.append("Labels: " + ", ".join(f"{k}={v}" for k, v in (meta.get("labels") or {}).items()))
    alloc = st.get("allocatable") or {}
    pods = _active_pods_on_node(name)
    cpu = sum(pod_requests(p)[0] for p in pods)
    mem = sum(pod_requests(p)[1] for p in pods)
    a_cpu, a_mem = parse_quantity(alloc.get("cpu")), parse_quantity(alloc.get("memory"))
    out.append(f"Capacity: {st.get('capacity')}")
    out.append(
        f"Requested: cpu {_fmt_cpu(cpu)} of {_fmt_cpu(a_cpu)} allocatable"
        f" ({cpu / a_cpu * 100 if a_cpu else 0:.0f}%), memory {_fmt_mem(mem)} of {_fmt_mem(a_mem)}"
        f" ({mem / a_mem * 100 if a_mem else 0:.0f}%), pods {len(pods)} of {alloc.get('pods')}"
    )
    info = st.get("nodeInfo") or {}
    out.append(f"OS: {info.get('osImage')}  runtime: {info.get('containerRuntimeVersion')}  kubelet: {info.get('kubeletVersion')}")
    return "\n".join(out)


@tool(
    category="read",
    description="""Explain why a Pending pod cannot be scheduled. Checks every node against the
pod's nodeSelector, required node affinity, taints/tolerations, cordoning, readiness
and free CPU/memory versus the pod's requests.""",
    properties={"name": {"type": "string", "description": "Pod name."}, "namespace": NAMESPACE},
    required=["name", "namespace"],
)
def check_scheduling(name: str, namespace: str) -> str:
    pod = get_object("Pod", name, namespace)
    spec = pod.get("spec", {})
    selector = spec.get("nodeSelector") or {}
    tolerations = spec.get("tolerations") or []
    req_cpu, req_mem = pod_requests(pod)
    out = [
        f"Pod {namespace}/{name}: nodeSelector={selector or '{}'} requests cpu={_fmt_cpu(req_cpu)} memory={_fmt_mem(req_mem)}",
    ]
    if spec.get("nodeName"):
        out.append(f"Pod is already bound to node {spec['nodeName']}.")
    eligible = 0
    pods_by_node: dict[str, list[dict]] = {}
    for p in list_objects("Pod"):
        if (p.get("status") or {}).get("phase") not in ("Succeeded", "Failed") and p.get("spec", {}).get("nodeName"):
            pods_by_node.setdefault(p["spec"]["nodeName"], []).append(p)
    for node in list_objects("Node"):
        nname = node["metadata"]["name"]
        labels = node["metadata"].get("labels") or {}
        nspec, nst = node.get("spec") or {}, node.get("status") or {}
        problems = []
        if node_ready(node) != "Ready":
            problems.append("node not Ready")
        if nspec.get("unschedulable"):
            problems.append("node cordoned")
        missing = {k: v for k, v in selector.items() if labels.get(k) != v}
        if missing:
            problems.append(f"nodeSelector mismatch {missing} (node has {[f'{k}={labels.get(k)}' for k in missing]})")
        aff = required_affinity_ok(spec, labels)
        if aff is False:
            problems.append("required nodeAffinity not satisfied")
        for taint in nspec.get("taints") or []:
            if taint.get("effect") in ("NoSchedule", "NoExecute") and not tolerates(tolerations, taint):
                problems.append(f"untolerated taint {taint.get('key')}={taint.get('value', '')}:{taint.get('effect')}")
        alloc = nst.get("allocatable") or {}
        used = pods_by_node.get(nname, [])
        free_cpu = parse_quantity(alloc.get("cpu")) - sum(pod_requests(p)[0] for p in used)
        free_mem = parse_quantity(alloc.get("memory")) - sum(pod_requests(p)[1] for p in used)
        if req_cpu > free_cpu:
            problems.append(f"insufficient cpu (free {_fmt_cpu(free_cpu)})")
        if req_mem > free_mem:
            problems.append(f"insufficient memory (free {_fmt_mem(free_mem)})")
        if len(used) >= int(parse_quantity(alloc.get("pods")) or 110):
            problems.append("pod count limit reached")
        if problems:
            out.append(f"- {nname}: NOT eligible: " + "; ".join(problems))
        else:
            eligible += 1
            out.append(f"- {nname}: eligible (free cpu {_fmt_cpu(free_cpu)}, memory {_fmt_mem(free_mem)})")
    out.append(f"Eligible nodes: {eligible}. (PVC zone binding and pod anti-affinity are not evaluated here; check events for those.)")
    return "\n".join(out)


@tool(
    category="read",
    description="""Diagnose a Service that does not route traffic: compares its selector and
target ports with the matching pods (and their readiness/container ports) and shows
the ready/not-ready endpoint addresses.""",
    properties={"name": {"type": "string", "description": "Service name."}, "namespace": NAMESPACE},
    required=["name", "namespace"],
)
def check_service_endpoints(name: str, namespace: str) -> str:
    svc = get_object("Service", name, namespace)
    spec = svc.get("spec", {})
    selector = spec.get("selector") or {}
    out = [f"Service {namespace}/{name} type={spec.get('type')} selector={selector or 'NONE'}"]
    for p in spec.get("ports") or []:
        out.append(f"  port {p.get('name', '')} {p.get('port')}/{p.get('protocol', 'TCP')} -> targetPort {p.get('targetPort')}" + (f" nodePort {p.get('nodePort')}" if p.get("nodePort") else ""))
    annotations = (svc.get("metadata") or {}).get("annotations") or {}
    elb = {k: v for k, v in annotations.items() if "elb" in k}
    if elb:
        out.append(f"  ELB annotations: {elb}")
    if spec.get("type") == "LoadBalancer":
        ingress = ((svc.get("status") or {}).get("loadBalancer") or {}).get("ingress")
        out.append(f"  LoadBalancer status: {ingress or 'PENDING (no address assigned; check service events)'}")
    if not selector:
        out.append("Service has no selector: endpoints must be managed manually.")
    else:
        label_selector = ",".join(f"{k}={v}" for k, v in selector.items())
        pods = list_objects("Pod", namespace, label_selector=label_selector)
        out.append(f"Pods matching selector: {len(pods)}")
        for p in pods:
            s = pod_summary(p)
            ports = [
                f"{cp.get('name', '')}:{cp.get('containerPort')}"
                for c in p.get("spec", {}).get("containers") or []
                for cp in c.get("ports") or []
            ]
            out.append(f"  - {s['name']}: {s['reason']} ready={s['ready']} containerPorts={ports or 'none declared'}")
        if not pods:
            similar = list_objects("Pod", namespace)
            out.append("  No pods match. Labels of pods in this namespace:")
            for p in similar[:15]:
                out.append(f"    {p['metadata']['name']}: {p['metadata'].get('labels')}")
    try:
        ep = get_object("Endpoints", name, namespace)
        ready = [a.get("ip") for sub in ep.get("subsets") or [] for a in sub.get("addresses") or []]
        not_ready = [a.get("ip") for sub in ep.get("subsets") or [] for a in sub.get("notReadyAddresses") or []]
        out.append(f"Endpoints: ready={ready or 'none'} notReady={not_ready or 'none'}")
    except ToolError as exc:
        out.append(f"Endpoints: {exc}")
    return "\n".join(out)


def _metrics(plural: str, namespace: str = "") -> list[dict]:
    api = Kube.custom()
    try:
        if namespace:
            data = call(api.list_namespaced_custom_object, "metrics.k8s.io", "v1beta1", namespace, plural)
        else:
            data = call(api.list_cluster_custom_object, "metrics.k8s.io", "v1beta1", plural)
    except ToolError as exc:
        raise ToolError(
            f"Metrics API unavailable ({exc}). On CCE, install the metrics-server or "
            "Cloud Native Cluster Monitoring add-on to enable usage metrics."
        ) from exc
    return data.get("items", [])


@tool(
    category="read",
    description="""Current CPU/memory usage per pod from the metrics API (like `kubectl top pods`),
sorted by the chosen resource. Useful for OOMKilled or throttling investigations.""",
    properties={
        "namespace": OPTIONAL_NAMESPACE,
        "sort_by": {"type": "string", "enum": ["cpu", "memory"]},
        "limit": {"type": "integer", "description": "Default 25."},
    },
)
def top_pods(namespace: str = "", sort_by: str = "memory", limit: int = 25) -> str:
    rows = []
    for item in _metrics("pods", namespace):
        cpu = sum(parse_quantity(c["usage"].get("cpu")) for c in item.get("containers", []))
        mem = sum(parse_quantity(c["usage"].get("memory")) for c in item.get("containers", []))
        rows.append((item["metadata"].get("namespace"), item["metadata"]["name"], cpu, mem))
    rows.sort(key=lambda r: r[2] if sort_by == "cpu" else r[3], reverse=True)
    if not rows:
        return "No pod metrics returned."
    return "\n".join(f"{ns}/{n}: cpu={_fmt_cpu(c)} memory={_fmt_mem(m)}" for ns, n, c, m in rows[: max(1, int(limit or 25))])


@tool(
    category="read",
    description="Current CPU/memory usage per node from the metrics API (like `kubectl top nodes`).",
)
def top_nodes() -> str:
    allocs = {n["metadata"]["name"]: (n.get("status") or {}).get("allocatable") or {} for n in list_objects("Node")}
    lines = []
    for item in _metrics("nodes"):
        nname = item["metadata"]["name"]
        cpu, mem = parse_quantity(item["usage"].get("cpu")), parse_quantity(item["usage"].get("memory"))
        a = allocs.get(nname, {})
        a_cpu, a_mem = parse_quantity(a.get("cpu")), parse_quantity(a.get("memory"))
        lines.append(
            f"{nname}: cpu={_fmt_cpu(cpu)} ({cpu / a_cpu * 100 if a_cpu else 0:.0f}%) "
            f"memory={_fmt_mem(mem)} ({mem / a_mem * 100 if a_mem else 0:.0f}%)"
        )
    return "\n".join(lines) or "No node metrics returned."


@tool(
    category="read",
    description="""Rollout status of a Deployment, StatefulSet or DaemonSet: desired/updated/
ready replicas, conditions (e.g. ProgressDeadlineExceeded) and, for Deployments,
the ReplicaSet revision history with images, so a bad rollout can be identified.""",
    properties={
        "kind": {"type": "string", "enum": ["Deployment", "StatefulSet", "DaemonSet"]},
        "name": {"type": "string"},
        "namespace": NAMESPACE,
    },
    required=["kind", "name", "namespace"],
)
def get_rollout_status(kind: str, name: str, namespace: str) -> str:
    kind = normalize_kind(kind)
    obj = get_object(kind, name, namespace)
    meta, spec, st = obj["metadata"], obj.get("spec") or {}, obj.get("status") or {}
    out = [
        f"{kind} {namespace}/{name}: generation={meta.get('generation')} observed={st.get('observedGeneration')}",
        f"status: {({k: v for k, v in st.items() if k != 'conditions'})}",
    ]
    if spec.get("selector"):
        out.append(f"selector: {spec['selector']}")
    for c in st.get("conditions") or []:
        out.append(f"condition {c.get('type')}={c.get('status')} {c.get('reason', '')}: {c.get('message', '')}")
    if kind == "Deployment":
        uid = meta.get("uid")
        rss = [
            rs
            for rs in list_objects("ReplicaSet", namespace)
            if any(o.get("uid") == uid for o in rs["metadata"].get("ownerReferences") or [])
        ]
        rss.sort(key=lambda r: int((r["metadata"].get("annotations") or {}).get("deployment.kubernetes.io/revision", "0")))
        out.append("Revisions (oldest first):")
        for rs in rss:
            rev = (rs["metadata"].get("annotations") or {}).get("deployment.kubernetes.io/revision")
            images = [c.get("image") for c in rs["spec"]["template"]["spec"].get("containers") or []]
            out.append(
                f"  rev {rev}: {rs['metadata']['name']} replicas={rs['spec'].get('replicas')} "
                f"ready={(rs.get('status') or {}).get('readyReplicas', 0)} images={images} age={age(rs['metadata'].get('creationTimestamp'))}"
            )
    return "\n".join(out)
