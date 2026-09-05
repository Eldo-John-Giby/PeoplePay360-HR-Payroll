"""Service layer for the Attendance & Time Off module. OWNER: Ambuj.

Business rules that belong here (from 01_DB_SCHEMA_ELDO.md):
- Reject a new check-in while an open (check_out IS NULL) row exists.
- worked_hours math with full timestamptz subtraction (overnight shifts).
- Balance checks via the v_time_off_balances view before approving requests.
- Overlapping approved requests -> block with a clear conflict message.
- Manual corrections: HR_MANAGER+ only, set is_manual_correction +
  corrected_by_user_id, never silently overwrite history.
"""

# TODO(Ambuj): implement