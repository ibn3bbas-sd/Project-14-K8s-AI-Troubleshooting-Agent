# Milestones

Delivery plan for the K8s AI Troubleshooting Agent on Huawei Cloud CCE.
Each milestone exists on GitHub (Issues → Milestones) with the same name.
Target dates are proposals from the project start (2026-11-17); adjust as needed.
Open work is tracked item by item in [`../open-items.md`](../open-items.md) (column *Milestone*).

| # | Milestone | Target | Status |
|---|---|---|---|
| M0 | Design & scaffold | 2026-11-17 | ✅ Done |
| M1 | Build & registry (SWR) | 2026-11-24 | ⏳ Not started |
| M2 | First deployment on CCE (dev) | 2026-12-01 | ⏳ Not started |
| M3 | Validation with test scenarios | 2026-12-08 | ⏳ Not started |
| M4 | Security hardening | 2026-12-22 | ⏳ Not started |
| M5 | Observability, cost & operations | 2027-01-05 | ⏳ Not started |
| M6 | Production readiness & handover | 2027-01-12 | ⏳ Not started |

---

## M0 — Design & scaffold ✅

Re-implementation of the DevOpsCube Kubernetes AI agent design for the Claude API and CCE.

- [x] Backend: FastAPI, agent loop, approval gate, 14 read / 11 write / 2 control tools, guards
- [x] Claude API integration (Opus 5.5, adaptive thinking, prompt caching, refusal fallback)
- [x] Session store (PostgreSQL, in-memory fallback), metrics, JSON logs
- [x] Next.js UI with plan approval, per-issue selection, guidance, retry, cost display
- [x] Kustomize manifests for CCE (RBAC, EVS-backed Postgres, SWR images, `default-secret`)
- [x] Six broken test workloads; GitHub Actions workflow for SWR
- [x] Unit tests (15, fake Claude client); UI build + type check; manifest render
- [x] HLD diagram (`docs/hld/`), milestones, open items

**Exit criteria:** code compiles, tests pass, manifests render. *(met)*

## M1 — Build & registry (SWR)

- [ ] Choose the Huawei Cloud region and SWR organization (OI-01)
- [ ] Add repository secrets `SWR_REGISTRY`, `SWR_ORG`, `SWR_USERNAME`, `SWR_PASSWORD` (OI-07)
- [ ] First green run of `build-push-swr.yml`; images `ai-agent-backend` / `ai-agent-frontend` in SWR
- [ ] Mirror `postgres:16-alpine` into SWR (OI-06)
- [ ] Decide on multi-arch images if Kunpeng node pools are used (OI-19)

**Exit criteria:** both images pullable from SWR with tags `latest` and `<git sha>`.

## M2 — First deployment on CCE (dev)

- [ ] Node subnet has NAT gateway SNAT rule + EIP; `api.anthropic.com:443` reachable from a pod (OI-02)
- [ ] Claude API organization, key and billing owner confirmed; key stored in the Secret (OI-03)
- [ ] EVS disk type valid for the region; PVC bound (OI-04)
- [ ] `default-secret` present in `ai-agent` (OI-05)
- [ ] `kubectl apply -k k8s/`: backend, frontend and Postgres Running; UI reachable via port-forward
- [ ] First live session against the real cluster and the real Claude API (OI-08)

**Exit criteria:** a read-only question ("check my cluster") answered end to end in the UI.

## M3 — Validation with test scenarios

- [ ] Run all six scenarios in `k8s/test-scenarios/` through diagnose → approve → fix → verify (OI-09)
- [ ] Record per scenario: root cause found?, fix correct?, tool calls, tokens, cost, duration
- [ ] Integration tests for the read/write tools against a real API server (OI-21)
- [ ] Replace `Endpoints` with `EndpointSlice` in `check_service_endpoints` (OI-22)
- [ ] Tune prompt / effort level from the results (OI-16 cost data feeds this)

**Exit criteria:** ≥ 5 of 6 scenarios fixed correctly with no unsafe action attempted.

## M4 — Security hardening

- [ ] Authentication in front of the UI (e.g. oauth2-proxy / IAM SSO) (OI-11)
- [ ] Audit trail: who approved what, and which writes ran (OI-12)
- [ ] Namespace-scoped remediator RoleBindings instead of the ClusterRoleBinding (OI-10)
- [ ] NetworkPolicies enabled (verify CCE network model) and private ELB only
- [ ] Prompt-injection red-team using hostile log lines / annotations (OI-15)
- [ ] Move the API key to DEW/CSMS-backed secret management (OI-23)

**Exit criteria:** security review sign-off; no unauthenticated path to the UI.

## M5 — Observability, cost & operations

- [ ] Metrics scraped by AOM / Prometheus; dashboard for runs, tools, tokens, cost (OI-18)
- [ ] Logs shipped to LTS
- [ ] Per-session token budget and monthly spend alert (OI-16)
- [ ] Postgres backup (EVS snapshot / CBR) and session retention clean-up (OI-17)
- [ ] Recovery of sessions left "running" after a pod restart (OI-14)

**Exit criteria:** on-call can see health, spend and failures without kubectl.

## M6 — Production readiness & handover

- [ ] Decide on HA for the backend (queue/worker) or accept single replica (OI-13)
- [ ] Runbook (deploy, upgrade, rotate key, restore DB, disable writes in an incident)
- [ ] Verify pricing values in the ConfigMap (OI-20)
- [ ] Final HLD review; region label confirmed (OI-24)
- [ ] All open items closed or accepted as known limitations

**Exit criteria:** owner sign-off; `open-items.md` has no open item without a decision.
