import { Link } from "react-router-dom";
import type { Device } from "../lib/types";
import { num } from "../lib/format";

/**
 * The building as a floor plan: three rooms on each side of a corridor.
 * Each room is one zone and its air handling unit. Colour = health.
 */
export default function FloorPlan({ devices }: { devices: Device[] }) {
  const byZone = [...devices].sort((a, b) => a.device_id.localeCompare(b.device_id, undefined, { numeric: true }));
  const north = byZone.slice(0, 3);
  const south = byZone.slice(3, 6);

  return (
    <div className="rounded-lg border-[3px] border-wall bg-plan p-0" role="list" aria-label="Zones of Building A">
      <div className="grid grid-cols-1 sm:grid-cols-3">
        {north.map((d, i) => <Room key={d.device_id} d={d} side="north" last={i === 2} />)}
      </div>
      <div className="hidden items-center gap-4 border-y-2 border-wall bg-paper px-5 py-2 sm:flex">
        <span className="text-[13px] text-ink-3">Corridor</span>
        <Legend />
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-3">
        {south.map((d, i) => <Room key={d.device_id} d={d} side="south" last={i === 2} />)}
      </div>
    </div>
  );
}

function Legend() {
  const items = [
    ["bg-chill", "Normal"],
    ["bg-warn", "Unusual reading"],
    ["bg-alarm", "Fault being handled"],
    ["bg-off", "Off or not reporting"],
  ];
  return (
    <ul className="ml-auto flex flex-wrap gap-x-4 gap-y-1 text-[12.5px] text-ink-2">
      {items.map(([c, l]) => (
        <li key={l} className="flex items-center gap-1.5">
          <span className={`h-2 w-2 rounded-full ${c}`} aria-hidden />
          {l}
        </li>
      ))}
    </ul>
  );
}

const ROOM = {
  ok: { floor: "", temp: "text-ink", note: "text-chill" },
  warning: { floor: "bg-warn-soft/70 shadow-[inset_0_0_0_2px_var(--color-warn)]", temp: "text-ink", note: "text-[#8a5a05]" },
  critical: { floor: "bg-alarm-soft shadow-[inset_0_0_0_3px_var(--color-alarm)]", temp: "text-ink", note: "text-alarm" },
  offline: { floor: "bg-[repeating-linear-gradient(135deg,transparent_0_8px,#dfe5e3_8px_9px)]", temp: "text-ink-3", note: "text-ink-3" },
} as const;

function Room({ d, side, last }: { d: Device; side: "north" | "south"; last: boolean }) {
  const s = ROOM[d.health];
  const off = d.status === "OFF";
  const delta = d.zone_temp_c - d.setpoint_c;
  const note =
    d.health === "critical" ? "Fault: agents are on it" :
    d.health === "warning" ? "Unusual reading, watching" :
    d.health === "offline" ? "Not reporting" :
    off ? "Unit off" : "Cooling normally";

  return (
    <Link
      to={d.open_incident_id ? `/incidents/${d.open_incident_id}` : `/devices/${d.device_id}`}
      role="listitem"
      aria-label={`${d.zone}, ${d.device_id}: ${num(d.zone_temp_c)} degrees, ${note}`}
      className={`group relative block min-h-[190px] px-5 pb-5 pt-4 transition-colors hover:bg-panel/60 ${s.floor}
        ${last ? "" : "sm:border-r-2 sm:border-wall"} border-b-2 border-wall sm:border-b-0`}
    >
      {d.health === "critical" && (
        <span className="alarm-glow pointer-events-none absolute inset-0 bg-alarm" aria-hidden />
      )}
      {/* door to the corridor */}
      <span
        aria-hidden
        className={`absolute left-1/2 hidden h-[3px] w-12 -translate-x-1/2 bg-paper sm:block ${side === "north" ? "-bottom-[1px]" : "-top-[1px]"}`}
      />
      <div className="relative">
        <div className="flex items-baseline justify-between">
          <h3 className="font-gauge text-[19px] font-semibold">{d.zone.replace("-", " ")}</h3>
          <span className="text-[13px] text-ink-2">{d.device_id}</span>
        </div>

        <p className={`mt-2 font-gauge text-[52px] font-semibold leading-none tracking-tight ${!off && delta > 1 ? "text-alarm" : s.temp}`}>
          {num(d.zone_temp_c)}
          <span className="ml-1 align-top text-[22px] font-medium">°C</span>
        </p>
        <p className="mt-1 text-[13px] text-ink-2">
          Target {num(d.setpoint_c)} °C
          {!off && Math.abs(delta) >= 0.3 && (
            <span className={delta > 0 ? "text-alarm" : "text-chill"}> ({delta > 0 ? "+" : ""}{num(delta)})</span>
          )}
        </p>

        <dl className="mt-3 grid grid-cols-3 gap-2 text-[13px]">
          <div>
            <dt className="text-ink-3">Airflow</dt>
            <dd className="font-gauge text-[16px] font-semibold">{num(d.airflow_cfm, 0)}</dd>
          </div>
          <div>
            <dt className="text-ink-3">Power</dt>
            <dd className="font-gauge text-[16px] font-semibold">{num(d.power_kw)} kW</dd>
          </div>
          <div>
            <dt className="text-ink-3">People</dt>
            <dd className="font-gauge text-[16px] font-semibold">{d.occupancy}</dd>
          </div>
        </dl>

        <p className={`mt-3 text-[13.5px] font-medium ${s.note}`}>{note}</p>
      </div>
    </Link>
  );
}
