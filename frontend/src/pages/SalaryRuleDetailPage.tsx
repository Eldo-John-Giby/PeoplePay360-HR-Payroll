// Salary rule detail — read + edit + delete a single global rule.
// Same computation-library semantics as SalaryRulesPage; writes are
// HR_PAYROLL_MANAGER/ADMIN only (the backend enforces this too).

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link, useParams, useNavigate } from "react-router-dom";

import {
  ApiError,
  deleteSalaryRule,
  getSalaryRule,
  updateSalaryRule,
} from "../api/client";
import type {
  ComputationMethod,
  SalaryRule,
  SalaryRuleCategory,
  SalaryRuleUpdatePayload,
} from "../api/types";
import { useAuth } from "../auth";

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

function formFromRule(r: SalaryRule): RuleForm {
  return {
    code: r.code,
    name: r.name,
    category: r.category,
    computation_method: r.computation_method,
    amount: r.amount ?? "",
    percentage: r.percentage ?? "",
    percentage_base_code: r.percentage_base_code ?? "",
    formula: r.formula ?? "",
    default_sequence: r.default_sequence,
  };
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

export function SalaryRuleDetailPage() {
  const { id } = useParams();
  const ruleId = Number(id);
  const nav = useNavigate();
  const { hasRole } = useAuth();
  const canWrite = hasRole("HR_PAYROLL_MANAGER", "ADMIN");

  const [rule, setRule] = useState<SalaryRule | null>(null);
  const [form, setForm] = useState<RuleForm>(EMPTY_FORM);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const load = useCallback(async () => {
    if (!Number.isFinite(ruleId)) return;
    setError("");
    try {
      const r = await getSalaryRule(ruleId);
      setRule(r);
      setForm(formFromRule(r));
      setDirty(false);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }, [ruleId]);

  useEffect(() => {
    void load();
  }, [load]);

  function keep<K extends keyof RuleForm>(value: RuleForm[K], field: K) {
    setForm((f) => ({ ...f, [field]: value }));
    setDirty(true);
  }

  async function onSave(e: FormEvent) {
    e.preventDefault();
    if (!canWrite || !dirty || !rule) return;
    setSaving(true);
    setError("");
    setNotice("");
    try {
      const payload: SalaryRuleUpdatePayload = {
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
      const updated = await updateSalaryRule(rule.id, payload);
      setRule(updated);
      setForm(formFromRule(updated));
      setDirty(false);
      setNotice(`Rule ${updated.code} updated.`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  async function onDelete() {
    if (!canWrite || !rule) return;
    if (!window.confirm(`Delete salary rule ${rule.code}? It will be soft-deleted (is_active = false).`)) {
      return;
    }
    setDeleting(true);
    setError("");
    setNotice("");
    try {
      await deleteSalaryRule(rule.id);
      setNotice(`Rule ${rule.code} deleted.`);
      nav("/payroll/rules");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setDeleting(false);
    }
  }

  const hasChanges =
    form.code !== rule?.code ||
    form.name !== rule?.name ||
    form.category !== rule?.category ||
    form.computation_method !== rule?.computation_method ||
    form.amount !== (rule?.amount ?? "") ||
    form.percentage !== (rule?.percentage ?? "") ||
    form.percentage_base_code !== (rule?.percentage_base_code ?? "") ||
    form.formula !== (rule?.formula ?? "") ||
    form.default_sequence !== rule?.default_sequence;

  return (
    <div>
      <div className="row spread">
        <h2>
          <Link to="/payroll/rules" className="muted" style={{ fontSize: 14 }}>
            ← Salary rules
          </Link>{" "}
          {rule ? `Rule ${rule.code}` : "Salary rule"}
        </h2>
      </div>

      {error ? <div className="alert alert-error">{error}</div> : null}
      {notice ? <div className="alert alert-ok">{notice}</div> : null}

      {!rule ? (
        <p className="muted">Loading…</p>
      ) : (
        <>
          <div className="card" style={{ padding: 16, marginTop: 12 }}>
            <h3 style={{ marginTop: 0 }}>Edit rule</h3>
            <p className="small muted">
              HR_PAYROLL_MANAGER/ADMIN only. Soft-deletes via Deactivate are
              also available from the rules list.
            </p>

            <form className="stack" onSubmit={onSave}>
              <div className="form-grid">
                <label className="field">
                  <span>Code (UPPER_SNAKE)</span>
                  <input
                    required
                    pattern="[A-Z][A-Z0-9_]*"
                    placeholder="BONUS"
                    value={form.code}
                    onChange={(e) => keep(e.target.value.toUpperCase(), "code")}
                  />
                </label>
                <label className="field">
                  <span>Name</span>
                  <input
                    required
                    value={form.name}
                    onChange={(e) => keep(e.target.value, "name")}
                  />
                </label>
                <label className="field">
                  <span>Category</span>
                  <select
                    value={form.category}
                    onChange={(e) =>
                      keep(e.target.value as SalaryRuleCategory, "category")
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
                      keep(
                        e.target.value as ComputationMethod,
                        "computation_method",
                      )
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
                      onChange={(e) => keep(e.target.value, "amount")}
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
                        onChange={(e) => keep(e.target.value, "percentage")}
                      />
                    </label>
                    <label className="field">
                      <span>Base code</span>
                      <input
                        required
                        placeholder="BASIC / CONTRACT_WAGE"
                        value={form.percentage_base_code}
                        onChange={(e) =>
                          keep(e.target.value.toUpperCase(), "percentage_base_code")
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
                      onChange={(e) => keep(e.target.value, "formula")}
                    />
                  </label>
                )}

                <label className="field">
                  <span>Default sequence</span>
                  <input
                    type="number"
                    value={form.default_sequence}
                    onChange={(e) =>
                      keep(Number(e.target.value), "default_sequence")
                    }
                  />
                </label>
              </div>

              <div className="row-actions">
                <button
                  className="btn btn-primary"
                  disabled={saving || !hasChanges}
                >
                  {saving ? "Saving…" : "Save changes"}
                </button>
              </div>
            </form>
          </div>

          <div className="card" style={{ padding: 16, marginTop: 12 }}>
            <h3 style={{ marginTop: 0 }}>Current values</h3>
            <table className="table" style={{ marginTop: 8 }}>
              <tbody>
                <tr>
                  <td>Code</td>
                  <td>
                    <b>{rule.code}</b>
                  </td>
                </tr>
                <tr>
                  <td>Name</td>
                  <td>{rule.name}</td>
                </tr>
                <tr>
                  <td>Category</td>
                  <td>
                    <span className={`badge badge-${rule.category}`}>
                      {rule.category}
                    </span>
                  </td>
                </tr>
                <tr>
                  <td>Computation method</td>
                  <td>{METHOD_LABEL[rule.computation_method]}</td>
                </tr>
                <tr>
                  <td>Definition</td>
                  <td>
                    {rule.computation_method === "fixed" && rule.amount}
                    {rule.computation_method === "percentage" &&
                      `${rule.percentage}% of ${rule.percentage_base_code}`}
                    {rule.computation_method === "formula" && (
                      <code>{rule.formula}</code>
                    )}
                  </td>
                </tr>
                <tr>
                  <td>Default sequence</td>
                  <td>{rule.default_sequence}</td>
                </tr>
                <tr>
                  <td>Status</td>
                  <td>
                    {rule.is_active ? (
                      <span className="badge badge-ok">Active</span>
                    ) : (
                      <span className="badge badge-muted">Inactive</span>
                    )}
                  </td>
                </tr>
                <tr>
                  <td>Created</td>
                  <td>{rule.created_at}</td>
                </tr>
              </tbody>
            </table>

            {canWrite && rule.is_active && (
              <div className="row-actions" style={{ marginTop: 12 }}>
                <button
                  className="btn btn-danger"
                  disabled={deleting}
                  onClick={() => void onDelete()}
                >
                  {deleting ? "Deleting…" : "Delete rule"}
                </button>
                <span className="small muted" style={{ marginLeft: 8 }}>
                  Soft-deletes the rule (is_active = false). It stays referenced
                  by any structures that use it.
                </span>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
