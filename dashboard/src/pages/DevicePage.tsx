import { Link, useParams } from "react-router-dom";
import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "../lib/api";
import { SIGNALS, num, time } from "../lib/format";
import { usePoll } from "../lib/live";
import type { Reading } from "../lib/types";
import { ErrorNote, HEALTH_DOT, Loading, Panel } from "../components/ui";

const CHARTS: (keyof Reading)[] = ["zone_temp_c", "airflow_cfm", "power_kw", "supply_temp_c", "humidity_pct", "co2_ppm"];

export default function DevicePage() {
  const { id = "" } = useParams();
  const devices = usePoll(api.devices, 5000);
  const tel = usePoll(() => api.telemetry(id, 120), 5000, id);
  const d = devices.data?.find((x) => x.device_id === id);
  const rows = tel.data?.readings ?? [];

  return (
    <div className="mx-auto max-w-[1400px]">
      <Link to="/" className="text-[14px] text-chill hover:underline">Back to the building</Link>
      <div className="mt-2 flex flex-wrap items-baseline gap-x-4 gap-y-1">
        <h1 className="font-gauge text-[30px] font-semibold">{d?.zone.replace("-", " ") ?? id}, {id}</h1>
        {d && (
          <span className="flex items-center gap-2 text-[14px] text-ink-2">
            <span className={`h-2.5 w-2.5 rounded-full ${HEALTH_DOT[d.health]}`} aria-hidden />
            {d.status === "ON" ? `Running at ${d.fan_speed_pct}% fan speed` : "Switched off"}, {d.occupancy} people in the zone
          </span>
        )}
        {d?.open_incident_id && (
          <Link to={`/incidents/${d.open_incident_id}`} className="rounded-md bg-alarm px-3 py-1 text-[14px] font-semibold text-white">
            Open the incident
          </Link>
        )}
      </div>
      <p className="mt-1 text-[14px] text-ink-2">Last 2 hours of building time. Dashed line: target temperature.</p>

      {tel.error && <div className="mt-4"><ErrorNote message={tel.error} /></div>}
      {!tel.data && !tel.error && <Loading label="Loading sensor history" />}

      <div className="mt-5 grid gap-4 md:grid-cols-2 2xl:grid-cols-3">
        {CHARTS.map((key) => (
          <SignalChart key={key} rows={rows} k={key} setpoint={key === "zone_temp_c" ? d?.setpoint_c : undefined} />
        ))}
      </div>
    </div>
  );
}

function SignalChart({ rows, k, setpoint }: { rows: Reading[]; k: keyof Reading; setpoint?: number }) {
  const meta = SIGNALS[k as string];
  const last = rows.at(-1)?.[k] as number | undefined;
  return (
    <Panel
      title={meta.label}
      action={<span className="font-gauge text-[20px] font-semibold">{num(last, meta.digits)} <span className="text-[14px] font-medium text-ink-2">{meta.unit}</span></span>}
    >
      <div className="h-[170px] px-2 py-3">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={rows} margin={{ top: 4, right: 12, bottom: 0, left: 0 }}>
            <CartesianGrid stroke="#e3e9e7" vertical={false} />
            <XAxis dataKey="ts" tickFormatter={time} tick={{ fontSize: 12, fill: "#4c5f5c" }} minTickGap={40} stroke="#d5dedb" />
            <YAxis width={44} tick={{ fontSize: 12, fill: "#4c5f5c" }} stroke="#d5dedb" domain={["auto", "auto"]}
              tickFormatter={(v: number) => num(v, meta.digits === 0 ? 0 : 1)} />
            {setpoint !== undefined && <ReferenceLine y={setpoint} stroke="#1d2e2c" strokeDasharray="4 4" strokeWidth={1} />}
            <Tooltip
              labelFormatter={(v) => `Building time ${time(String(v))}`}
              formatter={(v) => [`${num(Number(v), meta.digits)} ${meta.unit}`, meta.label]}
              contentStyle={{ borderRadius: 6, border: "1px solid #d5dedb", fontSize: 13 }}
            />
            <Line type="monotone" dataKey={k as string} stroke="#0e7c86" strokeWidth={2} dot={false} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </Panel>
  );
}
