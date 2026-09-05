import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";

import {
  ApiError,
  createSalaryStructure,
  getSalaryStructure,
  listContracts,
  listSalaryRules,
  listSalaryStructures,
  replaceSalaryStructureRules,
  updateSalaryStructure,
} from "../api/client";
import type {
  Contract,
  SalaryRule,
  SalaryStructureSummary,
} from "../api/types";
import { useAuth } from "../auth";

const CATEGORY_LABEL: Record<string, string> = {
  basic: "Basic",
  allowance: "Allowance",
  deduction: "Deduction",
  gross: "Gross",
  contribution: "Contribution",
  net: "Net",
};

export function SalaryStructuresPage() {
  const { hasRole } = useAuth();
  const canWrite = hasRole("HR_PAYROLL_MANAGER", "ADMIN");

  const [rows, setRows] = useState<SalaryStructureSummary[]>([]);
  const [allRules, setAllRules] = useState<SalaryRule[]>([]);
  const [contracts, setContracts] = useState<Contract[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Editor state
  const [editingId, setEditingId] = useState<number | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [name, setName] = useState("");
  const [code, setCode] = useState("");
  const [isActive, setIsActive] = useState(true);
  const [included, setIncluded] = useState<SalaryRule[]>([]);
  const [toAdd, setToAdd] = useState("");

  const load = useCallback(async () => {
    setError(null);
    try {
      const [structPage, rulesPage, contractsPage] = await Promise.all([
        listSalaryStructures(),
        listSalaryRules(),
        listContracts({ page_size: 100 } as never),
      ]);
      setRows(structPage.items);
      setAllRules(rulesPage.items);
      setContracts(contractsPage.items);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load structures.");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  /** # employees using each structure (distinct, from contract assignments). */
  const usageCount = useMemo(() => {
    const counts = new Map<number, Set<number>>();
    for (const c of contracts) {
      if (!c.salary_structure) continue;
      const set = counts.get(c.salary_structure.id) ?? new Set<number>();
      if (c.employee) set.add(c.employee.id);
      counts.set(c.salary_structure.id, set);
    }
    const out = new Map<number, number>();
    for (const [id, set] of counts) out.set(id, set.size);
    return out;
  }, [contracts]);

  function startNew() {
    setEditingId(null);
    setName("");
    setCode("");
    setIsActive(true);
    setIncluded([]);
    setToAdd("");
    setNotice(null);
    setError(null);
  }

  async function startEdit(id: number) {
    setError(null);
    setNotice(null);
    setLoadingDetail(true);
    try {
      const d = await getSalaryStructure(id);
      setEditingId(id);
      setName(d.name);
      setCode(d.code);
      setIsActive(d.is_active);
      setIncluded(
        [...d.rules]
          .sort((a, b) => a.sequence - b.sequence)
          .map((r) => r.rule),
      );
      setToAdd("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load structure.");
    } finally {
      setLoadingDetail(false);
    }
  }

  const availableRules = useMemo(
    () => allRules.filter((r) => r.is_active && !included.some((i) => i.id === r.id)),
    [allRules, included],
  );

  function addRule() {
    if (!toAdd) return;
    const rule = allRules.find((r) => r.id === Number(toAdd));
    if (rule) setIncluded((prev) => [...prev, rule]);
    setToAdd("");
  }

  function removeRule(id: number) {
    setIncluded((prev) => prev.filter((r) => r.id !== id));
  }

  function moveRule(index: number, dir: -1 | 1) {
    setIncluded((prev) => {
      const next = [...prev];
      const target = index + dir;
      if (target < 0 || target >= next.length) return prev;
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });
  }

  async function toggleActive(s: SalaryStructureSummary) {
    setError(null);
    setNotice(null);
    try {
      await updateSalaryStructure(s.id, { is_active: !s.is_active });
      setNotice(`Structure "${s.name}" ${s.is_active ? "deactivated" : "activated"}.`);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to toggle structure.");
    }
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setNotice(null);
    if (!name.trim() || !code.trim()) {
      setError("Name and code are required.");
      return;
    }
    if (included.length === 0) {
      setError("Add at least one salary rule (sequence matters for computation order).");
      return;
    }
    setBusy(true);
    try {
      if (editingId) {
        await updateSalaryStructure(editingId, { name: name.trim(), code: code.trim(), is_active: isActive });
        await replaceSalaryStructureRules(
          editingId,
          included.map((r, i) => ({ salary_rule_id: r.id, sequence: (i + 1) * 10 })),
        );
        setNotice("Structure updated (rules re-sequenced).");
      } else {
        const created = await createSalaryStructure({ name: name.trim(), code: code.trim(), is_active: isActive });
        await replaceSalaryStructureRules(
          created.id,
          included.map((r, i) => ({ salary_rule_id: r.id, sequence: (i + 1) * 10 })),
        );
        setNotice("Structure created with rules in order.");
      }
      startNew();
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to save structure.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="stack">
      <div className="row spread">
        <h2>Salary structures</h2>
        {canWrite && (
          <button className="btn btn-ghost btn-sm" onClick={startNew} disabled={busy}>
            ＋ New structure
          </button>
        )}
      </div>
      {error && <div className="alert alert-error">{error}</div>}
      {notice && <div className="alert alert-ok">{notice}</div>}

      {canWrite && (
        <div className="card">
          <h3>{editingId ? `Edit structure #${editingId}` : "New structure"}</h3>
          <form className="grid" onSubmit={onSubmit} style={{ marginBottom: 14 }}>
            <label className="field">
              <span>Name</span>
              <input
                type="text"
                required
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. Regular Salary"
              />
            </label>
            <label className="field">
              <span>Code</span>
              <input
                type="text"
                required
                value={code}
                onChange={(e) => setCode(e.target.value.toUpperCase())}
                placeholder="e.g. REGULAR"
              />
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

          <h3 style={{ fontSize: 13 }}>Salary rules (in computation order)</h3>
          {included.length === 0 && (
            <p className="muted small">No rules yet — add the first one below.</p>
          )}
          <table className="table" style={{ marginBottom: 10 }}>
            <tbody>
              {included.map((r, i) => (
                <tr key={r.id}>
                  <td style={{ width: 80 }} className="muted">
                    #{String(i + 1).padStart(2, "0")}
                  </td>
                  <td>
                    <b>{r.name}</b>
                    <div className="small muted">{r.code}</div>
                  </td>
                  <td>
                    <span className={`badge badge-cat-${r.category}`}>
                      {CATEGORY_LABEL[r.category] ?? r.category}
                    </span>
                  </td>
                  <td className="row-actions" style={{ justifyContent: "flex-end" }}>
                    <button
                      type="button"
                      className="btn btn-ghost btn-sm"
                      disabled={i === 0}
                      onClick={() => moveRule(i, -1)}
                      title="Move up"
                    >
                      ↑
                    </button>
                    <button
                      type="button"
                      className="btn btn-ghost btn-sm"
                      disabled={i === included.length - 1}
                      onClick={() => moveRule(i, 1)}
                      title="Move down"
                    >
                      ↓
                    </button>
                    <button
                      type="button"
                      className="btn btn-danger btn-sm"
                      onClick={() => removeRule(r.id)}
                    >
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          <div className="row">
            <label className="field field-wide">
              <span>Add a rule</span>
              <select value={toAdd} onChange={(e) => setToAdd(e.target.value)}>
                <option value="">Select…</option>
                {availableRules.map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.name} ({r.code})
                  </option>
                ))}
              </select>
            </label>
            <div className="field align-end">
              <button type="button" className="btn btn-ghost" onClick={addRule}>
                Add rule
              </button>
            </div>
          </div>

          <div className="row spread" style={{ marginTop: 14 }}>
            <p className="muted small">
              Rules run top to bottom — Basic first, then allowances, Gross, Deductions, Net.
            </p>
            <div className="row">
              <button className="btn btn-ghost" type="button" onClick={startNew}>
                Cancel
              </button>
              <button className="btn btn-primary" disabled={busy || loadingDetail}>
                {busy ? "Saving…" : editingId ? "Save structure" : "Create structure"}
              </button>
            </div>
          </div>
        </div>
      )}

      <table className="table">
        <thead>
          <tr>
            <th>Name</th>
            <th>Code</th>
            <th># Rules</th>
            <th># Employees using</th>
            <th>Status</th>
            {canWrite && <th>Actions</th>}
          </tr>
        </thead>
        <tbody>
          {rows.map((s) => (
            <tr key={s.id}>
              <td><b>{s.name}</b></td>
              <td>{s.code}</td>
              <td>{s.rule_count}</td>
              <td>{usageCount.get(s.id) ?? 0}</td>
              <td>
                {s.is_active ? (
                  <span className="badge badge-ok">Active</span>
                ) : (
                  <span className="badge badge-muted">Inactive</span>
                )}
              </td>
              {canWrite && (
                <td className="row-actions">
                  <button
                    className="btn btn-ghost btn-sm"
                    disabled={loadingDetail || busy}
                    onClick={() => void startEdit(s.id)}
                  >
                    Edit rules
                  </button>
                  <button
                    className={`btn btn-${s.is_active ? "warn" : "ok"} btn-sm`}
                    disabled={busy}
                    onClick={() => void toggleActive(s)}
                  >
                    {s.is_active ? "Deactivate" : "Activate"}
                  </button>
                </td>
              )}
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td colSpan={canWrite ? 6 : 5} className="muted">No structures yet.</td>
            </tr>
          )}
        </tbody>
      </table>
      {!canWrite && (
        <p className="muted small">
          You have read-only access. Only HR_PAYROLL_MANAGER and ADMIN can edit structures.
        </p>
      )}
    </div>
  );
}