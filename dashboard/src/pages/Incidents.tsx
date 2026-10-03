import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../lib/api";
import { AGENTS, PROVIDER_LABEL, RULES, SIGNALS, dateTime, money, num, pct, time } from "../lib/format";
import { usePoll } from "../lib/live";
import type { IncidentDetail, Triage } from "../lib/types";
import { AGENT_COLOR } from "../components/ActivityFeed";
import { ErrorNote, Loading, Meter, Panel, SeverityTag, StateText } from "../components/ui";

export default function Incidents() {
  const { id } = useParams();
  const list = usePoll(api.incidents, 5000);
  const navigate = useNavigate();
  const items = list.data ?? [];
  const selected = id ?? items[0]?.id;

  return (
    <div className="mx-auto max-w-[1400px]">
      <h1 className="font-gauge text-[30px] font-semibold">Incidents</h1>
      <p className="mt-1 text-[15px] text-ink-2">Every confirmed anomaly becomes an incident. The agents investigate it, then wait for a person to decide.</p>
      {list.error && <div className="mt-4"><ErrorNote message={list.error} /></div>}

      <div className="mt-6 grid gap-6 lg:grid-cols-[340px_minmax(0,1fr)]">
        <Panel title={`${items.length} incident${items.length === 1 ? "" : "s"}`} className="self-start">
          {!list.data ? <Loading /> : items.length === 0 ? (
            <p className="px-4 py-5 text-[14px] text-ink-2">No incidents yet. Inject a fault in the simulator to see the agents work.</p>
          ) : (
            <ul className="divide-y divide-line">
              {items.map((i) => (
                <li key={i.id}>
                  <button
                    onClick={() => navigate(`/incidents/${i.id}`)}
                    aria-current={i.id === selected}
                    className={`block w-full px-4 py-3 text-left hover:bg-paper ${i.id === selected ? "bg-chill-soft/60 shadow-[inset_3px_0_0_var(--color-chill)]" : ""}`}
                  >
                    <div className="flex items-center gap-2">
                      <SeverityTag severity={i.severity} />
                      <span className="text-[13px] text-ink-2">{i.device_id}, {dateTime(i.created_at)}</span>
                    </div>
                    <p className="mt-1 font-medium leading-snug">{i.title}</p>
                    <StateText state={i.state} />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Panel>

        {selected ? <IncidentView id={selected} /> : null}
      </div>
    </div>
  );
}

function IncidentView({ id }: { id: string }) {
  const { data: inc, error, refresh } = usePoll(() => api.incident(id), 4000, id);
  if (error) return <ErrorNote message={error} />;
  if (!inc) return <Loading label="Loading incident" />;
  const ev = inc.anomaly_event;

  return (
    <article className="flex min-w-0 flex-col gap-5">
      <header className="rounded-lg border border-line bg-panel px-5 py-4">
        <div className="flex flex-wrap items-center gap-3">
          <SeverityTag severity={inc.severity} />
          <StateText state={inc.state} />
          <span className="text-[13px] text-ink-2">
            {inc.zone.replace("-", " ")}, <Link className="text-chill hover:underline" to={`/devices/${inc.device_id}`}>{inc.device_id}</Link>
          </span>
        </div>
        <h2 className="mt-2 font-gauge text-[28px] font-semibold leading-tight">{inc.title}</h2>
        <p className="mt-1 text-[14px] text-ink-2">
          Abnormal since {time(ev.ts_start)}, confirmed at {time(ev.ts_detected)}. Reasoning by {PROVIDER_LABEL[inc.llm_provider] ?? inc.llm_provider}.
        </p>
      </header>

      <Decision inc={inc} onDone={refresh} />

      <TriagePanel triage={inc.triage} />

      <div className="grid gap-5 xl:grid-cols-2">
        <Panel title="What the sensors show">
          {ev.rule_hits.length > 0 && (
            <p className="border-b border-line px-4 py-2.5 text-[14px]">
              <span className="text-ink-2">Rules triggered: </span>
              {ev.rule_hits.map((r) => RULES[r] ?? r).join(", ")}
            </p>
          )}
          {ev.signals.length ? (
            <table className="w-full text-[14px]">
              <thead>
                <tr className="text-left text-[13px] text-ink-2">
                  <th className="px-4 py-2 font-medium">Sensor</th>
                  <th className="px-2 py-2 text-right font-medium">Now</th>
                  <th className="px-2 py-2 text-right font-medium">Normal</th>
                  <th className="px-4 py-2 text-right font-medium">How unusual</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {ev.signals.map((s) => {
                  const m = SIGNALS[s.key] ?? { label: s.key, unit: "", digits: 1 };
                  return (
                    <tr key={s.key}>
                      <td className="px-4 py-2">{m.label}</td>
                      <td className="whitespace-nowrap px-2 py-2 text-right font-gauge text-[15px] font-semibold">{num(s.value, m.digits)} {m.unit}</td>
                      <td className="whitespace-nowrap px-2 py-2 text-right text-ink-2">{s.baseline === null ? "–" : `${num(s.baseline, m.digits)} ${m.unit}`}</td>
                      <td className="px-4 py-2 text-right"><Deviation z={s.z} /></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          ) : <p className="px-4 py-4 text-[14px] text-ink-2">No sensor detail for this incident.</p>}
          <p className="border-t border-line px-4 py-2.5 text-[12.5px] text-ink-3">
            Detected by {ev.method === "combined" ? "z-score and Isolation Forest together" : ev.method === "isolation_forest" ? "Isolation Forest" : ev.method === "zscore" ? "z-score" : "the monitoring rules"}, anomaly score {num(ev.score, 2)}.
          </p>
        </Panel>

        <Panel title="Likely cause">
          {inc.diagnosis ? (
            <div className="px-4 py-4">
              <div className="flex flex-wrap items-baseline justify-between gap-3">
                <p className="font-gauge text-[22px] font-semibold leading-tight">{inc.diagnosis.cause_text}</p>
                <Meter value={inc.diagnosis.confidence} />
              </div>
              <ul className="mt-3 space-y-1.5 text-[14.5px]">
                {inc.diagnosis.evidence.map((e) => (
                  <li key={e} className="flex gap-2"><span className="mt-[9px] h-1 w-1 shrink-0 rounded-full bg-ink-2" aria-hidden />{e}</li>
                ))}
              </ul>
              {inc.diagnosis.sources.length > 0 && (
                <p className="mt-3 text-[13px] text-ink-2">Based on: {inc.diagnosis.sources.join("; ")}</p>
              )}
            </div>
          ) : <p className="px-4 py-4 text-[14px] text-ink-2">The Diagnosis agent is still working.</p>}
        </Panel>

        <Panel title="Recommended action">
          {inc.recommendation ? (
            <div className="px-4 py-4">
              <p className="text-[16px] font-semibold">{inc.recommendation.action}</p>
              <p className="mt-1 text-[13.5px] text-ink-2">
                {inc.recommendation.assign_to}, about {inc.recommendation.estimated_downtime_min} minutes of downtime
              </p>
              <ol className="mt-3 space-y-1.5 text-[14.5px]">
                {inc.recommendation.checklist.map((c, i) => (
                  <li key={c} className="flex gap-3">
                    <span className="w-5 shrink-0 text-right font-gauge font-semibold text-ink-2">{i + 1}</span>{c}
                  </li>
                ))}
              </ol>
            </div>
          ) : <p className="px-4 py-4 text-[14px] text-ink-2">The Maintenance agent has not drafted a ticket yet.</p>}
        </Panel>

        <Panel title="Cost of waiting">
          {inc.energy ? (
            <div className="px-4 py-4">
              <p className="font-gauge text-[34px] font-semibold leading-none">
                {money(inc.energy.cost_per_day, inc.energy.currency)}<span className="text-[16px] font-medium text-ink-2"> per day</span>
              </p>
              <p className="mt-1 text-[14px] text-ink-2">{num(inc.energy.extra_kwh_per_day)} kWh of extra electricity per day</p>
              <p className="mt-3 text-[14.5px]">{inc.energy.explanation}</p>
            </div>
          ) : <p className="px-4 py-4 text-[14px] text-ink-2">No energy estimate for this incident.</p>}
        </Panel>
      </div>

      <Panel title="How the agents handled it">
        <ol className="px-4 py-3">
          {inc.trace.map((t, i) => (
            <li key={`${t.ts}-${i}`} className="relative flex gap-3 pb-3 last:pb-0">
              {i < inc.trace.length - 1 && <span className="absolute left-[60px] top-4 h-full w-px bg-line" aria-hidden />}
              <span className="w-11 shrink-0 font-gauge text-[14px] text-ink-2">{time(t.ts)}</span>
              <span className="relative mt-[6px] h-2.5 w-2.5 shrink-0 rounded-full ring-2 ring-panel" style={{ background: AGENT_COLOR[t.agent] ?? "#8a9794" }} aria-hidden />
              <p className="text-[14.5px] leading-snug"><span className="font-semibold">{AGENTS[t.agent] ?? t.agent}.</span> {t.message}</p>
            </li>
          ))}
        </ol>
      </Panel>
    </article>
  );
}

const VERDICT: Record<Triage["verdict"], { word: string; color: string }> = {
  equipment_fault: { word: "Equipment fault", color: "text-alarm" },
  sensor_fault: { word: "Sensor fault", color: "text-[#8a5a05]" },
  operational_waste: { word: "Energy waste", color: "text-[#8a5a05]" },
  building_wide: { word: "Building-wide change", color: "text-ink-2" },
  false_alarm: { word: "Probably a false alarm", color: "text-ink-2" },
  unclear: { word: "Unclear", color: "text-ink-2" },
};
const NEXT_WORD: Record<Triage["recommend_next"], string> = {
  diagnosis: "Diagnosis agent", energy: "Energy agent", maintenance: "Maintenance agent", human_review: "a person",
};

function TriagePanel({ triage: t }: { triage?: Triage }) {
  return (
    <Panel title="First look" action={<span className="text-[12.5px] text-ink-3">Anomaly Detection agent</span>}>
      {!t ? (
        <p className="px-4 py-4 text-[14px] text-ink-2">Checking the evidence…</p>
      ) : (
        <div className="px-4 py-4">
          <div className="flex flex-wrap items-baseline justify-between gap-3">
            <p className={`font-gauge text-[22px] font-semibold leading-tight ${VERDICT[t.verdict].color}`}>
              {VERDICT[t.verdict].word}
              <span className="ml-2 text-[15px] font-medium text-ink-2">area: {t.suspected_area}</span>
            </p>
            <Meter value={t.confidence} />
          </div>
          <p className="mt-2 text-[15px]">{t.summary}</p>
          <ul className="mt-3 space-y-1.5 text-[14px]">
            {t.key_evidence.map((e) => (
              <li key={e} className="flex gap-2"><span className="mt-[9px] h-1 w-1 shrink-0 rounded-full bg-ink-2" aria-hidden />{e}</li>
            ))}
          </ul>
          <p className="mt-3 text-[13px] text-ink-2">
            Handed to the {NEXT_WORD[t.recommend_next]}. Checked {t.tools_used.length ? t.tools_used.map((x) => x.replace(/_/g, " ")).join(", ") : "nothing"}
            {" "}with {PROVIDER_LABEL[t.provider] ?? t.provider} in {num(t.duration_ms / 1000, 1)} s.
            {t.provider !== "rules" && (t.agrees_with_rules ? " The rule-based check agrees." : ` The rule-based check said "${t.rules_verdict.replace(/_/g, " ")}".`)}
          </p>
        </div>
      )}
    </Panel>
  );
}

function Deviation({ z }: { z: number }) {
  const a = Math.abs(z);
  const word = a >= 6 ? "Extreme" : a >= 3 ? "Strong" : a >= 2 ? "Noticeable" : "Small";
  const color = a >= 3 ? "text-alarm" : a >= 2 ? "text-[#8a5a05]" : "text-ink-2";
  return <span className={`text-[13.5px] font-medium ${color}`}>{word} <span className="text-ink-3">({z > 0 ? "+" : ""}{num(z)}σ)</span></span>;
}

function Decision({ inc, onDone }: { inc: IncidentDetail; onDone: () => void }) {
  const [operator, setOperator] = useState("Facility manager");
  const [mode, setMode] = useState<"idle" | "rejecting">("idle");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (inc.state === "approved") {
    return (
      <div className="rounded-lg border border-chill/40 bg-chill-soft px-5 py-4">
        <p className="font-semibold">Approved{inc.approved_by ? ` by ${inc.approved_by}` : ""}. Ticket {inc.ticket_id} was created.</p>
        <Link to="/tickets" className="text-[14px] text-chill hover:underline">See the ticket</Link>
      </div>
    );
  }
  if (inc.state === "rejected") {
    return (
      <div className="rounded-lg border border-line bg-panel px-5 py-4">
        <p className="font-semibold">Rejected{inc.rejected_by ? ` by ${inc.rejected_by}` : ""}.</p>
        {inc.reason && <p className="text-[14px] text-ink-2">Reason: {inc.reason}</p>}
      </div>
    );
  }
  if (inc.state !== "awaiting_approval") {
    return <p className="rounded-lg border border-line bg-panel px-5 py-4 text-[14px] text-ink-2">The agents are still investigating. The decision buttons appear when the recommendation is ready.</p>;
  }

  const act = async (kind: "approve" | "reject") => {
    setBusy(true);
    setError(null);
    try {
      if (kind === "approve") await api.approve(inc.id, operator, "");
      else await api.reject(inc.id, operator, reason.trim());
      setMode("idle");
      onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : "The decision was not saved");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rounded-lg border-2 border-ink bg-panel px-5 py-4">
      <p className="font-gauge text-[19px] font-semibold">Your decision</p>
      <p className="text-[14px] text-ink-2">
        The agents are {pct(inc.confidence)} confident. Approving creates the maintenance ticket; nothing is changed on the equipment.
      </p>
      <div className="mt-3 flex flex-wrap items-end gap-3">
        <label className="flex flex-col text-[13px] text-ink-2">
          Decided by
          <input value={operator} onChange={(e) => setOperator(e.target.value)}
            className="mt-1 w-52 rounded-md border border-line bg-paper px-3 py-2 text-[15px] text-ink" />
        </label>
        {mode === "idle" ? (
          <>
            <button disabled={busy || !operator.trim()} onClick={() => act("approve")}
              className="rounded-md bg-chill px-5 py-2.5 text-[15px] font-semibold text-white hover:bg-[#0b6870] disabled:opacity-50">
              {busy ? "Approving…" : "Approve and create ticket"}
            </button>
            <button disabled={busy} onClick={() => setMode("rejecting")}
              className="rounded-md border border-line px-5 py-2.5 text-[15px] font-semibold hover:bg-paper">
              Reject
            </button>
          </>
        ) : (
          <>
            <label className="flex min-w-64 flex-1 flex-col text-[13px] text-ink-2">
              Why are you rejecting it?
              <input autoFocus value={reason} onChange={(e) => setReason(e.target.value)} placeholder="For example: filter was replaced yesterday"
                className="mt-1 rounded-md border border-line bg-paper px-3 py-2 text-[15px] text-ink" />
            </label>
            <button disabled={busy || !reason.trim()} onClick={() => act("reject")}
              className="rounded-md bg-ink px-5 py-2.5 text-[15px] font-semibold text-white disabled:opacity-50">
              {busy ? "Rejecting…" : "Reject recommendation"}
            </button>
            <button onClick={() => setMode("idle")} className="px-2 py-2.5 text-[15px] text-ink-2 hover:text-ink">Cancel</button>
          </>
        )}
      </div>
      {error && <div className="mt-3"><ErrorNote message={error} /></div>}
    </div>
  );
}
