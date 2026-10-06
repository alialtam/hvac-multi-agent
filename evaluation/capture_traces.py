"""Capture real execution traces of the agentic behaviours.

    python evaluation/capture_traces.py                                    # all scenarios, rules provider
    python evaluation/capture_traces.py --provider ollama --only routing   # real local LLM
    python evaluation/capture_traces.py --only conflict                    # names containing "conflict"

Every scenario runs the REAL graph, agents, tools and trace code on a real sample event from
contracts/sample_events. In some scenarios the Diagnosis agent's LLM *answers* are scripted with
FakeProvider, to reproduce a behaviour on demand. That is written in each file under "setup", and
those steps show llm_provider "fake". The files are written by this script: never edit them by hand,
run the script again instead.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.agents.diagnosis import diagnose, strip_label  # noqa: E402
from app.agents.supervisor import build_graph, resume_incident, run_incident  # noqa: E402
from app.agents.trace import to_trace_lines  # noqa: E402
from app.knowledge.rules import CAUSE_TEXT, diagnose_rules  # noqa: E402
from app.llm import FakeProvider, ProviderError, llm  # noqa: E402

SAMPLES = ROOT / "contracts" / "sample_events"
OP = "capture_operator"
REASON = ("The compressor was serviced yesterday. The room temperature reading has been "
          "exactly the same for hours.")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class RecordingFake(FakeProvider):
    """FakeProvider that also keeps every prompt it received."""

    def __init__(self, responses):
        super().__init__(responses)
        self.prompts: list[str] = []

    def call(self, prompt: str):
        self.prompts.append(prompt)
        return super().call(prompt)


def scripted(fake):
    """A diagnose function whose LLM answers come from `fake`. Tools, prompt and agent code are real."""
    def fn(event, more_evidence=False, critique=None, **kw):
        saved = (llm.providers, llm.provider)
        llm.providers, llm.provider = {"fake": fake}, "fake"
        try:
            return diagnose(event, more_evidence=more_evidence, critique=critique)
        finally:
            llm.providers, llm.provider = saved
    return fn


def dx(cause, conf, why):
    return {"cause": cause, "cause_text": CAUSE_TEXT[cause], "confidence": conf,
            "evidence": [why], "sources": []}


def load_samples():
    out = []
    for f in sorted(SAMPLES.glob("*.json")):
        try:
            e = json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if isinstance(e, dict) and "signals" in e and "window" in e:
            out.append((f.name, strip_label(e)))
    return out


def pick(samples, cause):
    for name, e in samples:
        if diagnose_rules(e).cause == cause:
            return name, e
    raise SystemExit(f"No sample event in {SAMPLES} is diagnosed as {cause} by the rules "
                     f"({len(samples)} sample events found).")


def senders(sup):
    return [l["from"] for l in sup if l["type"] == "result"]


def kinds(sup):
    return [l["type"] for l in sup]


def text(l):
    return l["content"].get("text", "")


def build(samples):
    comp_file, comp = pick(samples, "compressor_failure")
    stuck_file, stuck = pick(samples, "sensor_stuck")
    after_file, after = pick(samples, "after_hours_waste")
    wrong = dx("filter_blockage", 0.8, "Scripted first answer: filter blockage")
    right = dx("compressor_failure", 0.9, "Scripted second answer: compressor failure")

    def chk_compressor(c):
        s = c["sup"]
        return {"diagnosis, energy and maintenance ran in this order":
                senders(s) == ["diagnosis", "energy", "maintenance"],
                "every routing decision has a reason": all(text(l) for l in s if l["type"] == "route"),
                "graph paused for the operator": "escalate_to_human" in kinds(s),
                "operator approval was recorded": "approval" in kinds(s)}

    def chk_stuck(c):
        return {"energy was skipped for a frozen sensor": senders(c["sup"]) == ["diagnosis", "maintenance"]}

    def chk_after(c):
        r = senders(c["sup"])
        return {"energy ran first on an after-hours case": r[:1] == ["energy"],
                "diagnosis and maintenance followed": "diagnosis" in r and r[-1:] == ["maintenance"]}

    def chk_resolved(c):
        s, st = c["sup"], c["state"]
        return {"energy sent a critique to diagnosis":
                any(l["type"] == "critique" and l["from"] == "energy" and l["to"] == "diagnosis" for l in s),
                "diagnosis was revised after the critique":
                any("revision after critique" in text(l) for l in s if l["from"] == "diagnosis"),
                "final diagnosis is compressor_failure and energy agrees":
                (st.get("diagnosis") or {}).get("cause") == "compressor_failure"
                and (st.get("critique") or {}).get("agrees") is True,
                "one revision, no escalation": st.get("revisions") == 1 and not st.get("escalated"),
                "a recommendation was produced": st.get("recommendation") is not None}

    def chk_escalated(c):
        s, st = c["sup"], c["state"]
        return {"two revisions were tried": st.get("revisions") == 2,
                "both views went to the operator":
                any(l["type"] == "escalate_to_human" and "still disagree" in text(l) for l in s),
                "maintenance did not draft a ticket":
                "maintenance" not in senders(s) and st.get("recommendation") is None}

    def chk_evidence(c):
        s, st = c["sup"], c["state"]
        return {"maintenance refused and asked diagnosis for more evidence":
                any(l["type"] == "request_more_evidence" and l["from"] == "maintenance" and l["to"] == "diagnosis"
                    for l in s),
                "diagnosis ran again with extra evidence":
                any("extra evidence" in text(l) for l in s if l["from"] == "diagnosis"),
                "then a recommendation was produced": st.get("recommendation") is not None and not st.get("escalated")}

    def chk_replan(c):
        s, st = c["sup"], c["state"]
        return {"the operator's rejection became a replan": "replan" in kinds(s),
                "the operator's reason reached the diagnosis prompt": any(REASON in p for p in c["fake"].prompts),
                "diagnosis step is marked as replanned":
                any("replanned after operator rejection" in text(l) for l in s if l["from"] == "diagnosis"),
                "diagnosis changed its answer after the reason": (st.get("diagnosis") or {}).get("cause") == "sensor_stuck",
                "the old energy estimate was dropped": st.get("energy") is None,
                "the operator approved the new recommendation": st.get("state") == "approved"}

    def chk_failure(c):
        s, st = c["sup"], c["state"]
        failed = [r for r in c["calls"] if r.get("agent") == "diagnosis" and r.get("ok") is False]
        return {"bad JSON and a timeout were logged as failed attempts": len(failed) >= 2,
                "agent switched to rules and said so in the trace":
                any(l["from"] == "diagnosis" and l["type"] == "result" and l["llm_provider"] == "rules"
                    and "fake" in l["content"].get("fallbacks", []) for l in s),
                "the run finished without crashing": st.get("state") == "awaiting_approval"}

    def chk_low(c):
        s, st = c["sup"], c["state"]
        return {"maintenance refused and asked for more evidence": "request_more_evidence" in kinds(s),
                "diagnosis ran again with extra evidence":
                any("extra evidence" in text(l) for l in s if l["from"] == "diagnosis"),
                "still too uncertain, so it went to the operator":
                "refused" in kinds(s) and "escalate_to_human" in kinds(s),
                "no ticket was drafted": st.get("recommendation") is None}

    sc = [
        dict(name="routing_compressor", file=comp_file, event=comp,
             behaviours=["Dynamic routing", "Triage before escalation"],
             setup="Real compressor sample event, nothing scripted. Compressor failure goes through all three agents, then the operator approves.",
             operator=[("approve", OP, "Approved in the capture run")], checks=chk_compressor),
        dict(name="routing_sensor_stuck", file=stuck_file, event=stuck,
             behaviours=["Dynamic routing"],
             setup="Real frozen-sensor sample event, nothing scripted. Energy is skipped because a frozen sensor has no energy cost.",
             checks=chk_stuck),
        dict(name="routing_after_hours", file=after_file, event=after,
             behaviours=["Dynamic routing"],
             setup="Real after-hours sample event, nothing scripted. Energy runs first.",
             checks=chk_after),
        dict(name="conflict_resolved", file=comp_file, event=comp,
             behaviours=["Conflict resolution"],
             setup="Real compressor event. Diagnosis LLM answers are scripted: first a wrong filter_blockage answer, then compressor_failure. Energy numbers, critique and the revision loop are real.",
             responses=[wrong, right], checks=chk_resolved),
        dict(name="conflict_escalated", file=comp_file, event=comp,
             behaviours=["Conflict resolution"],
             setup="Real compressor event. Diagnosis LLM answers are scripted to stay wrong in all three rounds. After two revisions both views go to the operator.",
             responses=[wrong, wrong, wrong], checks=chk_escalated),
        dict(name="evidence_requested", file=comp_file, event=comp,
             behaviours=["Asking for more evidence"],
             setup="Real compressor event. Diagnosis LLM answers are scripted: confidence 0.45, then 0.85 when it is called again with more evidence. Maintenance refusal is real.",
             responses=[dx("compressor_failure", 0.45, "Scripted low-confidence answer"),
                        dx("compressor_failure", 0.85, "Scripted answer after longer history and peer data")],
             checks=chk_evidence),
        dict(name="replan_after_rejection", file=comp_file, event=comp,
             behaviours=["Replanning after rejection"],
             setup="Real compressor event. Diagnosis LLM answers are scripted: compressor_failure, then sensor_stuck after the operator's reason. The pause, rejection, replan and approval are real.",
             responses=[dx("compressor_failure", 0.9, "Scripted first answer: compressor failure"),
                        dx("sensor_stuck", 0.85, "Scripted answer that takes the operator's reason into account")],
             operator=[("reject", OP, REASON), ("approve", OP, "Approved after the replan")], checks=chk_replan),
        dict(name="failure_fallback", file=comp_file, event=comp,
             behaviours=["Failure handling"],
             setup="Real compressor event. The Diagnosis LLM is scripted to fail: invalid JSON, then a timeout. The retry, the switch to rules and the trace entry are real.",
             responses=["this is not json at all", ProviderError("timeout")], checks=chk_failure),
    ]
    low = next(((n, e) for n, e in samples if diagnose_rules(e).confidence < 0.6), None)
    if low:
        sc.append(dict(name="evidence_refused_real", file=low[0], event=low[1],
                       behaviours=["Asking for more evidence"],
                       setup="Real sample event that the rules cannot diagnose with confidence, nothing scripted. Maintenance refuses, Diagnosis retries, the case goes to the operator.",
                       checks=chk_low))
    else:
        print("note: no sample event has a rules confidence below 0.6, skipping evidence_refused_real")
    return sc


def triage_part(event, provider):
    """Anomaly triage (Ali's agent) on the event. Peer units are assumed normal."""
    try:
        from app.detection.triage import triage_event
        ids = [f"AHU-{i}" for i in range(1, 7)]
        report = triage_event(event, provider=provider,
                              peers=lambda e: [{"device_id": d, "state": "ok"} for d in ids if d != e["device_id"]],
                              history=lambda e: [])
    except Exception as exc:
        return [], {"skipped": f"{type(exc).__name__}: {exc}"}
    return report.get("steps", []), {k: v for k, v in report.items() if k != "steps"}


def git_commit():
    try:
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                           capture_output=True, text=True, timeout=10)
        return r.stdout.strip() or None
    except Exception:
        return None


def run(sc, provider, commit):
    inc_id = f"cap_{sc['name']}"
    calls: list[dict] = []
    llm.log_hook = lambda r: calls.append({"ts": now(), **r})
    fake = RecordingFake(sc["responses"]) if sc.get("responses") is not None else None
    graph = build_graph(diagnose_fn=scripted(fake) if fake else diagnose)
    event = copy.deepcopy(sc["event"])
    tri_steps, tri = triage_part(event, provider)
    for s in tri_steps:
        s["incident_id"] = inc_id
    graph_event = event if "skipped" in tri else {**event, "triage": tri}
    state = run_incident(graph_event, graph, thread_id=inc_id)
    for decision, who, why in sc.get("operator", []):
        state = resume_incident(graph, inc_id, decision, who,
                                reason=why if decision == "reject" else None,
                                note=why if decision == "approve" else None)
    sup = to_trace_lines(inc_id, state["trace"])
    trace = tri_steps + sup
    checks = sc["checks"]({"sup": sup, "state": state, "fake": fake, "calls": calls})
    d = state.get("diagnosis") or {}
    return {
        "scenario": sc["name"], "behaviours": sc["behaviours"], "setup": sc["setup"],
        "provider": provider, "scripted_diagnosis_llm": fake is not None,
        "sample_event_file": sc["file"], "event_id": event.get("event_id"),
        "captured_at": now(), "code_commit": commit, "triage": tri,
        "result": {"state": state.get("state"), "diagnosis": d.get("cause"),
                   "confidence": d.get("confidence"), "revisions": state.get("revisions"),
                   "replans": state.get("replans"), "escalated": state.get("escalated"),
                   "has_recommendation": state.get("recommendation") is not None},
        "checks": checks,
        "trace_sha256": hashlib.sha256(json.dumps(trace, sort_keys=True, default=str).encode()).hexdigest()[:16],
        "trace": trace, "llm_calls": calls,
    }


def write_summary(out_dir, rows, provider, commit):
    lines = ["# Captured execution traces", "",
             f"Generated by `evaluation/capture_traces.py` (provider: `{provider}`, code commit: `{commit}`).",
             "Do not edit these files by hand. Run the script again to regenerate them.", "",
             "Each file holds the trace in the agreed format (`from`, `to`, `type`, `content`, `llm_provider`, "
             "`duration_ms`), the raw LLM call log, and the checks that prove the behaviour is in the trace. "
             "Steps with `llm_provider: fake` use a scripted Diagnosis answer; see `setup` in each file.", "",
             "| Scenario | Behaviours shown | How it was run | Checks | Trace lines |",
             "| --- | --- | --- | --- | --- |"]
    for sc, doc, bad in rows:
        lines.append(f"| `{sc['name']}` | {', '.join(sc['behaviours'])} | {sc['setup']} | "
                     f"{'all passed' if not bad else 'FAILED: ' + '; '.join(bad)} | {len(doc['trace'])} |")
    (out_dir / "SUMMARY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default="rules", choices=["rules", "ollama", "openai"])
    ap.add_argument("--only", default="", help="run only scenarios whose name contains this text")
    args = ap.parse_args()
    if args.provider != "rules":
        try:
            from dotenv import load_dotenv
            load_dotenv(ROOT / ".env")
        except ImportError:
            pass
    llm.set_provider(args.provider)
    samples = load_samples()
    if not samples:
        raise SystemExit(f"No sample events found in {SAMPLES}")
    out_dir = ROOT / "docs" / "traces" / args.provider
    out_dir.mkdir(parents=True, exist_ok=True)
    commit = git_commit()
    rows, failed = [], 0
    for sc in [s for s in build(samples) if args.only in s["name"]]:
        doc = run(sc, args.provider, commit)
        (out_dir / f"{sc['name']}.json").write_text(
            json.dumps(doc, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        bad = [k for k, v in doc["checks"].items() if not v]
        failed += len(bad)
        print(f"[{'PASS' if not bad else 'FAIL'}] {sc['name']}  ({len(doc['trace'])} trace lines)")
        for k, v in doc["checks"].items():
            print(f"      {'ok  ' if v else 'FAIL'} {k}")
        rows.append((sc, doc, bad))
    write_summary(out_dir, rows, args.provider, commit)
    llm.log_hook = None
    print(f"\nSaved {len(rows)} traces in {out_dir}")
    if failed and args.provider == "rules":
        sys.exit(1)


if __name__ == "__main__":
    main()