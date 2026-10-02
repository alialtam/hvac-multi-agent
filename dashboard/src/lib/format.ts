// Display helpers. All timestamps are SIMULATED building time written as UTC, so
// they are always shown in UTC (otherwise an Indian browser would add 5:30).

export const time = (ts?: string | null) =>
  ts ? new Date(ts).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", timeZone: "UTC" }) : "–";

export const dateTime = (ts?: string | null) =>
  ts
    ? new Date(ts).toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "UTC" })
    : "–";

export const num = (v: number | null | undefined, digits = 1) =>
  v === null || v === undefined || Number.isNaN(v) ? "–" : v.toLocaleString("en-IN", { maximumFractionDigits: digits, minimumFractionDigits: digits });

export const money = (v: number, currency = "INR", digits = 0) =>
  v.toLocaleString("en-IN", { style: "currency", currency, maximumFractionDigits: digits, minimumFractionDigits: digits });

export const pct = (v: number) => `${Math.round(v * 100)}%`;

export const AGENTS: Record<string, string> = {
  monitoring: "Monitoring",
  anomaly_detection: "Anomaly detection",
  supervisor: "Supervisor",
  diagnosis: "Diagnosis",
  energy: "Energy",
  maintenance: "Maintenance",
};

export const SIGNALS: Record<string, { label: string; unit: string; digits: number }> = {
  zone_temp_c: { label: "Room temperature", unit: "°C", digits: 1 },
  supply_temp_c: { label: "Supply air temperature", unit: "°C", digits: 1 },
  coil_dt_c: { label: "Cooling across the coil", unit: "°C", digits: 1 },
  humidity_pct: { label: "Humidity", unit: "%", digits: 0 },
  airflow_cfm: { label: "Airflow", unit: "CFM", digits: 0 },
  power_kw: { label: "Power", unit: "kW", digits: 1 },
  co2_ppm: { label: "CO₂", unit: "ppm", digits: 0 },
};

export const RULES: Record<string, string> = {
  zone_too_warm: "Room too warm",
  fast_temp_rise: "Temperature rising fast",
  low_airflow: "Low airflow",
  sensor_flatline: "Sensor reading frozen",
  running_unoccupied: "Running with nobody in",
  device_offline: "Unit stopped reporting",
};

export const STATE_LABEL: Record<string, string> = {
  investigating: "Agents investigating",
  awaiting_approval: "Waiting for approval",
  approved: "Approved",
  rejected: "Rejected",
  resolved: "Resolved",
};

export const PROVIDER_LABEL: Record<string, string> = {
  openai: "OpenAI",
  ollama: "Ollama (local)",
  rules: "Rules only",
};
