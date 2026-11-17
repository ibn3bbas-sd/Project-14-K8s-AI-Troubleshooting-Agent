# Open Items

Things to close before this project is production-ready. When you close an item,
tick it, set **Status** to `Closed`, and write the decision or evidence in *Resolution*.
Milestones are described in [`docs/milestones.md`](docs/milestones.md).

Status values: `Open` · `In progress` · `Blocked` · `Closed` · `Accepted` (known limitation, won't fix)

_Last updated: 2026-11-17_

## Summary

| Priority | Open | Closed |
|---|---|---|
| P1 (blocks first deploy) | 8 | 0 |
| P2 (needed for production) | 10 | 0 |
| P3 (improvement) | 6 | 0 |

## Items

| ID | Pri | Milestone | Item | Status | Resolution |
|---|---|---|---|---|---|
| OI-01 | P1 | M1 | Choose the Huawei Cloud **region** and **SWR organization**; replace `swr.REGION.myhuaweicloud.com/ORG` and `CLUSTER_NAME` in `k8s/kustomization.yaml` | Open | |
| OI-02 | P1 | M2 | Confirm the CCE node subnet has a **NAT gateway SNAT rule + EIP**; test `curl https://api.anthropic.com` from a pod | Open | |
| OI-03 | P1 | M2 | Confirm the **Claude API** organization, key owner, billing and that the region is supported; store the key in `k8s/secrets/anthropic.env` | Open | |
| OI-04 | P1 | M2 | Check the **EVS disk type** (`everest.io/disk-volume-type: SAS` in `k8s/postgres.yaml`) is sold in the region; switch to SSD/GPSSD/ESSD if not | Open | |
| OI-05 | P1 | M2 | Verify **`default-secret`** is created in namespace `ai-agent` for SWR pulls | Open | |
| OI-06 | P1 | M1 | Mirror **`postgres:16-alpine`** into SWR (avoid Docker Hub egress / rate limits) and update `k8s/postgres.yaml` | Open | |
| OI-07 | P1 | M1 | **Images never built yet**: add the 4 SWR repo secrets, run `build-push-swr.yml` (or `k8s/scripts/build-push.sh`), fix any Dockerfile issues | Open | |
| OI-08 | P1 | M2 | **First live run**: the agent has only been tested with a fake Claude client and fake tools. Run it against a real CCE cluster and the real Claude API | Open | |
| OI-09 | P2 | M3 | Run the six scenarios in `k8s/test-scenarios/broken-workloads.yaml` end to end; record root cause, fix, tokens, cost, duration | Open | |
| OI-10 | P2 | M4 | **`pods: create` cluster-wide** (needed by `recreate_pod`) is powerful. Decide: namespace-scoped RoleBindings, or drop `recreate_pod` | Open | |
| OI-11 | P2 | M4 | The UI has **no authentication**. Add SSO / oauth2-proxy before any ELB exposure; until then port-forward only | Open | |
| OI-12 | P2 | M4 | **Audit trail**: store approver identity and every executed write (tool, args, result, time); needs OI-11 for identity | Open | |
| OI-13 | P3 | M6 | Backend is **single replica** (in-process run + lock). Decide on HA (queue + worker) or accept the limitation | Open | |
| OI-14 | P2 | M5 | Sessions left in status `running` after a pod restart are not recovered automatically (user must press Retry). Add start-up recovery | Open | |
| OI-15 | P2 | M4 | **Prompt-injection red-team**: hostile text in logs / annotations / ConfigMaps trying to trigger writes; confirm the approval gate holds | Open | |
| OI-16 | P2 | M5 | **Cost controls**: per-session token budget, monthly spend alert; pick `CLAUDE_EFFORT` from M3 measurements | Open | |
| OI-17 | P2 | M5 | **Postgres backup** (EVS snapshot / CBR) and retention clean-up of old sessions | Open | |
| OI-18 | P2 | M5 | **Observability**: verify AOM/Prometheus scrape of `/metrics`, build a dashboard, ship logs to LTS | Open | |
| OI-19 | P3 | M1 | If Kunpeng (arm64) node pools are used, build multi-arch images (`PLATFORM=linux/amd64,linux/arm64`) | Open | |
| OI-20 | P3 | M6 | Verify `PRICE_*_PER_MTOK` in the ConfigMap against current Claude pricing (used only for the UI estimate) | Open | |
| OI-21 | P2 | M3 | **Integration tests** for read/write tools against a real API server (kind or a CCE test cluster); today they are unit-tested with fakes only | Open | |
| OI-22 | P3 | M3 | `check_service_endpoints` reads `v1/Endpoints` (deprecated since K8s 1.33); switch to `discovery.k8s.io/v1` EndpointSlice | Open | |
| OI-23 | P3 | M4 | Move `ANTHROPIC_API_KEY` from a plain Kubernetes Secret to DEW/CSMS-backed secret management (e.g. CCE secrets add-on) and define key rotation | Open | |
| OI-24 | P3 | M6 | HLD shows the region as "e.g. me-east-1"; update `docs/hld/hld-diagram.py` once OI-01 is decided and regenerate the PNGs | Open | |

## Checklist

### P1 — blocks first deploy
- [ ] OI-01 Region + SWR organization
- [ ] OI-02 NAT/SNAT + EIP egress
- [ ] OI-03 Claude API account and key
- [ ] OI-04 EVS disk type
- [ ] OI-05 `default-secret`
- [ ] OI-06 Mirror Postgres image
- [ ] OI-07 Build and push images
- [ ] OI-08 First live run

### P2 — needed for production
- [ ] OI-09 Six test scenarios
- [ ] OI-10 Narrow `pods: create`
- [ ] OI-11 UI authentication
- [ ] OI-12 Audit trail
- [ ] OI-14 Recover stuck sessions
- [ ] OI-15 Prompt-injection red-team
- [ ] OI-16 Cost controls
- [ ] OI-17 Postgres backup
- [ ] OI-18 Observability
- [ ] OI-21 Integration tests

### P3 — improvements
- [ ] OI-13 Backend HA decision
- [ ] OI-19 Multi-arch images
- [ ] OI-20 Pricing values
- [ ] OI-22 EndpointSlice
- [ ] OI-23 Secret management
- [ ] OI-24 HLD region label

## Where to resume

1. Start with **M1**: OI-01 → OI-07 (choose region/org, set repo secrets, push to `main` to trigger the image build).
2. Then **M2**: OI-02 → OI-05, deploy with `kubectl apply -k k8s/`, and do OI-08.
3. Local checks that already pass: `cd agent-backend && python -m pytest -q` (15 tests), `cd agent-interface && npm ci && npm run build`.
