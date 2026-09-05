// Salary structures — a named, ORDERED chain of salary rules that fully
// defines how pay is computed. Reads open to payroll roles; writes
// (create / toggle / replace the rule chain) are HR_PAYROLL_MANAGER/ADMIN
// only. The chain editor replaces the whole ordered list atomically via
// PUT /salary-structures/{id}/rules — the engine executes rules in
// ascending sequence order, so later rules may reference earlier ones.

import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";

import {
  ApiError,
  createSalaryStructure,
  getSalaryStructure,
  listSalaryRules,
  listSalaryStructures,
  replaceStructureRules,
  setStructureActive,
} from "../api/client";
import type {
  SalaryRule,
  SalaryStructure,
  SalaryStructureSummary,
} from "../api/types";
import { useAuth } from "../auth";

export function SalaryStructuresPage() {
  const { hasRole } = useAuth();
  const canWrite = hasRole("HR_PAYROLL_MANAGER", "ADMIN");

  const [structures, setStructures] = useState<SalaryStructureSummary[]>([]);
  const [library, setLibrary] = useState<SalaryRule[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<SalaryStructure | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [name, setName] = useState("");
  const [code, setCode] = useState("");
  const [saving, setSaving] = useState(false);
  const [busyId, setBusyId] = useState<number | null>(null);

  const loadStructures = useCallback(async () => {
    try {
      const page = await listSalaryStructures(100);
      setStructures(page.items);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }, []);

  const load = useCallback(async () => {
    setError("");
    try {
      const [, rules] = await Promise.all([loadStructures(), listSalaryRules({}, 200)]);
      setLibrary(rules.items.filter((r) => r.is_active));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }, [loadStructures]);

  useEffect(() => {
    void load();
  }, [load]);

  // Load the detail (ordered rule chain) whenever a structure is opened.
  useEffect(() => {
    if (selectedId === null) {
      setDetail(null);
      return;
    }
    setError("");
    getSalaryStructure(selectedId)
      .then(setDetail)
      .catch((err) =>
        setError(err instanceof ApiError ? err.message : String(err)),
      );
  }, [selectedId]);

  const activeDetail = detail?.is_active ?? false;

  async function onCreate(e: FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError("");
    setNotice("");
    try {
      const created = await createSalaryStructure({
        name,
        code: code.toUpperCase(),
      });
      setNotice(`Structure ${created.code} created — add rules to its chain below.`);
      setName("");
      setCode("");
      await loadStructures();
      setSelectedId(created.id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  async function onToggleActive(s: SalaryStructureSummary) {
    setBusyId(s.id);
    setError("");
    try {
      await setStructureActive(s.id, !s.is_active);
      if (selectedId === s.id) setDetail(null);
      await loadStructures();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusyId(null);
    }
  }

  // --- ordered chain editor (operates on a local copy until Save) ----------

  function openEditor(item: SalaryStructureSummary) {
    setError("");
    setNotice("");
    setSelectedId(item.id === selectedId ? null : item.id);
  }

  const chain = useMemo(() => detail?.rules ?? [], [detail]);

  const addableRules = useMemo(() => {
    const inChain = new Set(chain.map((r) => r.rule.id));
    return library.filter((r) => !inChain.has(r.id));
  }, [chain, library]);

  function move(idx: number, dir: -1 | 1) {
    if (!detail) return;
    const next = [...chain];
    const j = idx + dir;
    if (j < 0 || j >= next.length) return;
    [next[idx], next[j]] = [next[j], next[idx]];
    setDetail({ ...detail, rules: next });
  }

  function removeAt(idx: number) {
    if (!detail) return;
    setDetail({
      ...detail,
      rules: chain.filter((_, i) => i !== idx),
    });
  }

  function appendRule(ruleId: number) {
    if (!detail) return;
    const rule = library.find((r) => r.id === ruleId);
    if (!rule) return;
    setDetail({
      ...detail,
      rules: [...chain, { sequence: (chain.length + 1) * 10, rule }],
    });
  }

  async function onSaveChain() {
    if (!detail) return;
    setSaving(true);
    setError("");
    setNotice("");
    try {
      // Renumber by position so ascending order == visual order.
      const updated = await replaceStructureRules(
        detail.id,
        chain.map((r, i) => ({ salary_rule_id: r.rule.id, sequence: (i + 1) * 10 })),
      );
      setDetail(updated);
      setNotice("Rule chain saved — execution order is the order shown.");
      await loadStructures();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <h2>Salary structures</h2>
      <p className="small muted">
        A structure is an ordered chain of salary rules — the engine runs them
        in ascending sequence order and later rules can reference earlier ones.
        A payrun is scoped to one structure; contracts reference structures too.
      </p>

      {error ? <div className="alert alert-error">{error}</div> : null}
      {notice ? <div className="alert alert-ok">{notice}</div> : null}

      {canWrite && (
        <div className="card" style={{ padding: 16, marginTop: 12 }}>
          <h3 style={{ marginTop: 0 }}>New structure</h3>
          <form className="row" onSubmit={onCreate} style={{ alignItems: "flex-end" }}>
            <label className="field">
              <span>Name</span>
              <input
                required
                placeholder="Default monthly structure"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </label>
            <label className="field">
              <span>Code (UPPER_SNAKE)</span>
              <input
                required
                pattern="[A-Z][A-Z0-9_]*"
                placeholder="DEFAULT_MONTHLY"
                value={code}
                onChange={(e) => setCode(e.target.value.toUpperCase())}
              />
            </label>
            <button className="btn btn-primary" disabled={saving}>
              {saving ? "Creating…" : "Create shell"}
            </button>
          </form>
          <p className="small muted" style={{ marginBottom: 0 }}>
            Creates the shell with no rules yet — pick it below and add rules to
            its chain.
          </p>
        </div>
      )}

      <table className="table" style={{ marginTop: 12 }}>
        <thead>
          <tr>
            <th>Code</th>
            <th>Name</th>
            <th>Rules</th>
            <th>Status</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {structures.map((s) => (
            <tr
              key={s.id}
              className={selectedId === s.id ? "row-selected" : undefined}
            >
              <td>
                <b>{s.code}</b>
              </td>
              <td>{s.name}</td>
              <td>{s.rule_count}</td>
              <td>
                {s.is_active ? (
                  <span className="badge badge-ok">Active</span>
                ) : (
                  <span className="badge badge-muted">Inactive</span>
                )}
              </td>
              <td className="row-actions" style={{ justifyContent: "flex-end" }}>
                <button className="btn btn-ghost btn-sm" onClick={() => openEditor(s)}>
                  {selectedId === s.id ? "Close" : "Rules"}
                </button>
                {canWrite && (
                  <button
                    className="btn btn-ghost btn-sm"
                    disabled={busyId === s.id}
                    onClick={() => void onToggleActive(s)}
                  >
                    {s.is_active ? "Deactivate" : "Activate"}
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {selectedId !== null && (
        <div className="card" style={{ padding: 16, marginTop: 12 }}>
          <div className="row spread">
            <h3 style={{ margin: 0 }}>
              {detail?.name ?? "Structure"}{" "}
              <span className="muted small">
                — execution order (top executes first)
              </span>
            </h3>
          </div>

          {!activeDetail && (
            <div className="alert alert-error" style={{ margin: "10px 0" }}>
              This structure is inactive — reactivate it before editing or using
              it for a payrun.
            </div>
          )}

          {chain.length === 0 ? (
            <p className="muted">
              No rules in this chain yet. Add rules below.
            </p>
          ) : (
            <table className="table" style={{ marginTop: 10 }}>
              <thead>
                <tr>
                  <th style={{ width: 40 }}>#</th>
                  <th>Rule</th>
                  <th>Category</th>
                  <th>Computes</th>
                  <th style={{ width: 120 }} />
                </tr>
              </thead>
              <tbody>
                {chain.map((item, i) => (
                  <tr key={item.rule.id}>
                    <td className="muted">{i + 1}</td>
                    <td>
                      <b>{item.rule.code}</b>{" "}
                      <span className="muted">· {item.rule.name}</span>
                    </td>
                    <td>
                      <span className={`badge badge-${item.rule.category}`}>
                        {item.rule.category}
                      </span>
                    </td>
                    <td className="small">
                      {item.rule.computation_method === "fixed" && (
                        <>Fixed {item.rule.amount}</>
                      )}
                      {item.rule.computation_method === "percentage" && (
                        <>{item.rule.percentage}% of {item.rule.percentage_base_code}</>
                      )}
                      {item.rule.computation_method === "formula" && (
                        <code>{item.rule.formula}</code>
                      )}
                    </td>
                    <td>
                      <div className="row-actions">
                        <button
                          className="btn btn-ghost btn-sm"
                          disabled={i === 0}
                          onClick={() => move(i, -1)}
                          title="Move earlier"
                        >
                          ↑
                        </button>
                        <button
                          className="btn btn-ghost btn-sm"
                          disabled={i === chain.length - 1}
                          onClick={() => move(i, 1)}
                          title="Move later"
                        >
                          ↓
                        </button>
                        {canWrite && (
                          <button
                            className="btn btn-danger btn-sm"
                            onClick={() => removeAt(i)}
                          >
                            Remove
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          {canWrite && activeDetail && (
            <div className="row" style={{ marginTop: 12 }}>
              <label className="field">
                <span>Add rule to chain</span>
                <select
                  value=""
                  onChange={(e) => {
                    const id = Number(e.target.value);
                    if (id) appendRule(id);
                  }}
                >
                  <option value="">
                    {addableRules.length
                      ? "Choose a rule…"
                      : "All library rules are in the chain"}
                  </option>
                  {addableRules.map((r) => (
                    <option key={r.id} value={r.id}>
                      {r.code} — {r.name}
                    </option>
                  ))}
                </select>
              </label>
              {chain.length > 0 && (
                <button
                  className="btn btn-primary"
                  disabled={saving}
                  onClick={() => void onSaveChain()}
                >
                  {saving ? "Saving…" : "Save chain"}
                </button>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
