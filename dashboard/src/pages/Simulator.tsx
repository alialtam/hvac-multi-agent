import { useState } from "react";
import { Link } from "react-router-dom";
import { api, DEMO } from "../lib/api";
import { time } from "../lib/format";
import { usePoll } from "../lib/live";
import type { RejectedSummary, SimCommand, SimStatus } from "../lib/types";
import { ErrorNote, Loading, Panel } from "../components/ui";

// What each fault does, in the words an operator would use (matches the simulator's physics).
const FAULTS: Record<string, { label: string; effect: string }> = {
  filter_blockage: { label: "Filter blockage", effect: "Airflow falls by 40% over 20 minutes and the compressor works harder." },
  compressor_failure: { label: "Compressor failure", effect: "Cooling stops at once. The room warms about 3 °C in the first hour." },
  refrigerant_leak: { label: "Refrigerant leak", effect: "Cooling capacity fades slowly over 3 hours. The hardest one to catch early." },
  sensor_stuck: { label: "Stuck sensor", effect: "The room temperature reading freezes while the room keeps changing." },
  after_hours_waste: { label: "Running after hours", effect: "The unit runs at full fan speed in an empty building. Jump to 19:00 first." },
};
const CORRUPT: Record<string, { label: string; effect: string }> = {
  spike: { label: "Impossible value", effect: "Room temperature 999 °C" },
  missing: { label: "Missing value", effect: "No airflow in the message" },
  text: { label: "Not JSON", effect: "Garbage text instead of data" },
  negative: { label: "Negative power", effect: "Power below zero" },
};
const SPEEDS = [
  { s: 5, label: "Normal", note: "1 reading every 5 s" },
  { s: 2, label: "Faster", note: "every 2 s" },
  { s: 1, label: "Fast", note: "every second" },
];
const SCENARIO_LABEL: Record<string, string> = {
  demo_filter: "Main demo: filter blockage",
  demo_after_hours: "After-hours waste",
  all_faults: "All five faults, one by one",
};
const UNITS = ["AHU-1", "AHU-2", "AHU-3", "AHU-4", "AHU-5", "AHU-6"];

const btn = "rounded-md px-3 py-2 text-[14px] font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-50";
const btnPrimary = `${btn} bg-ink text-white hover:bg-ink/85`;
const btnQuiet = `${btn} border border-line bg-panel text-ink hover:bg-paper`;

export default function Simulator() {
  const sim = usePoll(api.simulation, 3000);
  const rejected = usePoll(api.rejected, 4000);
  const [busy, setBusy] = useState<string | null>(null);
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);

  const s: SimStatus | null = sim.data;
  const units = s?.devices?.length ? s.devices : UNITS;
  const offline = new Set(s?.offline ?? []);
  const disabled = !s?.connected || busy !== null;

  const run = async (key: string, cmd: SimCommand) => {
    setBusy(key);
    setResult(null);
    try {
      const r = await api.simCommand(cmd);
      setResult({ ok: true, text: r.message });
      await Promise.all([sim.refresh(), cmd.action === "corrupt" ? rejected.refresh() : null]);
    } catch (e) {
      setResult({ ok: false, text: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="mx-auto max-w-[1400px]">
      <h1 className="font-gauge text-[30px] font-semibold">Simulator</h1>
      <p className="mt-1 max-w-[72ch] text-[15px] text-ink-2">
        Create faults and test situations for the demo. Everything here acts on the simulated building only.
        The agents do not know a fault was injected: they have to find it in the sensor data.
      </p>

      <StatusBar status={s} error={sim.error} />
      {result && (
        <p role="status" className={`mt-3 rounded-md px-4 py-2.5 text-[14px] ${result.ok ? "bg-chill-soft text-ink" : "bg-alarm-soft text-[#8c1726]"}`}>
          {result.text}
        </p>
      )}

      <div className="mt-6 grid gap-6 xl:grid-cols-[minmax(0,1fr)_400px]">
        <InjectPanel units={units} faultTypes={s?.fault_types} disabled={disabled} busy={busy} onRun={run} />

        <div className="flex flex-col gap-6">
          <Panel
            title="Active faults"
            action={(s?.faults?.length ?? 0) > 0 && (
              <button className="text-[13.5px] font-semibold text-chill hover:underline disabled:opacity-50" disabled={disabled}
                onClick={() => run("reset-all", { action: "reset", device_id: "all" })}>Reset all</button>
            )}
          >
            {!s ? <Loading /> : !s.faults?.length ? (
              <p className="px-4 py-4 text-[14px] text-ink-2">No faults. All units behave normally.</p>
            ) : (
              <ul className="divide-y divide-line">
                {s.faults.map((f) => (
                  <li key={f.device_id} className="flex items-center gap-3 px-4 py-3">
                    <span className="h-2 w-2 shrink-0 rounded-full bg-alarm" aria-hidden />
                    <div className="min-w-0 flex-1">
                      <p className="font-semibold leading-snug">{FAULTS[f.fault]?.label ?? f.fault} on {f.device_id}</p>
                      <p className="text-[13px] text-ink-2">since {time(f.since)} building time</p>
                    </div>
                    <button className={btnQuiet} disabled={disabled} onClick={() => run(`reset-${f.device_id}`, { action: "reset", device_id: f.device_id })}>
                      Reset
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </Panel>

          <Panel title="Building clock">
            <div className="px-4 py-4">
              <p className="text-[13.5px] font-medium text-ink-2">Speed</p>
              <div className="mt-2 grid grid-cols-3 gap-2" role="group" aria-label="Simulation speed">
                {SPEEDS.map((x) => {
                  const active = s?.seconds_per_reading === x.s;
                  return (
                    <button key={x.s} aria-pressed={active} disabled={disabled}
                      onClick={() => run(`speed-${x.s}`, { action: "speed", seconds_per_reading: x.s })}
                      className={`rounded-md border px-2 py-2 text-left transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${active ? "border-ink bg-ink text-white" : "border-line bg-panel hover:bg-paper"}`}>
                      <span className="block text-[14px] font-semibold">{x.label}</span>
                      <span className={`block text-[12px] ${active ? "text-white/80" : "text-ink-2"}`}>{x.note}</span>
                    </button>
                  );
                })}
              </div>
              <JumpClock disabled={disabled} onRun={run} />
            </div>
          </Panel>

          <Panel title="Unit connection">
            <p className="px-4 pt-3 text-[13.5px] text-ink-2">Stop a unit sending data, as if its network failed. The system raises a "device offline" alert after 30 seconds.</p>
            <ul className="grid grid-cols-2 gap-2 px-4 py-3">
              {units.map((u) => {
                const isOff = offline.has(u);
                return (
                  <li key={u}>
                    <button disabled={disabled} aria-pressed={isOff}
                      onClick={() => run(`net-${u}`, { action: isOff ? "online" : "offline", device_id: u })}
                      className={`flex w-full items-center justify-between rounded-md border px-3 py-2 text-[14px] transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${isOff ? "border-off bg-paper" : "border-line bg-panel hover:bg-paper"}`}>
                      <span className="font-semibold">{u}</span>
                      <span className={`inline-flex items-center gap-1.5 text-[13px] ${isOff ? "text-ink-2" : "text-chill"}`}>
                        <span className={`h-2 w-2 rounded-full ${isOff ? "bg-off" : "bg-chill"}`} aria-hidden />
                        {isOff ? "Offline" : "Sending"}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </Panel>

          {(s?.scenarios?.length ?? 0) > 0 && (
            <Panel title="Scripted scenarios">
              <ul className="divide-y divide-line">
                {s!.scenarios!.map((sc) => (
                  <li key={sc} className="flex items-center justify-between gap-3 px-4 py-2.5">
                    <span className="text-[14.5px]">{SCENARIO_LABEL[sc] ?? sc.replace(/_/g, " ")}</span>
                    <button className={btnQuiet} disabled={disabled} onClick={() => run(`sc-${sc}`, { action: "scenario", scenario: sc })}>Run</button>
                  </li>
                ))}
              </ul>
              {(s?.pending_scenario_steps ?? 0) > 0 && (
                <p className="border-t border-line px-4 py-2 text-[13px] text-ink-2">{s!.pending_scenario_steps} scripted step(s) still to come.</p>
              )}
            </Panel>
          )}
        </div>
      </div>

      <BadDataPanel units={units} disabled={disabled} busy={busy} onRun={run} rejected={rejected.data} error={rejected.error} />
    </div>
  );
}

function StatusBar({ status: s, error }: { status: SimStatus | null; error: string | null }) {
  if (error) return <div className="mt-5"><ErrorNote message={error} /></div>;
  if (!s) return <div className="mt-5"><Loading label="Looking for the simulator" /></div>;
  if (!s.connected) {
    return (
      <div className="mt-5 rounded-lg border border-warn/40 bg-warn-soft px-5 py-4">
        <p className="font-semibold">The simulator is not running</p>
        <p className="mt-1 text-[14px] text-ink-2">
          Start it in a terminal: <code className="rounded bg-panel px-1.5 py-0.5 text-[13px]">cd simulator</code> then{" "}
          <code className="rounded bg-panel px-1.5 py-0.5 text-[13px]">python main.py --start 10:30</code>. This page connects by itself.
        </p>
      </div>
    );
  }
  return (
    <div className="mt-5 flex flex-wrap items-center gap-x-6 gap-y-2 rounded-lg border border-line bg-panel px-5 py-3">
      <span className="inline-flex items-center gap-2 text-[14px] font-semibold">
        <span className="h-2 w-2 rounded-full bg-chill" aria-hidden />
        Simulator running{DEMO && " (demo data)"}
      </span>
      <span className="text-[14px] text-ink-2">Building time <b className="font-gauge text-[17px] text-ink">{time(s.clock)}</b></span>
      <span className="text-[14px] text-ink-2">One reading every {s.seconds_per_reading} s</span>
      <span className="text-[14px] text-ink-2">{s.faults?.length ? `${s.faults.length} active fault${s.faults.length > 1 ? "s" : ""}` : "No active faults"}</span>
    </div>
  );
}

function InjectPanel({ units, faultTypes, disabled, busy, onRun }: {
  units: string[]; faultTypes?: string[]; disabled: boolean; busy: string | null;
  onRun: (key: string, cmd: SimCommand) => void;
}) {
  const [unit, setUnit] = useState("AHU-4");
  const [fault, setFault] = useState("filter_blockage");
  const [ramp, setRamp] = useState("");
  const types = faultTypes?.length ? faultTypes : Object.keys(FAULTS);
  const rampNum = ramp.trim() === "" ? undefined : Number(ramp);
  const rampBad = rampNum !== undefined && (!Number.isFinite(rampNum) || rampNum < 1 || rampNum > 600);

  return (
    <Panel title="Inject a fault" className="self-start">
      <div className="px-4 py-4">
        <p id="unit-label" className="text-[13.5px] font-medium text-ink-2">1. Choose the unit</p>
        <div className="mt-2 grid grid-cols-3 gap-2 sm:grid-cols-6" role="radiogroup" aria-labelledby="unit-label">
          {units.map((u) => (
            <button key={u} role="radio" aria-checked={unit === u} onClick={() => setUnit(u)}
              className={`rounded-md border px-2 py-2 text-[14px] font-semibold transition-colors ${unit === u ? "border-ink bg-ink text-white" : "border-line bg-panel hover:bg-paper"}`}>
              {u}
            </button>
          ))}
        </div>

        <p id="fault-label" className="mt-5 text-[13.5px] font-medium text-ink-2">2. Choose the fault</p>
        <div className="mt-2 grid gap-2 md:grid-cols-2" role="radiogroup" aria-labelledby="fault-label">
          {types.map((f) => {
            const on = fault === f;
            return (
              <button key={f} role="radio" aria-checked={on} onClick={() => setFault(f)}
                className={`rounded-md border px-3 py-2.5 text-left transition-colors ${on ? "border-ink bg-chill-soft/60 shadow-[inset_3px_0_0_var(--color-ink)]" : "border-line bg-panel hover:bg-paper"}`}>
                <span className="block font-semibold">{FAULTS[f]?.label ?? f}</span>
                <span className="mt-0.5 block text-[13.5px] leading-snug text-ink-2">{FAULTS[f]?.effect}</span>
              </button>
            );
          })}
        </div>

        <div className="mt-5 flex flex-wrap items-end gap-4">
          <label className="block">
            <span className="text-[13.5px] font-medium text-ink-2">Develops over (minutes, optional)</span>
            <input value={ramp} onChange={(e) => setRamp(e.target.value)} inputMode="numeric" placeholder="default"
              aria-invalid={rampBad}
              className={`mt-1 block w-36 rounded-md border bg-panel px-3 py-2 text-[14px] ${rampBad ? "border-alarm" : "border-line"}`} />
          </label>
          <button className={`${btnPrimary} px-5 py-2.5 text-[15px]`} disabled={disabled || rampBad}
            onClick={() => onRun("inject", { action: "inject", device_id: unit, fault, ...(rampNum ? { ramp_min: rampNum } : {}) })}>
            {busy === "inject" ? "Injecting…" : `Inject ${(FAULTS[fault]?.label ?? fault).toLowerCase()} on ${unit}`}
          </button>
        </div>
        {rampBad && <p className="mt-2 text-[13px] text-[#8c1726]">Enter a number of minutes between 1 and 600, or leave it empty.</p>}
        <p className="mt-4 text-[13px] text-ink-2">
          Watch the <Link to="/" className="text-chill hover:underline">floor plan</Link>: the zone turns amber when the reading looks unusual and
          red when an incident opens, usually within 2 to 10 minutes of building time.
        </p>
      </div>
    </Panel>
  );
}

function JumpClock({ disabled, onRun }: { disabled: boolean; onRun: (key: string, cmd: SimCommand) => void }) {
  const [t, setT] = useState("19:00");
  const valid = /^([01]\d|2[0-3]):[0-5]\d$/.test(t);
  return (
    <div className="mt-4">
      <label htmlFor="jump-time" className="text-[13.5px] font-medium text-ink-2">Jump the clock forward to</label>
      <div className="mt-1 flex gap-2">
        <input id="jump-time" type="time" value={t} onChange={(e) => setT(e.target.value)}
          className="w-32 rounded-md border border-line bg-panel px-3 py-2 text-[14px]" />
        <button className={btnQuiet} disabled={disabled || !valid} onClick={() => onRun("jump", { action: "jump", time: t })}>Jump</button>
      </div>
      <p className="mt-1.5 text-[12.5px] text-ink-3">19:00 is after closing time: use it before "Running after hours". The model runs through the skipped time, so it can take a few seconds.</p>
    </div>
  );
}

function BadDataPanel({ units, disabled, busy, onRun, rejected, error }: {
  units: string[]; disabled: boolean; busy: string | null; onRun: (key: string, cmd: SimCommand) => void;
  rejected: RejectedSummary | null;
  error: string | null;
}) {
  const [unit, setUnit] = useState("AHU-3");
  return (
    <Panel title="Input validation" className="mt-6"
      action={rejected && <span className="text-[13.5px] text-ink-2">{rejected.count} rejected so far</span>}>
      <div className="grid gap-6 px-4 py-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
        <div>
          <p className="text-[14.5px]">
            Send one broken message from a unit. The backend must reject it before it is stored or analysed,
            and keep working normally.
          </p>
          <label className="mt-3 block">
            <span className="text-[13.5px] font-medium text-ink-2">From unit</span>
            <select value={unit} onChange={(e) => setUnit(e.target.value)}
              className="mt-1 block w-36 rounded-md border border-line bg-panel px-3 py-2 text-[14px]">
              {units.map((u) => <option key={u}>{u}</option>)}
            </select>
          </label>
          <div className="mt-3 grid grid-cols-2 gap-2">
            {Object.entries(CORRUPT).map(([k, v]) => (
              <button key={k} disabled={disabled} onClick={() => onRun(`bad-${k}`, { action: "corrupt", device_id: unit, kind: k })}
                className="rounded-md border border-line bg-panel px-3 py-2 text-left transition-colors hover:bg-paper disabled:cursor-not-allowed disabled:opacity-50">
                <span className="block text-[14px] font-semibold">{busy === `bad-${k}` ? "Sending…" : v.label}</span>
                <span className="block text-[12.5px] text-ink-2">{v.effect}</span>
              </button>
            ))}
          </div>
        </div>
        <div>
          <p className="text-[13.5px] font-medium text-ink-2">Rejected messages, newest first</p>
          {error ? <div className="mt-2"><ErrorNote message={error} /></div> : !rejected ? <Loading /> : rejected.recent.length === 0 ? (
            <p className="mt-2 text-[14px] text-ink-2">Nothing rejected yet: every reading so far was valid.</p>
          ) : (
            <ul className="mt-2 divide-y divide-line rounded-md border border-line">
              {rejected.recent.slice(0, 8).map((r, i) => (
                <li key={`${r.received_at}-${i}`} className="flex gap-3 px-3 py-2 text-[14px]">
                  <span className="w-11 shrink-0 font-gauge text-[14px] text-ink-2" title="Building time">{time(r.building_time ?? r.ts)}</span>
                  <span className="w-14 shrink-0 font-semibold">{r.device_id ?? "?"}</span>
                  <span className="min-w-0 text-ink-2">{r.reason}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </Panel>
  );
}
