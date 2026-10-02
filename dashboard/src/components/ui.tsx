import type { ReactNode } from "react";
import type { Health, IncidentState, Severity } from "../lib/types";
import { STATE_LABEL } from "../lib/format";

export function Panel({ title, action, children, className = "" }: {
  title?: ReactNode; action?: ReactNode; children: ReactNode; className?: string;
}) {
  return (
    <section className={`rounded-lg border border-line bg-panel ${className}`}>
      {title && (
        <header className="flex items-baseline justify-between gap-3 border-b border-line px-4 py-3">
          <h2 className="font-gauge text-[17px] font-semibold leading-tight">{title}</h2>
          {action}
        </header>
      )}
      {children}
    </section>
  );
}

const SEV: Record<Severity, string> = {
  HIGH: "bg-alarm text-white",
  MEDIUM: "bg-warn-soft text-[#7a5006] ring-1 ring-inset ring-warn/40",
  LOW: "bg-paper text-ink-2 ring-1 ring-inset ring-line",
};
const SEV_WORD: Record<Severity, string> = { HIGH: "High", MEDIUM: "Medium", LOW: "Low" };

export function SeverityTag({ severity }: { severity: Severity }) {
  return (
    <span className={`inline-flex items-center rounded px-1.5 py-0.5 text-[12px] font-semibold ${SEV[severity]}`}>
      {SEV_WORD[severity]}
    </span>
  );
}

const STATE: Record<IncidentState, string> = {
  investigating: "text-chill",
  awaiting_approval: "text-alarm",
  approved: "text-chill",
  rejected: "text-ink-3",
  resolved: "text-ink-3",
};

export function StateText({ state }: { state: IncidentState }) {
  return <span className={`text-[13px] font-medium ${STATE[state]}`}>{STATE_LABEL[state] ?? state}</span>;
}

export const HEALTH_DOT: Record<Health, string> = {
  ok: "bg-chill",
  warning: "bg-warn",
  critical: "bg-alarm",
  offline: "bg-off",
};

export function ErrorNote({ message }: { message: string }) {
  return (
    <div role="alert" className="rounded-md border border-alarm/30 bg-alarm-soft px-4 py-3 text-[14px] text-[#8c1726]">
      {message}
    </div>
  );
}

export function Loading({ label = "Loading" }: { label?: string }) {
  return <p className="px-4 py-6 text-[14px] text-ink-3">{label}…</p>;
}

export function Meter({ value }: { value: number }) {
  // confidence 0..1
  return (
    <span className="inline-flex items-center gap-2" aria-label={`Confidence ${Math.round(value * 100)}%`}>
      <span className="relative h-1.5 w-20 overflow-hidden rounded-full bg-line">
        <span className="absolute inset-y-0 left-0 rounded-full bg-ink" style={{ width: `${value * 100}%` }} />
      </span>
      <span className="font-gauge text-[15px] font-semibold">{Math.round(value * 100)}%</span>
    </span>
  );
}
