import pytest

from app.tools import api_tool_definitions, guards
from app.tools.kube import ToolError, merge_patch, normalize_kind, parse_quantity
from app.tools.read import is_unhealthy, pod_summary, required_affinity_ok, tolerates


def test_protected_namespaces():
    for ns in ("kube-system", "ai-agent", ""):
        with pytest.raises(ToolError):
            guards.check_namespace(ns)
    guards.check_namespace("default")


def test_patch_body_blocks_privilege_escalation():
    with pytest.raises(ToolError):
        guards.check_patch_body({"spec": {"template": {"spec": {"hostNetwork": True}}}})
    with pytest.raises(ToolError):
        guards.check_patch_body({"spec": {"containers": [{"name": "a", "securityContext": {"privileged": True}}]}})
    guards.check_patch_body({"spec": {"replicas": 2}})


def test_node_label_and_cordon_guards():
    with pytest.raises(ToolError):
        guards.check_node_labels({"kubernetes.io/hostname": "x"})
    with pytest.raises(ToolError):
        guards.check_node_labels({"cce.cloud.com/cce-nodepool": "x"})
    guards.check_node_labels({"type": "web"})
    ready = {"type": "Ready", "status": "True"}
    nodes = [
        {"metadata": {"name": "a"}, "status": {"conditions": [ready]}},
        {"metadata": {"name": "b"}, "status": {"conditions": [ready]}, "spec": {"unschedulable": True}},
    ]
    with pytest.raises(ToolError):
        guards.check_cordon("a", nodes)


def test_replicas_and_kinds():
    with pytest.raises(ToolError):
        guards.check_replicas(1000)
    with pytest.raises(ToolError):
        guards.check_patchable_kind("ClusterRole")
    with pytest.raises(ToolError):
        normalize_kind("Secret")
    assert normalize_kind("deploy") == "Deployment"


def test_merge_patch_and_quantities():
    assert merge_patch({"a": 1, "nodeSelector": {"type": "web"}}, {"nodeSelector": None}) == {"a": 1}
    assert merge_patch({"a": {"b": 1}}, {"a": {"c": 2}}) == {"a": {"b": 1, "c": 2}}
    assert parse_quantity("500m") == 0.5
    assert parse_quantity("1Gi") == 1024**3
    assert parse_quantity("2") == 2.0


def test_pod_summary_crashloop():
    pod = {
        "metadata": {"name": "p", "namespace": "default"},
        "spec": {"containers": [{"name": "c"}], "nodeName": "n1"},
        "status": {
            "phase": "Running",
            "containerStatuses": [
                {
                    "name": "c",
                    "ready": False,
                    "restartCount": 5,
                    "state": {"waiting": {"reason": "CrashLoopBackOff"}},
                    "lastState": {"terminated": {"reason": "OOMKilled", "exitCode": 137}},
                }
            ],
        },
    }
    s = pod_summary(pod)
    assert s["reason"] == "CrashLoopBackOff" and s["restarts"] == 5
    assert "OOMKilled" in s["last_termination"]
    assert is_unhealthy(s)


def test_scheduling_helpers():
    taint = {"key": "dedicated", "value": "gpu", "effect": "NoSchedule"}
    assert tolerates([{"key": "dedicated", "operator": "Equal", "value": "gpu"}], taint)
    assert tolerates([{"operator": "Exists"}], taint)
    assert not tolerates([{"key": "other", "operator": "Exists"}], taint)
    spec = {"affinity": {"nodeAffinity": {"requiredDuringSchedulingIgnoredDuringExecution": {
        "nodeSelectorTerms": [{"matchExpressions": [{"key": "zone", "operator": "In", "values": ["a"]}]}]}}}}
    assert required_affinity_ok(spec, {"zone": "a"}) is True
    assert required_affinity_ok(spec, {"zone": "b"}) is False
    assert required_affinity_ok({}, {}) is None


def test_tool_definitions_are_valid_and_stable():
    defs = api_tool_definitions()
    names = [d["name"] for d in defs]
    assert len(names) == len(set(names))
    assert names[:2] == ["propose_remediation", "report_outcome"]
    assert len([n for n in names]) == 2 + 14 + 11
    for d in defs:
        assert d["input_schema"]["type"] == "object"
        assert set(d["input_schema"]["required"]) <= set(d["input_schema"]["properties"])
        assert len(d["description"]) > 20
    read_only = api_tool_definitions(include_write=False)
    assert "recreate_pod" not in [d["name"] for d in read_only]
