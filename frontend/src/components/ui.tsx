// Shared UI primitives from the PeoplePay360 design system:
// StatusBadge · SmartButton · PayrollStepper · KpiCard · SegmentedToggle.

import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import { cn } from "../lib/cn";

/* ------------------------------------------------------------------ */
/* StatusBadge — colored pill; states, never actions.                   */
/* ------------------------------------------------------------------ */

export type Status =
  | "active"
  | "inactive"
  | "running"
  | "expired"
  | "draft"
  | "computed"
  | "validated"
  | "paid"
  | "cancelled"
  | "approved"
  | "to_approve"
  | "refused"
  | "present"
  | "absent"
  | "late"
  | "overtime"
  | "missing_checkout"
  | "warning"
  | "missing"
  | "terminated";

const STATUS_CLASS: Record<Status, string> = {
  active: "badge-active",
  approved: "badge-approved",
  paid: "badge-paid",
  present: "badge-present",
  draft: "badge-draft",
  inactive: "badge-inactive",
  cancelled: "badge-cancelled",
  running: "badge-running",
  computed: "badge-computed",
  validated: "badge-validated",
  to_approve: "badge-to_approve",
  warning: "badge-warning",
  pending: "badge-pending",
  late: "badge-late",
  expired: "badge-expired",
  absent: "badge-absent",
  refused: "badge-refused",
  terminated: "badge-terminated",
  missing: "badge-missing",
  missing_checkout: "badge-missing_checkout",
  overtime: "badge-overtime",
} as Record<Status, string>;

/** Semantic status pill: dot + label, colors from the status matrix. */
export function StatusBadge({ status, label }: { status: Status; label?: string }) {
  const cls = STATUS_CLASS[status] ?? "badge-muted";
  return (
    <span className={cn("badge", cls)}>
      {label ?? status.replaceAll("_", " ").replace(/\b\w/g, (c) => c.toUpperCase())}
    </span>
  );
}

/* ------------------------------------------------------------------ */
/* SmartButton — record-relationship counter chip                       */
/* (Contracts 2 · Attendance 14 · Time Off 3)                           */
/* ------------------------------------------------------------------ */

export function SmartButton({
  label,
  count,
  to,
}: {
  label: string;
  count: number;
  to: string;
}) {
  return (
    <Link className="smart-btn" to={to} title={`${count} related ${label.toLowerCase()}`}>
      <b className="tabular">{count}</b>
      <span>{label}</span>
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="m9 18 6-6-6-6" />
      </svg>
    </Link>
  );
}

/* ------------------------------------------------------------------ */
/* PayrollStepper — Draft → Computed → Validated → Paid                 */
/* ------------------------------------------------------------------ */

export const PAYROLL_STEPS = ["Draft", "Computed", "Validated", "Paid"] as const;

/**
 * `currentIndex` = index of the current step in PAYROLL_STEPS
 * (0 = draft, 1 = computed, 2 = validated, 3 = paid).
 */
export function PayrollStepper({ currentIndex }: { currentIndex: number }) {
  return (
    <div className="stepper">
      {PAYROLL_STEPS.map((step, i) => (
        <div key={step} className={cn("step", i < currentIndex && "done", i === currentIndex && "current")}>
          <div className="step-dot">{i < currentIndex ? "✓" : i + 1}</div>
          <span className="step-label">{step}</span>
          {i < PAYROLL_STEPS.length - 1 && <div className="step-line" />}
        </div>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* KpiCard — label / big tabular number / context line                  */
/* ------------------------------------------------------------------ */

export function KpiCard({
  label,
  value,
  context,
  trend,
}: {
  label: string;
  value: string;
  context?: string;
  trend?: "up" | "down" | "flat";
}) {
  return (
    <div className="kpi">
      <p className="kpi-label">{label}</p>
      <p className="kpi-value">{value}</p>
      {context && (
        <p className={cn("kpi-context", trend === "up" && "up")}>{context}</p>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* SegmentedToggle — Kanban | List view switcher                        */
/* ------------------------------------------------------------------ */

export function SegmentedToggle<T extends string>({
  value,
  onChange,
  options,
}: {
  value: T;
  onChange: (v: T) => void;
  options: { value: T; label: string }[];
}) {
  return (
    <div className="seg">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          className={o.value === value ? "on" : ""}
          onClick={() => onChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* EmptyState — quiet helper text for empty tables/lists                */
/* ------------------------------------------------------------------ */

export function EmptyState({ children }: { children: ReactNode }) {
  return <p className="muted" style={{ padding: "8px 0" }}>{children}</p>;
}
