import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";

import {
  ApiError,
  createSalaryRule,
  listSalaryRules,
  updateSalaryRule,
} from "../api/client";
import type {
  ComputationMethod,
  SalaryRule,
  SalaryRuleCategory,
} from "../api/types";
import { useAuth } from "../auth";
import { fmtMoney } from "./ContractsPage";

const CATEGORY_LABEL: Record<SalaryRuleCategory, string> = {
  basic: "Basic",
  allowance: "Allowance",
  deduction: "Deduction",
  gross: "Gross",
  contribution: "Contribution",
  net: "Net",
};

const METHOD_LABEL: Record<ComputationMethod, string> = {
  fixed: "Fixed amount",
  percentage: "Percentage of base",
  formula: "Formula",
};

/** Codes usable as a percentage base (rule codes + virtual inputs). */
const VIRTUAL_BASES = ["CONTRACT_WAGE", "WORKED_DAYS", "EXPECTED_DAYS"];

interface FormState {
  code: string;
  name: string;
  category: SalaryRuleCategory;
  computation_method: ComputationMethod;
  amount: string;
  percentage: string;
  percentage_base_code: string;
  formula: string;
  default_sequence: string;
}

const EMPTY_FORM: FormState = {
  code: "",
  name: "",
  category: "basic",
  computation_method: "fixed",
  amount: "",
  percentage: "",
  percentage_base_code: "",
  formula: "",
  default_sequence: "10",
};

export function SalaryRulesPage() {
  const { hasRole } = useAuth();
  const canWrite = hasRole("HR_PAYROLL_MANAGER", "ADMIN");

  const [rows, setRows] = useState<SalaryRule[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState<SalaryRule | null>(null);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);

  const load = useCallback(async () => {
    setError(null);
    try {
      const page = await listSalaryRules();
      setRows(page.items);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load salary rules.");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const baseCodes = useMemo(() => rows.map((r) => r.code), [rows]);

  function startEdit(r: SalaryRule) {
    setEditing(r);
    setForm({
      code: r.code,
      name: r.name,
      category: r.category,
      computation_method: r.computation_method,
      amount: r.amount ?? "",
      percentage: r.percentage ?? "",
      percentage_base_code: r.percentage_base_code ?? "",
      formula: r.formula ?? "",
      default_sequence: String(r.default_sequence),
    });
  }

  function resetForm() {
    setEditing(null);
    setForm(EMPTY_FORM);
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setNotice(null);
    if (!form.code.trim() || !form.name.trim()) {
      setError("Code and name are required.");
      return;
    }
    const method = form.computation_method;
    if (method === "fixed" && !form.amount) {
      setError("Fixed amount rules need an amount.");
      return;
    }
    if (method === "percentage" && (!form.percentage || !form.percentage_base_code)) {
      setError("Percentage rules need a percentage and a base code.");
      return;
    }
    if (method === "formula" && !form.formula.trim()) {
      setError("Formula rules need an expression (e.g. BASIC + HRA - PF_DEDUCTION).");
      return;
    }
    setBusy(true);
    try {
      const common = {
        name: form.name.trim(),
        category: form.category,
        default_sequence: Number(form.default_sequence) || 10,
        is_active: true,
      };
      if (editing) {
        await updateSalaryRule(editing.id, {
          ...common,
          code: form.code.trim().toUpperCase(),
          computation_method: method,
          amount: method === "fixed" ? form.amount : null,
          percentage: method === "percentage" ? form.percentage : null,
          percentage_base_code: method === "percentage" ? form.percentage_base_code : null,
          formula: method === "formula" ? form.formula.trim() : null,
        });
        setNotice(`Rule ${form.code.toUpperCase()} updated.`);
      } else {
        await createSalaryRule({
          ...common,
          code: form.code.trim().toUpperCase(),
          computation_method: method,
          amount: method === "fixed" ? form.amount : null,
          percentage: method === "percentage" ? form.percentage : null,
          percentage_base_code: method === "percentage" ? form.percentage_base_code : null,
          formula: method === "formula" ? form.formula.trim() : null,
        });
        setNotice(`Rule ${form.code.toUpperCase()} created.`);
      }
      resetForm();
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to save salary rule.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="stack">
      <div className="row spread">
        <h2>Salary rules</h2>
        {canWrite && (
          <button className="btn btn-ghost btn-sm" onClick={resetForm} disabled={busy}>
            ＋ New rule
          </button>
        )}
      </div>
      {error && <div className="alert alert-error">{error}</div>}
      {notice && <div className="alert alert-ok">{notice}</div>}

      {canWrite && (
        <div className="card">
          <h3>{editing ? `Edit rule ${editing.code}` : "New salary rule"}</h3>
          <form className="grid" onSubmit={onSubmit}>
            <label className="field">
              <span>Code (uppercase, unique)</span>
              <input
                type="text"
                required
                value={form.code}
                onChange={(e) => setForm({ ...form, code: e.target.value.toUpperCase() })}
                placeholder="e.g. HRA"
              />
            </label>
            <label className="field">
              <span>Name</span>
              <input
                type="text"
                required
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder="e.g. House Rent Allowance"
              />
            </label>
            <label className="field">
              <span>Category</span>
              <select
                value={form.category}
                onChange={(e) => setForm({ ...form, category: e.target.value as SalaryRuleCategory })}
              >
                {Object.entries(CATEGORY_LABEL).map(([k, v]) => (
                  <option key={k} value={k}>{v}</option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Sequence</span>
              <input
                type="number"
                min="0"
                step="10"
                value={form.default_sequence}
                onChange={(e) => setForm({ ...form, default_sequence: e.target.value })}
              />
            </label>
            <label className="field">
              <span>Computation type</span>
              <select
                value={form.computation_method}
                onChange={(e) =>
                  setForm({ ...form, computation_method: e.target.value as ComputationMethod })
                }
              >
                {Object.entries(METHOD_LABEL).map(([k, v]) => (
                  <option key={k} value={k}>{v}</option>
                ))}
              </select>
            </label>

            {form.computation_method === "fixed" && (
              <label className="field">
                <span>Amount</span>
                <input
                  type="number"
                  min="0"
                  step="0.01"
                  value={form.amount}
                  onChange={(e) => setForm({ ...form, amount: e.target.value })}
                  placeholder="e.g. 2200"
                />
              </label>
            )}

            {form.computation_method === "percentage" && (
              <>
                <label className="field">
                  <span>Percentage (%)</span>
                  <input
                    type="number"
                    min="0.01"
                    max="100"
                    step="0.01"
                    value={form.percentage}
                    onChange={(e) => setForm({ ...form, percentage: e.target.value })}
                    placeholder="e.g. 40"
                  />
                </label>
                <label className="field">
                  <span>Percentage base code</span>
                  <select
                    value={form.percentage_base_code}
                    onChange={(e) =>
                      setForm({ ...form, percentage_base_code: e.target.value })
                    }
                  >
                    <option value="">Select…</option>
                    {[...VIRTUAL_BASES, ...baseCodes]
                      .filter((c) => c !== form.code)
                      .map((c) => (
                        <option key={c} value={c}>{c}</option>
                      ))}
                  </select>
                </label>
              </>
            )}

            {form.computation_method === "formula" && (
              <label className="field field-wide">
                <span>Formula (over rule codes)</span>
                <input
                  type="text"
                  value={form.formula}
                  onChange={(e) => setForm({ ...form, formula: e.target.value })}
                  placeholder="e.g. BASIC + HRA - PF_DEDUCTION"
                />
                <small>
                  Available codes: {[...VIRTUAL_BASES, ...baseCodes].join(", ")}. Operators:
                  + - * / ( )
                </small>
              </label>
            )}

            <div className="field align-end">
              <button className="btn btn-primary" disabled={busy}>
                {busy ? "Saving…" : editing ? "Save rule" : "Create rule"}
              </button>
            </div>
          </form>
        </div>
      )}

      <table className="table">
        <thead>
          <tr>
            <th>Name</th>
            <th>Code</th>
            <th>Category</th>
            <th>Sequence</th>
            <th>Computation type</th>
            {canWrite && <th>Actions</th>}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id}>
              <td><b>{r.name}</b></td>
              <td><code>{r.code}</code></td>
              <td>
                <span className={`badge badge-cat-${r.category}`}>
                  {CATEGORY_LABEL[r.category]}
                </span>
              </td>
              <td>{r.default_sequence}</td>
              <td>
                <span className="small muted">
                  {METHOD_LABEL[r.computation_method]}
                  {r.computation_method === "fixed" && ` · ${fmtMoney(r.amount)}`}
                  {r.computation_method === "percentage" &&
                    ` · ${r.percentage}% of ${r.percentage_base_code}`}
                  {r.computation_method === "formula" && ` · ${r.formula}`}
                </span>
              </td>
              {canWrite && (
                <td className="row-actions">
                  <button
                    className="btn btn-ghost btn-sm"
                    disabled={busy}
                    onClick={() => startEdit(r)}
                  >
                    Edit
                  </button>
                </td>
              )}
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td colSpan={canWrite ? 6 : 5} className="muted">No salary rules yet.</td>
            </tr>
          )}
        </tbody>
      </table>
      {!canWrite && (
        <p className="muted small">
          You have read-only access. Only HR_PAYROLL_MANAGER and ADMIN can edit rules.
        </p>
      )}
    </div>
  );
}
