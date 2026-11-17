"""Prometheus metrics exposed on /metrics."""

from prometheus_client import Counter, Gauge, Histogram

SESSIONS = Counter("agent_sessions_total", "Troubleshooting sessions started")
RUNS_ACTIVE = Gauge("agent_runs_active", "Agent loops currently running")
TOOL_CALLS = Counter("agent_tool_calls_total", "Tool calls", ["tool", "category", "status"])
APPROVALS = Counter("agent_plan_decisions_total", "Operator decisions on plans", ["decision"])
LLM_REQUESTS = Counter("agent_llm_requests_total", "Claude API requests", ["status"])
LLM_LATENCY = Histogram(
    "agent_llm_request_seconds",
    "Claude API request latency",
    buckets=(1, 2, 5, 10, 20, 40, 80, 160, 320),
)
TOKENS = Counter("agent_llm_tokens_total", "Tokens used", ["type"])
COST = Counter("agent_llm_cost_usd_total", "Estimated Claude API cost in USD")
