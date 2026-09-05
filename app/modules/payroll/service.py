"""Service layer for the Payroll module. OWNER: Steve.

Business rules that belong here (from 01_DB_SCHEMA_ELDO.md):
- Computation engine ordering by salary_structure_rules.sequence; formulas
  reference rule codes (Python-expression strings); CONTRACT_WAGE is the
  engine-injected virtual base = contract.wage_monthly.
- Payslips with no running contract -> missing_contract warning, zero salary.
- Recompute only draft/computed payslips; mark-pay cascades to children in
  one transaction; Validate blocked while unresolved warnings exist.
- Overlapping-period detection across payruns.
"""

# TODO(Steve): implement