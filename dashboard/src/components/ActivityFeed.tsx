import { Link } from "react-router-dom";
import type { AgentStep } from "../lib/types";
import { AGENTS, time } from "../lib/format";

// One colour per agent so the hand-offs between agents are easy to follow.
// Fixed order (dataviz reference palette), never by rank.
export const AGENT_COLOR: Record<string, string> = {
  monitoring: "#2a78d6",        // slot 1
  anomaly_detection: "#eb6834", // slot 2
  supervisor: "#1baf7a",        // slot 3
  diagnosis: "#eda100",         // slot 4
  energy: "#e87ba4",            // slot 5
  maintenance: "#008300",       // slot 6
};

export default function ActivityFeed({ steps, compact = false }: { steps: AgentStep[]; compact?: boolean }) {
  if (!steps.length) {
    return <p className="px-4 py-6 text-[14px] text-ink-3">No agent activity yet. It appears here as soon as a unit behaves unusually.</p>;
  }
  return (
    <ol className="divide-y divide-line">
      {steps.map((s, i) => (
        <li key={`${s.ts}-${i}`} className="flex gap-3 px-4 py-2.5">
          <span className="w-11 shrink-0 pt-0.5 font-gauge text-[14px] font-medium text-ink-2">{time(s.ts)}</span>
          <span className="mt-[7px] h-2 w-2 shrink-0 rounded-full" style={{ background: AGENT_COLOR[s.agent] ?? "#8a9794" }} aria-hidden />
          <div className="min-w-0">
            <p className="text-[14px] leading-snug">
              <span className="font-semibold">{AGENTS[s.agent] ?? s.agent}</span>
              {s.device_id && !compact && <span className="text-ink-2"> on {s.device_id}</span>}
            </p>
            <p className="text-[14px] leading-snug text-ink-2">
              {s.incident_id ? <Link className="hover:underline" to={`/incidents/${s.incident_id}`}>{s.message}</Link> : s.message}
            </p>
          </div>
        </li>
      ))}
    </ol>
  );
}
