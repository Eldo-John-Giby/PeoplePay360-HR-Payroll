"""One-command local dev launcher (no Docker / no system Postgres needed).

Boots an embedded pgserver PostgreSQL, provisions the schema, seeds the demo
data, and serves the API on http://localhost:8000.

Why not `alembic upgrade head`?  The initial migration enables the pg_trgm
contrib extension, which embedded-PG builds don't ship (no contrib modules).
This launcher mirrors the migration with the pieces that matter locally:
  * `Base.metadata.create_all` — the models declare every table + index,
    including the running-contract partial unique index;
  * the two SQL views from the migration, verbatim;
  * the trigram GIN search index on `employees.full_name` is skipped (it is
    pg_trgm-only and backs Ameen's employee name-search, still a stub).

On a machine with Docker or a system Postgres, prefer the standard workflow
(`docker compose up --build`, or `alembic upgrade head` + seed + uvicorn from
the README).  This file exists purely so the app can be demoed without one.
"""

import os
import subprocess
import sys
import tempfile

DATA_DIR = os.path.join(tempfile.gettempdir(), "pp360_dev")

import pgserver  # noqa: E402

os.makedirs(DATA_DIR, exist_ok=True)
pg = pgserver.get_server(DATA_DIR, cleanup_mode="stop")

VIEW_BALANCES_SQL = """
CREATE OR REPLACE VIEW v_time_off_balances AS
SELECT
    t.employee_id,
    t.time_off_type_id,
    COALESCE(a.allocated, 0)::NUMERIC(12,2) AS allocated,
    COALESCE(r.taken, 0)::NUMERIC(12,2) AS taken,
    (COALESCE(a.allocated, 0) - COALESCE(r.taken, 0))::NUMERIC(12,2) AS remaining
FROM (
    SELECT employee_id, time_off_type_id FROM time_off_allocations
    UNION
    SELECT employee_id, time_off_type_id FROM time_off_requests
) t
LEFT JOIN (
    SELECT employee_id, time_off_type_id, SUM(allocated_amount) AS allocated
    FROM time_off_allocations
    WHERE status = 'approved'
      AND (valid_to IS NULL OR valid_to >= CURRENT_DATE)
    GROUP BY employee_id, time_off_type_id
) a ON a.employee_id = t.employee_id AND a.time_off_type_id = t.time_off_type_id
LEFT JOIN (
    SELECT employee_id, time_off_type_id, SUM(duration) AS taken
    FROM time_off_requests
    WHERE status = 'approved'
    GROUP BY employee_id, time_off_type_id
) r ON r.employee_id = t.employee_id AND r.time_off_type_id = t.time_off_type_id
"""

VIEW_SCHEDULE_HOURS_SQL = """
CREATE OR REPLACE VIEW v_working_schedule_hours AS
SELECT
    working_schedule_id,
    ROUND(
        EXTRACT(EPOCH FROM SUM(
            end_time - start_time - make_interval(mins => break_minutes)
        )) / 3600.0, 2
    )::NUMERIC(10,2) AS total_weekly_hours
FROM working_schedule_lines
GROUP BY working_schedule_id
"""


def _psql(sql: str) -> None:
    try:
        pg.psql(sql)
    except Exception as exc:  # role/db may already exist
        print(f"psql note ({sql[:36]!r}): {exc}", flush=True)


def main() -> None:
    # Fresh database on every boot -> deterministic demo state.
    _psql("CREATE ROLE peoplepay LOGIN SUPERUSER PASSWORD 'peoplepay'")
    _psql("DROP DATABASE IF EXISTS peoplepay WITH (FORCE)")
    _psql("CREATE DATABASE peoplepay OWNER peoplepay")

    port = pg.get_uri().split("@")[1].split("/")[0].split(":")[1]
    db_url = f"postgresql+psycopg2://peoplepay:peoplepay@127.0.0.1:{port}/peoplepay"
    os.environ["DATABASE_URL"] = db_url
    print(f"PostgreSQL ready -> {db_url}", flush=True)

    # Import the app AFTER DATABASE_URL is set (engine binds at import time).
    from sqlalchemy import text

    from app.core.base import Base
    from app.core.database import engine
    from app.models.employee import Employee

    # Views are not ORM tables: unregister before create_all so they don't get
    # created as real tables (see app/models/views.py docstring).
    from app.models.views import (
        TimeOffBalanceView,
        WorkingScheduleHoursView,
    )

    Base.metadata.remove(TimeOffBalanceView.__table__)
    Base.metadata.remove(WorkingScheduleHoursView.__table__)

    # Skip the pg_trgm-only employees name-search index (contrib unavailable).
    trgm = next(
        (i for i in Employee.__table__.indexes if i.name == "ix_employees_full_name_trgm"),
        None,
    )
    if trgm is not None:
        Employee.__table__.indexes.discard(trgm)

    print("Creating schema (create_all)...", flush=True)
    Base.metadata.create_all(engine)

    with engine.begin() as conn:
        conn.execute(text(VIEW_BALANCES_SQL))
        conn.execute(text(VIEW_SCHEDULE_HOURS_SQL))
    print("Schema + views ready.", flush=True)

    print("Seeding demo data...", flush=True)
    result = subprocess.run(
        [sys.executable, "-m", "app.seed.seed_data"],
        env={**os.environ, "DATABASE_URL": db_url},
    )
    if result.returncode != 0:
        print("Seed failed — aborting.", flush=True)
        sys.exit(1)

    import uvicorn

    print("\nAPI on http://localhost:8000  (docs: /docs)", flush=True)
    print("Demo login: divya.nair@oxp.com / Password@123\n", flush=True)
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    main()
