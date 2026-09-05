import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";

import {
  ApiError,
  createWorkingSchedule,
  getWorkingSchedule,
  listContracts,
  listWorkingSchedules,
  replaceWorkingScheduleLines,
  updateWorkingSchedule,
} from "../api/client";
import type {
  Contract,
  ScheduleType,
  WorkingScheduleDetail,
  WorkingScheduleItem,
  WorkingScheduleLineInput,
} from "../api/types";
import { useAuth } from "../auth";

const DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

const TYPE_LABEL: Record<ScheduleType, string> = {
  full_time: "Full-time",
  part_time: "Part-time",
  custom: "Custom",
};

export interface ScheduleLineState {
  day_of_week: number;
  enabled: boolean;
  start_time: string; // "HH:MM"
  end_time: string; // "HH:MM"
  break_minutes: number;
}

function timeToMinutes(hhmm: string): number {
  if (!hhmm) return 0;
  const [h, m] = hhmm.split(":").map(Number);
  if (Number.isNaN(h) || Number.isNaN(m)) return 0;
  return h * 60 + m;
}

/** Weekly paid hours from line states (break subtracted), 2dp. */
export function weeklyHours(lines: ScheduleLineState[]): number {
  let minutes = 0;
  for (const l of lines) {
    if (!l.enabled || !l.start_time || !l.end_time) continue;
    const start = timeToMinutes(l.start_time);
    const end = timeToMinutes(l.end_time);
    let diff = end - start;
    if (diff < 0) diff += 24 * 60; // overnight row
    minutes += Math.max(0, diff - (l.break_minutes || 0));
  }
  return Math.round((minutes / 60) * 100) / 100;
}

function emptyLines(): ScheduleLineState[] {
  return DAY_LABELS.map((_, i) => ({
    day_of_week: i,
    enabled: i < 5,
    start_time: "09:00",
    end_time: "18:00",
    break_minutes: 60,
  }));
}

function detailToLines(d: WorkingScheduleDetail | null): ScheduleLineState[] {
  if (!d) return emptyLines();
  const map = new Map(d.lines.map((l) => [l.day_of_week, l]));
  return DAY_LABELS.map((_, i) => {
    const l = map.get(i);
    return {
      day_of_week: i,
      enabled: Boolean(l),
      start_time: l ? l.start_time.slice(0, 5) : "09:00",
      end_time: l ? l.end_time.slice(0, 5) : "18:00",
      break_minutes: l?.break_minutes ?? 60,
    };
  });
}

export function WorkingSchedulesPage() {
  const { isHr } = useAuth();
  const [rows, setRows] = useState<WorkingScheduleItem[]>([]);
  const [contracts, setContracts] = useState<Contract[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Editor state
  const [editingId, setEditingId] = useState<number | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [name, setName] = useState("");
  const [scheduleType, setScheduleType] = useState<ScheduleType>("full_time");
  const [lines, setLines] = useState<ScheduleLineState[]>(emptyLines());
  const [isActive, setIsActive] = useState(true);

  const total = useMemo(() => weeklyHours(lines), [lines]);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [schedPage, contractsPage] = await Promise.all([
        listWorkingSchedules(),
        listContracts({ page_size: 100 } as never),
      ]);
      setRows(schedPage.items);
      setContracts(contractsPage.items);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load schedules.");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  /** Distinct employee count per schedule (from contract assignments). */
  const assignedCount = useMemo(() => {
    const counts = new Map<number, Set<number>>();
    for (const c of contracts) {
      if (!c.working_schedule) continue;
      const set = counts.get(c.working_schedule.id) ?? new Set<number>();
      if (c.employee) set.add(c.employee.id);
      counts.set(c.working_schedule.id, set);
    }
    const out = new Map<number, number>();
    for (const [id, set] of counts) out.set(id, set.size);
    return out;
  }, [contracts]);

  function startNew() {
    setEditingId(null);
    setName("");
    setScheduleType("full_time");
    setLines(emptyLines());
    setIsActive(true);
    setNotice(null);
    setError(null);
  }

  async function startEdit(id: number) {
    setError(null);
    setNotice(null);
    setLoadingDetail(true);
    try {
      const d = await getWorkingSchedule(id);
      setEditingId(id);
      setName(d.name);
      setScheduleType(d.schedule_type);
      setLines(detailToLines(d));
      setIsActive(d.is_active);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load schedule.");
    } finally {
      setLoadingDetail(false);
    }
  }

  function setLine(i: number, patch: Partial<ScheduleLineState>) {
    setLines((ls) => ls.map((l, idx) => (idx === i ? { ...l, ...patch } : l)));
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setNotice(null);
    if (!name.trim()) {
      setError("Schedule name is required.");
      return;
    }
    const activeLines: WorkingScheduleLineInput[] = lines
      .filter((l) => l.enabled && l.start_time && l.end_time)
      .map((l) => ({
        day_of_week: l.day_of_week,
        start_time: `${l.start_time}:00`,
        end_time: `${l.end_time}:00`,
        break_minutes: l.break_minutes || 0,
      }));
    if (activeLines.length === 0) {
      setError("Tick at least one working day.");
      return;
    }
    setBusy(true);
    try {
      if (editingId) {
        await updateWorkingSchedule(editingId, {
          name: name.trim(),
          schedule_type: scheduleType,
          is_active: isActive,
        });
        await replaceWorkingScheduleLines(editingId, activeLines);
        setNotice("Schedule updated.");
      } else {
        await createWorkingSchedule({
          name: name.trim(),
          schedule_type: scheduleType,
          is_active: isActive,
          lines: activeLines,
        });
        setNotice("Schedule created.");
      }
      startNew();
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to save schedule.");
    } finally {
      setBusy(false);
    }
  }

  if (!isHr) {
    return <div className="alert alert-error">Working schedules are HR-only.</div>;
  }

  return (
    <div className="stack">
      <div className="row spread">
        <h2>Working schedules</h2>
        <button
          className="btn btn-ghost btn-sm"
          disabled={busy || loadingDetail}
          onClick={startNew}
        >
          {editingId ? "New schedule" : "＋ New schedule"}
        </button>
      </div>
      {error && <div className="alert alert-error">{error}</div>}
      {notice && <div className="alert alert-ok">{notice}</div>}

      <div className="card">
          <div className="row spread">
            <h3>{editingId ? `Edit schedule #${editingId}` : "New schedule"}</h3>
            <div className="metric" style={{ textAlign: "right" }}>
              <b>{total.toFixed(2)} h</b>
              <span>total weekly hours</span>
            </div>
          </div>
          <form className="grid" onSubmit={onSubmit} style={{ marginBottom: 14 }}>
            <label className="field">
              <span>Name</span>
              <input
                type="text"
                required
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. Full-Time 40h"
              />
            </label>
            <label className="field">
              <span>Type</span>
              <select
                value={scheduleType}
                onChange={(e) => setScheduleType(e.target.value as ScheduleType)}
              >
                <option value="full_time">Full-time</option>
                <option value="part_time">Part-time</option>
                <option value="custom">Custom</option>
              </select>
            </label>
            <label className="check" style={{ alignSelf: "end", paddingBottom: 10 }}>
              <input
                type="checkbox"
                checked={isActive}
                onChange={(e) => setIsActive(e.target.checked)}
              />
              Active
            </label>
          </form>

          <table className="table" style={{ marginBottom: 14 }}>
            <thead>
              <tr>
                <th style={{ width: 90 }}>Day</th>
                <th style={{ width: 80 }}>Working</th>
                <th>Start</th>
                <th>End</th>
                <th style={{ width: 130 }}>Break (min)</th>
                <th className="muted" style={{ width: 90 }}>Hours</th>
              </tr>
            </thead>
            <tbody>
              {lines.map((l, i) => {
                const start = timeToMinutes(l.start_time);
                const end = timeToMinutes(l.end_time);
                let diff = end - start;
                if (diff < 0) diff += 24 * 60;
                const dayHours = l.enabled ? Math.max(0, diff - (l.break_minutes || 0)) / 60 : 0;
                return (
                  <tr key={l.day_of_week}>
                    <td>{DAY_LABELS[i]}</td>
                    <td>
                      <input
                        type="checkbox"
                        checked={l.enabled}
                        onChange={(e) => setLine(i, { enabled: e.target.checked })}
                      />
                    </td>
                    <td>
                      <input
                        type="time"
                        disabled={!l.enabled}
                        value={l.start_time}
                        onChange={(e) => setLine(i, { start_time: e.target.value })}
                      />
                    </td>
                    <td>
                      <input
                        type="time"
                        disabled={!l.enabled}
                        value={l.end_time}
                        onChange={(e) => setLine(i, { end_time: e.target.value })}
                      />
                    </td>
                    <td>
                      <input
                        type="number"
                        min="0"
                        step="5"
                        disabled={!l.enabled}
                        value={l.break_minutes}
                        onChange={(e) =>
                          setLine(i, { break_minutes: Number(e.target.value) || 0 })
                        }
                      />
                    </td>
                    <td className="muted">
                      {l.enabled ? `${dayHours.toFixed(2)} h` : "-"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>

          <div className="row spread">
            <p className="muted small">
              Total weekly hours recalculates live as you edit. Overnight rows (end before
              start) are supported.
            </p>
            <div className="row">
              <button className="btn btn-ghost" type="button" onClick={startNew}>
                Cancel
              </button>
              <button className="btn btn-primary" disabled={busy || loadingDetail}>
                {busy ? "Saving…" : editingId ? "Save schedule" : "Create schedule"}
              </button>
            </div>
          </div>
        </div>

      <table className="table">
        <thead>
          <tr>
            <th>Name</th>
            <th>Type</th>
            <th>Total weekly hours</th>
            <th>Assigned employees</th>
            <th>Status</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((s) => (
            <tr key={s.id}>
              <td><b>{s.name}</b></td>
              <td>{TYPE_LABEL[s.schedule_type] ?? s.schedule_type}</td>
              <td>{s.total_weekly_hours} h</td>
              <td>{assignedCount.get(s.id) ?? 0}</td>
              <td>
                {s.is_active ? (
                  <span className="badge badge-ok">Active</span>
                ) : (
                  <span className="badge badge-muted">Inactive</span>
                )}
              </td>
              <td className="row-actions">
                <button
                  className="btn btn-ghost btn-sm"
                  disabled={loadingDetail || busy}
                  onClick={() => void startEdit(s.id)}
                >
                  Edit
                </button>
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td colSpan={6} className="muted">No schedules yet.</td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}