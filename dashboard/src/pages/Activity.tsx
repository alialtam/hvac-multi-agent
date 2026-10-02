import { useState } from "react";
import { api } from "../lib/api";
import { AGENTS } from "../lib/format";
import { usePoll } from "../lib/live";
import ActivityFeed, { AGENT_COLOR } from "../components/ActivityFeed";
import { ErrorNote, Loading, Panel } from "../components/ui";

export default function Activity() {
  const { data, error } = usePoll(() => api.activity(200), 4000);
  const [agent, setAgent] = useState<string | null>(null);
  const steps = (data ?? []).filter((s) => !agent || s.agent === agent);

  return (
    <div className="mx-auto max-w-[900px]">
      <h1 className="font-gauge text-[30px] font-semibold">Agent activity</h1>
      <p className="mt-1 text-[15px] text-ink-2">Every step the six agents took, newest first. Filter to follow one agent.</p>

      <div className="mt-5 flex flex-wrap gap-2" role="group" aria-label="Filter by agent">
        <Chip active={!agent} onClick={() => setAgent(null)}>All agents</Chip>
        {Object.entries(AGENTS).map(([k, label]) => (
          <Chip key={k} active={agent === k} onClick={() => setAgent(agent === k ? null : k)} color={AGENT_COLOR[k]}>{label}</Chip>
        ))}
      </div>

      {error && <div className="mt-4"><ErrorNote message={error} /></div>}
      <Panel className="mt-4">{data ? <ActivityFeed steps={steps} /> : <Loading />}</Panel>
    </div>
  );
}

function Chip({ active, onClick, color, children }: { active: boolean; onClick: () => void; color?: string; children: React.ReactNode }) {
  return (
    <button onClick={onClick} aria-pressed={active}
      className={`inline-flex items-center gap-2 rounded-full border px-3 py-1 text-[14px] ${active ? "border-ink bg-ink text-white" : "border-line bg-panel text-ink-2 hover:text-ink"}`}>
      {color && <span className="h-2 w-2 rounded-full" style={{ background: color }} aria-hidden />}
      {children}
    </button>
  );
}
