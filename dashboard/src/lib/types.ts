// Shapes from ../contracts/api.md. Keep in sync with Person 2's API.

export type Health = "ok" | "warning" | "critical" | "offline";
export type Severity = "LOW" | "MEDIUM" | "HIGH";
export type IncidentState = "investigating" | "awaiting_approval" | "approved" | "rejected" | "resolved";
export type Provider = "openai" | "ollama" | "rules";

export interface Reading {
  device_id: string;
  zone: string;
  ts: string;
  zone_temp_c: number;
  supply_temp_c: number;
  setpoint_c: number;
  humidity_pct: number;
  airflow_cfm: number;
  fan_speed_pct: number;
  power_kw: number;
  occupancy: number;
  co2_ppm: number;
  status: "ON" | "OFF";
  outdoor_temp_c?: number;
}

export interface Device extends Reading {
  health: Health;
  open_incident_id?: string | null;
}

export interface IncidentSummary {
  id: string;
  device_id: string;
  zone: string;
  title: string;
  severity: Severity;
  state: IncidentState;
  confidence: number;
  created_at: string;
  updated_at: string;
  llm_provider: Provider;
  energy_cost_per_day: number;
  currency: string;
}

export interface Signal { key: string; value: number; baseline: number | null; z: number }

export interface Triage {
  verdict: "equipment_fault" | "sensor_fault" | "operational_waste" | "building_wide" | "false_alarm" | "unclear";
  suspected_area: string;
  severity: Severity;
  confidence: number;
  summary: string;
  key_evidence: string[];
  recommend_next: "diagnosis" | "energy" | "maintenance" | "human_review";
  provider: string;
  rules_verdict: string;
  agrees_with_rules: boolean;
  tools_used: string[];
  tokens: number;
  duration_ms: number;
}

export interface IncidentDetail extends IncidentSummary {
  anomaly_event: {
    event_id: string;
    method: string;
    score: number;
    ts_start: string;
    ts_detected: string;
    rule_hits: string[];
    signals: Signal[];
  };
  triage?: Triage;
  diagnosis?: { cause: string; cause_text: string; confidence: number; evidence: string[]; sources: string[] };
  energy?: { extra_kwh_per_day: number; cost_per_day: number; currency: string; explanation: string };
  recommendation?: {
    priority: Severity;
    action: string;
    checklist: string[];
    estimated_downtime_min: number;
    assign_to: string;
  };
  trace: { ts: string; agent: string; message: string }[];
  ticket_id: string | null;
  approved_by?: string;
  rejected_by?: string;
  reason?: string;
  note?: string;
}

export interface Ticket {
  id: string;
  incident_id: string;
  device_id: string;
  title: string;
  priority: Severity;
  status: string;
  assign_to: string;
  created_at: string;
  estimated_downtime_min: number;
}

export interface AgentStep {
  ts: string;
  agent: string;
  incident_id: string | null;
  device_id: string | null;
  message: string;
}

export interface EnergySummary {
  date: string;
  currency: string;
  tariff_per_kwh: number;
  total_kwh_today: number;
  baseline_kwh_today: number;
  waste_kwh_today: number;
  waste_cost_today: number;
  devices: { device_id: string; kwh_today: number; baseline_kwh: number; waste_kwh: number }[];
  hourly: { hour: number; kwh: number; baseline_kwh: number }[];
}

export interface LlmSettings {
  provider: Provider;
  available: Provider[];
  models: Record<string, string | null>;
}

export interface Telemetry { device_id: string; minutes: number; readings: Reading[] }

export interface SimFault { device_id: string; fault: string; since: string }

export interface SimStatus {
  connected: boolean;
  running?: boolean;
  clock?: string;
  seconds_per_reading?: number;
  devices?: string[];
  faults?: SimFault[];
  offline?: string[];
  fault_types?: string[];
  corrupt_kinds?: string[];
  scenarios?: string[];
  pending_scenario_steps?: number;
}

export type SimAction = "inject" | "reset" | "offline" | "online" | "jump" | "speed" | "corrupt" | "scenario";

export interface SimCommand {
  action: SimAction;
  device_id?: string;
  fault?: string;
  ramp_min?: number;
  time?: string;
  seconds_per_reading?: number;
  kind?: string;
  scenario?: string;
}

export interface SimResult { command: string; ok: boolean; message: string; status: SimStatus }

export interface RejectedReading { received_at: string; device_id: string | null; ts: string | null; building_time?: string | null; reason: string }
export interface RejectedSummary { count: number; recent: RejectedReading[] }
