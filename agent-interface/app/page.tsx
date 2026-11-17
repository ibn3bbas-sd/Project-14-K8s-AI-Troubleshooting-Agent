"use client";

import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";

type Usage = {
  input_tokens: number;
  output_tokens: number;
  cache_read_input_tokens: number;
  cache_creation_input_tokens: number;
  requests: number;
  cost_usd: number;
};

type Issue = {
  id: string;
  title: string;
  severity: "low" | "medium" | "high" | "critical";
  resource: string;
  root_cause: string;
  evidence?: string;
  fix_steps: string[];
  risk: string;
};

type AgentEvent = {
  seq: number;
  type: "user" | "assistant" | "thinking" | "tool_call" | "tool_result" | "plan" | "decision" | "outcome" | "error" | "status";
  ts: string;
  text?: string;
  id?: string;
  name?: string;
  category?: string;
  input?: unknown;
  ok?: boolean;
  output?: string;
  summary?: string;
  issues?: Issue[];
  approved?: boolean;
  issue_ids?: string[];
  guidance?: string;
  resolved?: boolean;
  remaining?: string;
};

type Session = {
  id: string;
  title: string;
  status: "idle" | "running" | "awaiting_approval" | "error";
  phase: "diagnosis" | "remediation";
  busy: boolean;
  events: AgentEvent[];
  pending: { summary: string; issues: Issue[] } | null;
  usage: Usage;
  model: string;
  updated_at: string;
};

type SessionSummary = Pick<Session, "id" | "title" | "status" | "usage" | "updated_at">;

type Config = {
  model: string;
  effort: string;
  cluster_name: string;
  write_tools_enabled: boolean;
  protected_namespaces: string[];
};

const EXAMPLES = [
  "Pod web-service in default is stuck in Pending. Why?",
  "Find every unhealthy pod in the cluster and explain the root causes.",
  "My deployment api in namespace shop keeps restarting. Investigate.",
  "Service frontend in namespace shop returns no traffic. Check its endpoints.",
];

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api/${path}`, {
    ...init,
    headers: { "content-type": "application/json" },
    cache: "no-store",
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* not JSON */
    }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return res.status === 204 ? (undefined as T) : res.json();
}

function fmtTokens(n: number) {
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);
}

export default function Page() {
  const [config, setConfig] = useState<Config | null>(null);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [current, setCurrent] = useState<Session | null>(null);
  const [input, setInput] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  const refreshList = useCallback(async () => {
    try {
      setSessions(await api<SessionSummary[]>("sessions"));
    } catch (e) {
      setError(String(e));
    }
  }, []);

  useEffect(() => {
    api<Config>("config").then(setConfig).catch((e) => setError(String(e)));
    refreshList();
  }, [refreshList]);

  const active = current && (current.busy || current.status === "running");

  // Poll the open session while the agent works.
  useEffect(() => {
    if (!current || !active) return;
    const timer = setInterval(async () => {
      try {
        const s = await api<Session>(`sessions/${current.id}`);
        setCurrent(s);
        if (!s.busy && s.status !== "running") refreshList();
      } catch (e) {
        setError(String(e));
      }
    }, 1500);
    return () => clearInterval(timer);
  }, [current, active, refreshList]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [current?.events.length, current?.status]);

  async function act(fn: () => Promise<Session>) {
    setError(null);
    setSending(true);
    try {
      setCurrent(await fn());
      refreshList();
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setSending(false);
    }
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    const text = input.trim();
    if (!text) return;
    setInput("");
    if (current) {
      act(() => api<Session>(`sessions/${current.id}/message`, { method: "POST", body: JSON.stringify({ text }) }));
    } else {
      act(() => api<Session>("sessions", { method: "POST", body: JSON.stringify({ query: text }) }));
    }
  }

  async function open(id: string) {
    act(() => api<Session>(`sessions/${id}`));
  }

  async function remove(id: string) {
    setError(null);
    try {
      await api<void>(`sessions/${id}`, { method: "DELETE" });
      if (current?.id === id) setCurrent(null);
      refreshList();
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    }
  }

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="logo">⎈</span>
          <div>
            <div className="brand-title">Troubleshooting Agent</div>
            <div className="muted small">{config ? `${config.cluster_name} · ${config.model}` : "connecting…"}</div>
          </div>
        </div>
        <button className="new" onClick={() => setCurrent(null)}>
          + New session
        </button>
        <div className="list">
          {sessions.map((s) => (
            <div key={s.id} className={`item ${current?.id === s.id ? "selected" : ""}`} onClick={() => open(s.id)}>
              <div className="item-title">{s.title}</div>
              <div className="item-meta">
                <span className={`dot ${s.status}`} /> {s.status.replace("_", " ")} · ${s.usage?.cost_usd?.toFixed(3) ?? "0"}
                <button
                  className="link danger"
                  title="Delete session"
                  onClick={(e) => {
                    e.stopPropagation();
                    remove(s.id);
                  }}
                >
                  ✕
                </button>
              </div>
            </div>
          ))}
          {!sessions.length && <div className="muted small pad">No sessions yet.</div>}
        </div>
        {config && (
          <div className="safety small">
            <div>
              Write tools: <b>{config.write_tools_enabled ? "enabled after approval" : "disabled (read-only)"}</b>
            </div>
            <div className="muted">Protected: {config.protected_namespaces.join(", ")}</div>
          </div>
        )}
      </aside>

      <main className="main">
        <header className="topbar">
          <div className="title">{current ? current.title : "New troubleshooting session"}</div>
          {current && (
            <div className="usage" title="Claude API usage for this session">
              <span className={`badge ${current.status}`}>{current.busy ? "working…" : current.status.replace("_", " ")}</span>
              <span className={`badge phase-${current.phase}`}>{current.phase}</span>
              <span>in {fmtTokens(current.usage.input_tokens)}</span>
              <span>cache {fmtTokens(current.usage.cache_read_input_tokens)}</span>
              <span>out {fmtTokens(current.usage.output_tokens)}</span>
              <b>${current.usage.cost_usd.toFixed(4)}</b>
            </div>
          )}
        </header>

        <section className="feed">
          {!current && (
            <div className="empty">
              <h2>Describe a problem in your CCE cluster</h2>
              <p className="muted">
                The agent investigates with read-only tools, proposes a fix, and changes nothing until you approve.
              </p>
              <div className="examples">
                {EXAMPLES.map((ex) => (
                  <button key={ex} className="example" onClick={() => setInput(ex)}>
                    {ex}
                  </button>
                ))}
              </div>
            </div>
          )}
          {current && <Feed session={current} onAct={act} sending={sending} />}
          {current?.busy && (
            <div className="working">
              <span className="spinner" /> Claude is working…
            </div>
          )}
          <div ref={bottomRef} />
        </section>

        {error && (
          <div className="error-bar" onClick={() => setError(null)}>
            {error}
          </div>
        )}

        <form className="composer" onSubmit={submit}>
          <textarea
            value={input}
            placeholder={
              current?.status === "awaiting_approval"
                ? "Reply with guidance instead of approving…"
                : current
                  ? "Ask a follow-up…"
                  : "e.g. Why is pod web-service Pending?"
            }
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) submit(e);
            }}
            disabled={!!current?.busy || sending}
            rows={2}
          />
          <button type="submit" disabled={!input.trim() || !!current?.busy || sending}>
            Send
          </button>
        </form>
      </main>
    </div>
  );
}

function Feed({ session, onAct, sending }: { session: Session; onAct: (fn: () => Promise<Session>) => void; sending: boolean }) {
  const results = useMemo(() => {
    const map = new Map<string, AgentEvent>();
    session.events.forEach((e) => e.type === "tool_result" && e.id && map.set(e.id, e));
    return map;
  }, [session.events]);
  const lastPlanSeq = [...session.events].reverse().find((e) => e.type === "plan")?.seq;

  return (
    <>
      {session.events.map((ev) => {
        switch (ev.type) {
          case "user":
            return (
              <div key={ev.seq} className="msg user">
                {ev.text}
              </div>
            );
          case "assistant":
            return (
              <div key={ev.seq} className="msg assistant">
                {ev.text}
              </div>
            );
          case "thinking":
            return (
              <details key={ev.seq} className="thinking">
                <summary>Reasoning</summary>
                <div className="pre">{ev.text}</div>
              </details>
            );
          case "tool_call": {
            const res = ev.id ? results.get(ev.id) : undefined;
            if (ev.name === "propose_remediation" || ev.name === "report_outcome") return null;
            return (
              <details key={ev.seq} className={`tool ${ev.category}`}>
                <summary>
                  <span className={`cat ${ev.category}`}>{ev.category}</span> <code>{ev.name}</code>{" "}
                  <span className="muted small">{argsPreview(ev.input)}</span>
                  {res ? <span className={res.ok ? "ok" : "fail"}>{res.ok ? " ✓" : " ✕"}</span> : <span className="muted"> …</span>}
                </summary>
                <div className="pre small">{JSON.stringify(ev.input, null, 2)}</div>
                {res && <div className={`pre small ${res.ok ? "" : "fail-text"}`}>{res.output}</div>}
              </details>
            );
          }
          case "plan":
            return (
              <PlanCard
                key={ev.seq}
                event={ev}
                live={session.status === "awaiting_approval" && ev.seq === lastPlanSeq && !session.busy}
                sessionId={session.id}
                onAct={onAct}
                sending={sending}
              />
            );
          case "decision":
            return (
              <div key={ev.seq} className={`chip ${ev.approved ? "approved" : "rejected"}`}>
                {ev.approved ? `Approved: ${ev.issue_ids?.join(", ")}` : "Not approved"}
                {ev.guidance ? ` — “${ev.guidance}”` : ""}
              </div>
            );
          case "outcome":
            return (
              <div key={ev.seq} className={`outcome ${ev.resolved ? "resolved" : "unresolved"}`}>
                <b>{ev.resolved ? "✓ Resolved" : "✕ Not resolved"}</b>
                <div className="pre">{ev.summary}</div>
                {ev.remaining && <div className="muted">Remaining: {ev.remaining}</div>}
              </div>
            );
          case "error":
            return (
              <div key={ev.seq} className="err">
                {ev.text}
                {session.status === "error" && !session.busy && (
                  <button
                    className="small-btn"
                    onClick={() => onAct(() => api<Session>(`sessions/${session.id}/retry`, { method: "POST" }))}
                  >
                    Retry
                  </button>
                )}
              </div>
            );
          case "status":
            return (
              <div key={ev.seq} className="muted small center">
                {ev.text}
              </div>
            );
          default:
            return null;
        }
      })}
    </>
  );
}

function argsPreview(input: unknown) {
  if (!input || typeof input !== "object") return "";
  const parts = Object.entries(input as Record<string, unknown>)
    .filter(([, v]) => typeof v !== "object")
    .map(([k, v]) => `${k}=${String(v)}`);
  const s = parts.join(" ");
  return s.length > 90 ? s.slice(0, 90) + "…" : s;
}

function PlanCard({
  event,
  live,
  sessionId,
  onAct,
  sending,
}: {
  event: AgentEvent;
  live: boolean;
  sessionId: string;
  onAct: (fn: () => Promise<Session>) => void;
  sending: boolean;
}) {
  const issues = event.issues ?? [];
  const [selected, setSelected] = useState<string[]>(issues.map((i) => i.id));
  const [guidance, setGuidance] = useState("");

  function decide(approve: boolean) {
    onAct(() =>
      api<Session>(`sessions/${sessionId}/decision`, {
        method: "POST",
        body: JSON.stringify({ approve, issue_ids: selected, guidance }),
      }),
    );
  }

  return (
    <div className={`plan ${live ? "live" : ""}`}>
      <div className="plan-head">
        <b>Proposed remediation</b>
        {live && <span className="badge awaiting_approval">waiting for you</span>}
      </div>
      <p>{event.summary}</p>
      {issues.map((issue) => (
        <div key={issue.id} className="issue">
          <label className="issue-title">
            {live && (
              <input
                type="checkbox"
                checked={selected.includes(issue.id)}
                onChange={(e) =>
                  setSelected((cur) => (e.target.checked ? [...cur, issue.id] : cur.filter((x) => x !== issue.id)))
                }
              />
            )}
            <span className={`sev ${issue.severity}`}>{issue.severity}</span> {issue.title}
            <code className="small">{issue.resource}</code>
          </label>
          <div className="small">
            <b>Root cause:</b> {issue.root_cause}
          </div>
          {issue.evidence && (
            <div className="small muted">
              <b>Evidence:</b> {issue.evidence}
            </div>
          )}
          <ol className="small">
            {issue.fix_steps.map((s, i) => (
              <li key={i}>
                <code>{s}</code>
              </li>
            ))}
          </ol>
          <div className="small">
            <b>Risk:</b> {issue.risk}
          </div>
        </div>
      ))}
      {live && (
        <div className="plan-actions">
          <textarea
            placeholder="Optional guidance (e.g. 'label the node instead of changing the pod')"
            value={guidance}
            onChange={(e) => setGuidance(e.target.value)}
            rows={2}
          />
          <div className="row">
            <button className="approve" disabled={sending || !selected.length} onClick={() => decide(true)}>
              Approve {selected.length < issues.length ? `${selected.length} of ${issues.length}` : "plan"}
            </button>
            <button className="reject" disabled={sending} onClick={() => decide(false)}>
              {guidance.trim() ? "Revise with guidance" : "Reject"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
