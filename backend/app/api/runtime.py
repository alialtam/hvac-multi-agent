"""Agent runtime for the API. Runs the Supervisor graph in a background thread and keeps the
incident, trace, activity and ticket stores up to date. No FastAPI or MQTT in here."""
import logging
import queue
import threading
from collections import defaultdict
from datetime import datetime, timezone
from functools import partial
from typing import Callable, Optional

from app.agents.diagnosis import diagnose, strip_label
from app.agents.schemas import Diagnosis, Recommendation
from app.agents.supervisor import (_cfg, build_graph, get_checkpointer, get_incident,
                                   resume_incident, run_incident)
from app.agents.trace import timeline_entry, to_trace_lines

log = logging.getLogger("runtime")
LIGHT_SKIP = ("anomaly_event", "trace", "ticket_id", "triage")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class AgentRuntime:
    def __init__(self, incidents: dict, traces: dict, activity: list, tickets: list,
                 publish: Callable[[str, dict], None], graph=None, store=None, pipeline=None):
        self.incidents, self.traces, self.activity, self.tickets = incidents, traces, activity, tickets
        self.publish = publish
        self.graph = graph or build_graph(diagnose_fn=partial(diagnose, store=store, pipeline=pipeline),
                                          checkpointer=get_checkpointer())
        self.triage_steps: dict[str, list] = {}
        self.sup_trace: dict[str, list] = {}
        self.seen: dict[str, int] = defaultdict(int)
        self.locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
        self.pending: set[str] = set()
        self.q: queue.Queue = queue.Queue()
        self.thread: Optional[threading.Thread] = None

    # ---- worker thread ----
    def start(self):
        if self.thread is None:
            self.thread = threading.Thread(target=self._loop, daemon=True, name="agent-runtime")
            self.thread.start()

    def stop(self):
        if self.thread:
            self.q.put(None)
            self.thread.join(timeout=5)
            self.thread = None

    def wait_idle(self):
        self.q.join()

    def _loop(self):
        while True:
            job = self.q.get()
            try:
                if job is None:
                    return
                self._do(*job)
            finally:
                self.q.task_done()

    def _do(self, kind: str, inc_id: str, data: dict):
        upd = lambda s: self._sync(inc_id, s)  # noqa: E731
        try:
            with self.locks[inc_id]:
                if kind == "run":
                    run_incident(data, self.graph, thread_id=inc_id, on_update=upd)
                else:
                    resume_incident(self.graph, inc_id, "reject", data["operator"],
                                    reason=data["reason"], on_update=upd)
        except Exception as e:
            log.exception("agent job failed for %s", inc_id)
            self._note(inc_id, "supervisor", f"Agent run failed: {e}")
            s = get_incident(self.graph, inc_id)
            if s:
                self._sync(inc_id, s)
        finally:
            self.pending.discard(inc_id)

    # ---- called by the API ----
    def add_triage(self, inc_id: str, steps: list):
        self.triage_steps[inc_id] = list(steps)
        self._rebuild_trace(inc_id)

    def submit(self, inc_id: str, event: dict):
        """Queue the event for the graph. Returns at once; the incident updates as agents finish."""
        self.q.put(("run", inc_id, strip_label(event)))

    def approve(self, inc_id: str, operator: str, note: Optional[str] = None) -> dict:
        with self.locks[inc_id]:
            if inc_id in self.pending or not self._waiting(inc_id):
                raise ValueError("this incident is not waiting for approval")
            if not (get_incident(self.graph, inc_id) or {}).get("recommendation"):
                raise ValueError("there is no recommendation to approve; reject it with a reason to replan")
            s = resume_incident(self.graph, inc_id, "approve", operator, note=note,
                                on_update=lambda x: self._sync(inc_id, x))
        if s.get("state") == "approved":
            self._ticket(inc_id, s)
        return self.incidents[inc_id]

    def reject(self, inc_id: str, operator: str, reason: str) -> dict:
        """Validates now, replans in the background (the replan calls the LLM)."""
        if not (reason or "").strip():
            raise ValueError("a rejection needs a reason")
        if inc_id in self.pending or not self._waiting(inc_id):
            raise ValueError("this incident is not waiting for approval")
        self.pending.add(inc_id)
        inc = self.incidents[inc_id]
        inc["state"] = "investigating"
        self._publish_incident(inc)
        self.q.put(("reject", inc_id, {"operator": operator, "reason": reason}))
        return inc

    # ---- internals ----
    def _waiting(self, inc_id: str) -> bool:
        return "approval_wait" in (self.graph.get_state(_cfg(inc_id)).next or ())

    def _rebuild_trace(self, inc_id: str):
        self.traces[inc_id] = self.triage_steps.get(inc_id, []) + \
            to_trace_lines(inc_id, self.sup_trace.get(inc_id, []))

    def _publish_incident(self, inc: dict):
        self.publish("incident", {k: v for k, v in inc.items() if k not in LIGHT_SKIP})

    def _step(self, inc_id: str, ts: str, agent: str, message: str):
        inc = self.incidents[inc_id]
        inc["trace"].append({"ts": ts, "agent": agent, "message": message})
        step = {"ts": ts, "agent": agent, "incident_id": inc_id,
                "device_id": inc.get("device_id"), "message": message}
        self.activity.insert(0, step)
        self.publish("agent_step", step)

    def _note(self, inc_id: str, agent: str, message: str):
        if inc_id in self.incidents:
            self._step(inc_id, _now(), agent, message)

    def _fields(self, s: dict) -> dict:
        """Graph state -> incident fields. Check the names against contracts/api_examples/incident_detail.json."""
        d, e = s.get("diagnosis"), s.get("energy")
        out = {"state": s.get("state", "investigating"), "diagnosis": d, "energy": e,
               "critique": s.get("critique"), "recommendation": s.get("recommendation"),
               "revisions": s.get("revisions", 0), "replans": s.get("replans", 0)}
        if d:
            out["confidence"] = d["confidence"]
        if e:
            out["energy_cost_per_day"] = e["cost_per_day"]
        used = [t["provider"] for t in s.get("trace", []) if t.get("provider")]
        if used:
            out["llm_provider"] = used[-1]
        return out

    def _sync(self, inc_id: str, s: dict):
        inc = self.incidents.get(inc_id)
        if inc is None:
            return
        trace = s.get("trace", [])
        self.sup_trace[inc_id] = trace
        self._rebuild_trace(inc_id)
        for t in trace[self.seen[inc_id]:]:
            m = timeline_entry(t)
            self._step(inc_id, t["ts"], m["agent"], m["message"])
        self.seen[inc_id] = len(trace)
        inc.update(self._fields(s))
        inc["updated_at"] = _now()
        self._publish_incident(inc)

    def _ticket(self, inc_id: str, s: dict):
        from app.agents.maintenance import draft_ticket
        diag = Diagnosis.model_validate(s["diagnosis"])
        rec = Recommendation.model_validate(s["recommendation"])
        t = {"id": f"TCK-{len(self.tickets) + 1:04d}", "incident_id": inc_id, "status": "open",
             "created_at": _now(), **draft_ticket(s["event"], rec, diag)}
        self.tickets.append(t)
        inc = self.incidents[inc_id]
        inc["ticket_id"] = t["id"]
        self._note(inc_id, "maintenance", f"Ticket {t['id']} created ({t['priority']} priority)")
        self._publish_incident(inc)