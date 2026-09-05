// Salary rules — the global computation library (fixed / % of a base /
// restricted formula). Reads open to payroll roles; writes are
// HR_PAYROLL_MANAGER/ADMIN only (the backend enforces this too).

import { useCallback, useEffect, useState, type FormEvent } from "react";

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
import { Link } from "react-router-dom";

const METHOD_LABEL: Record<ComputationMethod, string> = {
  fixed: "Fixed amount",
  percentage: "% of another rule",
  formula: "Formula",
};

const CATEGORY_OPTIONS: { value: SalaryRuleCategory; label: string }[] = [
  { value: "basic", label: "Basic" },
  { value: "allowance", label: "Allowance" },
  { value: "deduction", label: "Deduction" },
  { value: "gross", label: "Gross" },
  { value: "contribution", label: "Contribution" },
  { value: "net", label: "Net" },
];

const METHOD_OPTIONS: { value: ComputationMethod; label: string }[] = [
  { value: "fixed", label: "Fixed amount" },
  { value: "percentage", label: "% of a base rule" },
  { value: "formula", label: "Formula over codes" },
];

interface RuleForm {
  code: string;
  name: string;
  category: SalaryRuleCategory;
  computation_method: ComputationMethod;
  amount: string;
  percentage: string;
  percentage_base_code: string;
  formula: string;
  default_sequence: number;
}

const EMPTY_FORM: RuleForm = {
  code: "",
  name: "",
  category: "basic",
  computation_method: "fixed",
  amount: "",
  percentage: "",
  percentage_base_code: "",
  formula: "",
  default_sequence: 10,
};

export function SalaryRulesPage() {
  const { hasRole } = useAuth();
  const canWrite = hasRole("HR_PAYROLL_MANAGER", "ADMIN");

  const [rules, setRules] = useState<SalaryRule[]>([]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [form, setForm] = useState<RuleForm>(EMPTY_FORM);
  const [saving, setSaving] = useState(false);
  const [busyId, setBusyId] = useState<number | null>(null);

  const load = useCallback(async () => {
    setError("");
    try {
      const page = await listSalaryRules();
      setRules(page.items);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function onCreate(e: FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError("");
    setNotice("");
    try {
      const payload: Parameters<typeof createSalaryRule>[0] = {
        code: form.code.toUpperCase(),
        name: form.name,
        category: form.category,
        computation_method: form.computation_method,
        default_sequence: form.default_sequence,
      };
      if (form.computation_method === "fixed") {
        payload.amount = form.amount || null;
      } else if (form.computation_method === "percentage") {
        payload.percentage = form.percentage || null;
        payload.percentage_base_code = form.percentage_base_code || null;
      } else {
        payload.formula = form.formula || null;
      }
      await createSalaryRule(payload);
      setNotice(`Rule ${payload.code} created.`);
      setForm(EMPTY_FORM);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  async function onDeactivate(rule: SalaryRule) {
    setBusyId(rule.id);
    setError("");
    try {
      await updateSalaryRule(rule.id, { is_active: false });
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div>
      <h2>Salary rules</h2>
      <p className="small muted">
        One atomic line of pay. A percentage rule needs a base code (e.g.
        BASIC, or the virtual CONTRACT_WAGE); a formula references
        already-computed codes (e.g. <code>BASIC + HRA - PF_DEDUCTION</code>).
      </p>

      {error ? <div className="alert alert-error">{error}</div> : null}
      {notice ? <div className="alert alert-ok">{notice}</div> : null}

      {canWrite && (
        <div className="card" style={{ padding: 16, marginTop: 12 }}>
          <h3 style={{ marginTop: 0 }}>New rule</h3>
          <form className="stack" onSubmit={onCreate}>
            <div className="form-grid">
              <label className="field">
                <span>Code (UPPER_SNAKE)</span>
                <input
                  required
                  pattern="[A-Z][A-Z0-9_]*"
                  placeholder="BONUS"
                  value={form.code}
                  onChange={(e) => setForm({ ...form, code: e.target.value.toUpperCase() })}
                />
              </label>
              <label className="field">
                <span>Name</span>
                <input
                  required
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                />
              </label>
              <label className="field">
                <span>Category</span>
                <select
                  value={form.category}
                  onChange={(e) =>
                    setForm({ ...form, category: e.target.value as SalaryRuleCategory })
                  }
                >
                  {CATEGORY_OPTIONS.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field">
                <span>Computation</span>
                <select
                  value={form.computation_method}
                  onChange={(e) =>
                    setForm({
                      ...form,
                      computation_method: e.target.value as ComputationMethod,
                    })
                  }
                >
                  {METHOD_OPTIONS.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </select>
              </label>

              {form.computation_method === "fixed" && (
                <label className="field">
                  <span>Amount</span>
                  <input
                    required
                    type="number"
                    step="0.01"
                    min="0"
                    value={form.amount}
                    onChange={(e) => setForm({ ...form, amount: e.target.value })}
                  />
                </label>
              )}

              {form.computation_method === "percentage" && (
                <>
                  <label className="field">
                    <span>Percentage %</span>
                    <input
                      required
                      type="number"
                      step="0.01"
                      min="0"
                      max="100"
                      value={form.percentage}
                      onChange={(e) =>
                        setForm({ ...form, percentage: e.target.value })
                      }
                    />
                  </label>
                  <label className="field">
                    <span>Base code</span>
                    <input
                      required
                      placeholder="BASIC / CONTRACT_WAGE"
                      value={form.percentage_base_code}
                      onChange={(e) =>
                        setForm({ ...form, percentage_base_code: e.target.value.toUpperCase() })
                      }
                    />
                  </label>
                </>
              )}

              {form.computation_method === "formula" && (
                <label className="field field-wide">
                  <span>Formula</span>
                  <input
                    required
                    placeholder="BASIC + HRA - PF_DEDUCTION"
                    value={form.formula}
                    onChange={(e) => setForm({ ...form, formula: e.target.value })}
                  />
                </label>
              )}

              <label className="field">
                <span>Default sequence</span>
                <input
                  type="number"
                  value={form.default_sequence}
                  onChange={(e) =>
                    setForm({ ...form, default_sequence: Number(e.target.value) })
                  }
                />
              </label>
            </div>
            <div className="row-actions">
              <button className="btn btn-primary" disabled={saving}>
                {saving ? "Creating…" : "Create rule"}
              </button>
            </div>
          </form>
        </div>
      )}

      <table className="table" style={{ marginTop: 12 }}>
        <thead>
          <tr>
            <th>Code</th>
            <th>Name</th>
            <th>Category</th>
            <th>Method</th>
            <th>Definition</th>
            <th>Seq</th>
            <th>Status</th>
            {canWrite && <th />}
          </tr>
        </thead>
        <tbody>
          {rules.map((r) => (
            <tr key={r.id}>
              <td>
                <b>{r.code}</b>
              </td>
              <td>{r.name}</td>
              <td>
                <span className={`badge badge-${r.category}`}>
                  {r.category}
                </span>
              </td>
              <td>{METHOD_LABEL[r.computation_method]}</td>
              <td>
                {r.computation_method === "fixed" && r.amount}
                {r.computation_method === "percentage" &&
                  `${r.percentage}% of ${r.percentage_base_code}`}
                {r.computation_method === "formula" && (
                  <code>{r.formula}</code>
                )}
              </td>
              <td>{r.default_sequence}</td>
              <td>
                {r.is_active ? (
                  <span className="badge badge-ok">Active</span>
                ) : (
                  <span className="badge badge-muted">Inactive</span>
                )}
              </td>
              {canWrite && (
                <td>
                  <div className="row-actions">
                    <Link
                      className="btn btn-ghost btn-sm"
                      to={`/payroll/rules/${r.id}`}
                    >
                      Edit
                    </Link>
                    {r.is_active ? (
                      <button
                        className="btn btn-ghost btn-sm"
                        disabled={busyId === r.id}
                        onClick={() => void onDeactivate(r)}
                      >
                        Deactivate
                      </button>
                    ) : (
                      <span className="muted small">soft-deleted</span>
                    )}
                  </div>
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
