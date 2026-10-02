import { Link } from "react-router-dom";
import { api } from "../lib/api";
import { money, num, pct, time } from "../lib/format";
import { usePoll } from "../lib/live";
import FloorPlan from "../components/FloorPlan";
import ActivityFeed from "../components/ActivityFeed";
import { ErrorNote, Loading, Panel, SeverityTag } from "../components/ui";

export default function Overview() {
  const devices = usePoll(api.devices, 5000);
  const incidents = usePoll(api.incidents, 5000);
  const activity = usePoll(() => api.activity(8), 5000);
  const energy = usePoll(api.energy, 30000);

  const d = devices.data ?? [];
  const running = d.filter((x) => x.status === "ON");
  const waiting = (incidents.data ?? []).filter((i) => i.state === "awaiting_approval");
  const needsAttention = d.filter((x) => x.health === "critical" || x.health === "warning").length;
  const headline =
    !devices.data ? "Loading the building…" :
    waiting.length ? `${waiting.length === 1 ? "One fault needs" : `${waiting.length} faults need`} your decision` :
    needsAttention ? "The agents are looking into an unusual reading" :
    "All six zones are cooling normally";

  return (
    <div className="mx-auto max-w-[1400px]">
      <h1 className="font-gauge text-[30px] font-semibold leading-tight">{headline}</h1>
      <p className="mt-1 text-[15px] text-ink-2">
        {running.length} of {d.length || 6} air handling units running, using {num(running.reduce((s, x) => s + x.power_kw, 0))} kW now.
        {energy.data && <> Estimated waste today: {money(energy.data.waste_cost_today, energy.data.currency)}.</>}
      </p>

      {devices.error && <div className="mt-4"><ErrorNote message={devices.error} /></div>}

      <div className="mt-6 grid gap-6 xl:grid-cols-[minmax(0,1fr)_380px]">
        <div>
          {devices.data ? <FloorPlan devices={devices.data} /> : <Loading label="Loading zones" />}
        </div>

        <div className="flex flex-col gap-6">
          <Panel title="Waiting for your approval">
            {!incidents.data ? <Loading /> : waiting.length === 0 ? (
              <p className="px-4 py-5 text-[14px] text-ink-2">Nothing to approve. When the agents recommend maintenance, it appears here first.</p>
            ) : (
              <ul className="divide-y divide-line">
                {waiting.map((i) => (
                  <li key={i.id}>
                    <Link to={`/incidents/${i.id}`} className="block px-4 py-3 hover:bg-paper">
                      <div className="flex items-center gap-2">
                        <SeverityTag severity={i.severity} />
                        <span className="text-[13px] text-ink-2">{i.device_id}, detected {time(i.created_at)}</span>
                      </div>
                      <p className="mt-1 font-semibold leading-snug">{i.title}</p>
                      <p className="mt-0.5 text-[13.5px] text-ink-2">
                        {pct(i.confidence)} confident
                        {i.energy_cost_per_day > 0 && <>, costing about {money(i.energy_cost_per_day, i.currency)} per day</>}
                      </p>
                      <span className="mt-2 inline-block rounded-md bg-ink px-3 py-1.5 text-[14px] font-semibold text-white">Review recommendation</span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </Panel>

          <Panel title="What the agents are doing" action={<Link to="/activity" className="text-[13px] text-chill hover:underline">All activity</Link>}>
            {activity.data ? <ActivityFeed steps={activity.data} /> : <Loading />}
          </Panel>
        </div>
      </div>
    </div>
  );
}
