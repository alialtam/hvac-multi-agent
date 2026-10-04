// API client. With VITE_API_URL set it talks to the FastAPI backend (Person 2);
// without it, or with ?demo in the URL, it serves the example responses from
// ../contracts/api_examples so the dashboard can be built and shown on its own.
import { time } from "./format";
import type {
  AgentStep, Device, EnergySummary, IncidentDetail, IncidentSummary, LlmSettings, Provider, RejectedSummary,
  SimCommand, SimResult, SimStatus, Telemetry, Ticket,
} from "./types";

import devicesEx from "@contracts/api_examples/devices.json";
import telemetryEx from "@contracts/api_examples/device_telemetry.json";
import incidentsEx from "@contracts/api_examples/incidents.json";
import incidentDetailEx from "@contracts/api_examples/incident_detail.json";
import ticketsEx from "@contracts/api_examples/tickets.json";
import activityEx from "@contracts/api_examples/agents_activity.json";
import energyEx from "@contracts/api_examples/energy_summary.json";
import settingsEx from "@contracts/api_examples/settings_llm.json";
import simulationEx from "@contracts/api_examples/simulation.json";
import rejectedEx from "@contracts/api_examples/rejected_readings.json";

const params = new URLSearchParams(window.location.search);
export const API_URL: string = (import.meta.env.VITE_API_URL ?? "").replace(/\/$/, "");
export const DEMO = !API_URL || params.has("demo");

export class ApiError extends Error {}

async function http<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
  } catch {
    throw new ApiError(`Cannot reach the backend at ${API_URL}. Check that it is running and on the same network.`);
  }
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new ApiError(errorText(text) ?? `${init?.method ?? "GET"} ${path} failed (${res.status}). ${text.slice(0, 200)}`);
  }
  return res.json() as Promise<T>;
}

/** FastAPI errors: {"detail": "text"} or {"detail": [{"msg": "..."}]}. Show the human part. */
function errorText(body: string): string | null {
  try {
    const d = JSON.parse(body).detail;
    if (typeof d === "string") return d.replace(/^error: /, "");
    if (Array.isArray(d) && d[0]?.msg) return d.map((x: { msg: string }) => x.msg.replace(/^Value error, /, "")).join("; ");
  } catch { /* not JSON */ }
  return null;
}

// ------------------------------------------------------------- demo data
// Demo mode keeps a small in-memory state so Approve / Reject and the AI switch
// behave like the real thing during a rehearsal.
const clone = <T,>(x: T): T => JSON.parse(JSON.stringify(x));
const demo = {
  devices: clone(devicesEx) as Device[],
  incidents: clone(incidentsEx) as IncidentSummary[],
  detail: { [incidentDetailEx.id]: clone(incidentDetailEx) } as Record<string, IncidentDetail>,
  tickets: clone(ticketsEx) as Ticket[],
  activity: clone(activityEx) as AgentStep[],
  settings: clone(settingsEx) as LlmSettings,
  sim: clone(simulationEx) as SimStatus,
  rejected: clone(rejectedEx) as RejectedSummary,
  tick: 0,
};

const CORRUPT_REASON: Record<string, string> = {
  spike: "zone_temp_c = 999.0 is outside the possible range -10 to 60",
  missing: "missing airflow_cfm",
  text: "message is not valid JSON",
  negative: "power_kw = -3.2 is outside the possible range 0 to 100",
};

/** Demo mode: apply a command to the in-memory simulator so the page can be rehearsed offline. */
function demoSim(c: SimCommand): SimResult {
  const s = demo.sim;
  const hhmm = time(s.clock);
  let message = "";
  if (c.action === "inject") {
    s.faults = [...(s.faults ?? []).filter((f) => f.device_id !== c.device_id),
      { device_id: c.device_id!, fault: c.fault!, since: s.clock! }];
    message = `[${hhmm}] injected ${c.fault} on ${c.device_id}`;
  } else if (c.action === "reset") {
    s.faults = !c.device_id || c.device_id === "all" ? [] : (s.faults ?? []).filter((f) => f.device_id !== c.device_id);
    message = `[${hhmm}] reset ${!c.device_id || c.device_id === "all" ? "all devices" : c.device_id}`;
  } else if (c.action === "offline" || c.action === "online") {
    const off = new Set(s.offline);
    if (c.action === "offline") off.add(c.device_id!); else off.delete(c.device_id!);
    s.offline = [...off].sort();
    message = `[${hhmm}] ${c.device_id} is now ${c.action}`;
  } else if (c.action === "jump") {
    s.clock = `${s.clock!.slice(0, 11)}${c.time}:00Z`;
    message = `clock is now ${s.clock.slice(0, 10)} ${c.time}`;
  } else if (c.action === "speed") {
    s.seconds_per_reading = c.seconds_per_reading;
    message = `one reading every ${c.seconds_per_reading} s`;
  } else if (c.action === "corrupt") {
    demo.rejected.count++;
    demo.rejected.recent.unshift({ received_at: new Date().toISOString().slice(0, 19) + "Z", device_id: c.device_id!,
      ts: c.kind === "text" ? null : s.clock!, building_time: s.clock!, reason: CORRUPT_REASON[c.kind ?? "spike"] });
    message = `[${hhmm}] sent one broken reading (${c.kind ?? "spike"}) for ${c.device_id}`;
  } else {
    message = `scenario '${c.scenario}' loaded`;
  }
  return { command: c.action, ok: true, message, status: clone(s) };
}

function demoDetail(id: string): IncidentDetail {
  if (demo.detail[id]) return demo.detail[id];
  const s = demo.incidents.find((i) => i.id === id);
  if (!s) throw new ApiError(`Incident ${id} not found`);
  // other example incidents only have a summary; build a short detail for them
  demo.detail[id] = {
    ...s,
    anomaly_event: { event_id: `evt_${s.device_id}`, method: "rule", score: s.confidence, ts_start: s.created_at,
      ts_detected: s.created_at, rule_hits: [], signals: [] },
    trace: [{ ts: s.created_at, agent: "supervisor", message: `Opened incident ${s.id}` }],
    ticket_id: null,
  };
  return demo.detail[id];
}

function demoTelemetry(id: string): Telemetry {
  // replay the example hour with a per-device offset so each AHU looks different
  const idx = Number(id.replace(/\D/g, "")) || 1;
  const base = telemetryEx as Telemetry;
  const d = demo.devices.find((x) => x.device_id === id);
  return {
    device_id: id,
    minutes: 60,
    readings: base.readings.map((r) => ({
      ...r,
      device_id: id,
      zone: d?.zone ?? r.zone,
      setpoint_c: d?.setpoint_c ?? r.setpoint_c,
      zone_temp_c: +(r.zone_temp_c + (d ? d.zone_temp_c - base.readings.at(-1)!.zone_temp_c : 0)).toFixed(2),
      airflow_cfm: id === "AHU-4" ? r.airflow_cfm : Math.round(2250 + ((idx * 37 + r.airflow_cfm) % 40) - 20),
      power_kw: id === "AHU-4" ? r.power_kw : +(d?.power_kw ?? 5.5).toFixed(2),
    })),
  };
}

const wait = (ms = 120) => new Promise((r) => setTimeout(r, ms));

// ------------------------------------------------------------------ API
export const api = {
  async devices(): Promise<Device[]> {
    if (DEMO) {
      await wait();
      demo.tick++;
      // gentle movement so the floor plan looks alive in demo mode
      return demo.devices.map((d, i) => ({
        ...d,
        zone_temp_c: +(d.zone_temp_c + Math.sin(demo.tick / 3 + i) * 0.06).toFixed(2),
        power_kw: +(d.power_kw + Math.cos(demo.tick / 2 + i) * 0.08).toFixed(2),
      }));
    }
    return http("/devices");
  },
  async telemetry(id: string, minutes = 60): Promise<Telemetry> {
    if (DEMO) return (await wait(), demoTelemetry(id));
    const res = await http<Telemetry | Telemetry["readings"]>(`/devices/${encodeURIComponent(id)}/telemetry?minutes=${minutes}`);
    return Array.isArray(res) ? { device_id: id, minutes, readings: res } : res;
  },
  async incidents(): Promise<IncidentSummary[]> {
    if (DEMO) return (await wait(), clone(demo.incidents));
    return http("/incidents");
  },
  async incident(id: string): Promise<IncidentDetail> {
    if (DEMO) return (await wait(), clone(demoDetail(id)));
    return http(`/incidents/${encodeURIComponent(id)}`);
  },
  async approve(id: string, operator: string, note: string) {
    if (DEMO) {
      await wait(300);
      const ticket = `TCK-${String(13 + demo.tickets.length).padStart(4, "0")}`;
      const d = demoDetail(id);
      const now = new Date().toISOString().slice(0, 19) + "Z";
      Object.assign(d, { state: "approved", ticket_id: ticket, approved_by: operator, note, updated_at: now });
      d.trace.push({ ts: now, agent: "supervisor", message: `Approved by ${operator}. Ticket ${ticket} created` });
      demo.incidents = demo.incidents.map((i) => (i.id === id ? { ...i, state: "approved", updated_at: now } : i));
      demo.tickets.unshift({ id: ticket, incident_id: id, device_id: d.device_id, title: d.recommendation?.action ?? d.title,
        priority: d.recommendation?.priority ?? d.severity, status: "open", assign_to: d.recommendation?.assign_to ?? "HVAC technician team",
        created_at: now, estimated_downtime_min: d.recommendation?.estimated_downtime_min ?? 30 });
      demo.activity.unshift({ ts: now, agent: "supervisor", incident_id: id, device_id: d.device_id,
        message: `Approved by ${operator}. Ticket ${ticket} created` });
      return { incident: d, ticket_id: ticket };
    }
    return http<{ ticket_id: string | null }>(`/incidents/${encodeURIComponent(id)}/approve`,
      { method: "POST", body: JSON.stringify({ operator, note }) });
  },
  async reject(id: string, operator: string, reason: string) {
    if (DEMO) {
      await wait(300);
      const d = demoDetail(id);
      const now = new Date().toISOString().slice(0, 19) + "Z";
      Object.assign(d, { state: "rejected", rejected_by: operator, reason, updated_at: now });
      d.trace.push({ ts: now, agent: "supervisor", message: `Rejected by ${operator}: ${reason}` });
      demo.incidents = demo.incidents.map((i) => (i.id === id ? { ...i, state: "rejected", updated_at: now } : i));
      demo.activity.unshift({ ts: now, agent: "supervisor", incident_id: id, device_id: d.device_id,
        message: `Rejected by ${operator}: ${reason}` });
      return { incident: d, ticket_id: null };
    }
    return http<{ ticket_id: null }>(`/incidents/${encodeURIComponent(id)}/reject`,
      { method: "POST", body: JSON.stringify({ operator, reason }) });
  },
  async tickets(): Promise<Ticket[]> {
    if (DEMO) return (await wait(), clone(demo.tickets));
    return http("/tickets");
  },
  async activity(limit = 50): Promise<AgentStep[]> {
    if (DEMO) return (await wait(), clone(demo.activity).slice(0, limit));
    return http(`/agents/activity?limit=${limit}`);
  },
  async energy(): Promise<EnergySummary> {
    if (DEMO) return (await wait(), clone(energyEx) as EnergySummary);
    return http("/energy/summary");
  },
  async settings(): Promise<LlmSettings> {
    if (DEMO) return (await wait(), clone(demo.settings));
    return http("/settings/llm");
  },
  async simulation(): Promise<SimStatus> {
    if (DEMO) return (await wait(), clone(demo.sim));
    return http("/simulation");
  },
  async simCommand(cmd: SimCommand): Promise<SimResult> {
    if (DEMO) return (await wait(250), demoSim(cmd));
    return http("/simulation/command", { method: "POST", body: JSON.stringify(cmd) });
  },
  async rejected(): Promise<RejectedSummary> {
    if (DEMO) return (await wait(), clone(demo.rejected));
    return http("/ingestion/rejected");
  },
  async setProvider(provider: Provider): Promise<LlmSettings> {
    if (DEMO) {
      await wait(250);
      demo.settings.provider = provider;
      return clone(demo.settings);
    }
    return http("/settings/llm", { method: "POST", body: JSON.stringify({ provider }) });
  },
};

export function wsUrl(): string | null {
  if (DEMO) return null;
  return API_URL.replace(/^http/, "ws") + "/ws/live";
}
