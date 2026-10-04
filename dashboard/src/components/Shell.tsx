import { NavLink, Outlet } from "react-router-dom";
import { Activity, FlaskConical, Gauge, LayoutGrid, ReceiptText, Siren, Zap } from "lucide-react";
import { useState } from "react";
import { api } from "../lib/api";
import { PROVIDER_LABEL, time } from "../lib/format";
import { useLive, usePoll } from "../lib/live";
import type { Provider } from "../lib/types";

const NAV = [
  { to: "/", label: "Overview", icon: LayoutGrid, end: true },
  { to: "/incidents", label: "Incidents", icon: Siren },
  { to: "/activity", label: "Agent activity", icon: Activity },
  { to: "/tickets", label: "Tickets", icon: ReceiptText },
  { to: "/energy", label: "Energy", icon: Zap },
  { to: "/simulator", label: "Simulator", icon: FlaskConical },
];

export default function Shell() {
  const devices = usePoll(api.devices, 5000);
  const incidents = usePoll(api.incidents, 5000);
  const waiting = incidents.data?.filter((i) => i.state === "awaiting_approval").length ?? 0;
  const clock = devices.data?.map((d) => d.ts).sort().at(-1);
  const outside = devices.data?.find((d) => d.outdoor_temp_c !== undefined)?.outdoor_temp_c;

  return (
    <div className="flex min-h-full flex-col lg:flex-row">
      <aside className="border-b border-line bg-panel lg:sticky lg:top-0 lg:h-screen lg:w-[228px] lg:shrink-0 lg:border-r lg:border-b-0">
        <div className="flex items-center gap-3 px-5 py-4 lg:py-6">
          <span className="grid h-9 w-9 place-items-center rounded-md bg-chill text-white">
            <Gauge size={20} strokeWidth={2.2} aria-hidden />
          </span>
          <div className="leading-tight">
            <p className="font-gauge text-[18px] font-semibold">Building A</p>
            <p className="text-[13px] text-ink-2">HVAC operations</p>
          </div>
        </div>
        <nav aria-label="Main" className="flex gap-1 overflow-x-auto px-3 pb-3 lg:flex-col lg:px-3">
          {NAV.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) =>
                `flex shrink-0 items-center gap-3 rounded-md px-3 py-2 text-[15px] transition-colors ${
                  isActive ? "bg-chill-soft font-semibold text-ink" : "text-ink-2 hover:bg-paper hover:text-ink"
                }`
              }
            >
              <Icon size={18} aria-hidden />
              <span>{label}</span>
              {to === "/incidents" && waiting > 0 && (
                <span className="ml-auto rounded-full bg-alarm px-2 text-[12px] font-semibold text-white" aria-label={`${waiting} waiting for approval`}>
                  {waiting}
                </span>
              )}
            </NavLink>
          ))}
        </nav>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex flex-wrap items-center gap-x-6 gap-y-3 border-b border-line bg-paper/90 px-5 py-3 backdrop-blur lg:px-8">
          <div className="flex items-baseline gap-2">
            <span className="text-[13px] text-ink-2">Building time</span>
            <span className="font-gauge text-[22px] font-semibold leading-none">{time(clock)}</span>
          </div>
          {outside !== undefined && (
            <div className="flex items-baseline gap-2">
              <span className="text-[13px] text-ink-2">Outside</span>
              <span className="font-gauge text-[18px] font-semibold leading-none">{outside.toFixed(1)} °C</span>
            </div>
          )}
          <ConnectionPill />
          <div className="ml-auto">
            <ProviderSwitch />
          </div>
        </header>
        <main className="flex-1 px-5 py-6 lg:px-8">
          <Outlet />
        </main>
      </div>
    </div>
  );
}

function ConnectionPill() {
  const { connection } = useLive();
  const map = {
    demo: ["bg-warn", "Demo data"],
    live: ["bg-chill", "Live"],
    polling: ["bg-chill", "Connected"],
    offline: ["bg-alarm", "Backend unreachable"],
  } as const;
  const [dot, label] = map[connection];
  return (
    <span className="inline-flex items-center gap-2 text-[13px] text-ink-2">
      <span className={`h-2 w-2 rounded-full ${dot}`} aria-hidden />
      {label}
    </span>
  );
}

function ProviderSwitch() {
  const settings = usePoll(api.settings, 15000);
  const [busy, setBusy] = useState<Provider | null>(null);
  const [error, setError] = useState<string | null>(null);
  const current = settings.data?.provider;
  const options = settings.data?.available ?? ["openai", "ollama", "rules"];

  const choose = async (p: Provider) => {
    if (p === current || busy) return;
    setBusy(p);
    setError(null);
    try {
      await api.setProvider(p);
      await settings.refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not switch the AI model");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="flex items-center gap-3">
      <span id="ai-label" className="text-[13px] text-ink-2">AI reasoning</span>
      <div role="radiogroup" aria-labelledby="ai-label" className="flex rounded-md border border-line bg-panel p-0.5">
        {options.map((p) => {
          const active = p === current;
          return (
            <button
              key={p}
              role="radio"
              aria-checked={active}
              onClick={() => choose(p)}
              title={settings.data?.models?.[p] ?? undefined}
              className={`rounded px-3 py-1.5 text-[14px] transition-colors ${
                active ? "bg-ink font-semibold text-white" : "text-ink-2 hover:text-ink"
              } ${busy === p ? "opacity-60" : ""}`}
            >
              {PROVIDER_LABEL[p] ?? p}
            </button>
          );
        })}
      </div>
      {error && <span role="alert" className="text-[13px] text-alarm">{error}</span>}
    </div>
  );
}
