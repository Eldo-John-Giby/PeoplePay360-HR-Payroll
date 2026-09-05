"""Employee module router — combines the five sub-routers (OWNER: Ameen).

`app/main.py` imports `router` from this package and mounts it at
prefix `/api/v1`, so every path declared in the sub-routers is relative to
`/api/v1`. Splitting by entity keeps each file small and merge-safe.
"""

from fastapi import APIRouter

from .router_contract import router as contract_router
from .router_department import router as department_router
from .router_employee import router as employee_router
from .router_job_position import router as job_position_router
from .router_schedule import router as schedule_router

router = APIRouter()

router.include_router(department_router)
router.include_router(job_position_router)
router.include_router(schedule_router)
router.include_router(employee_router)
router.include_router(contract_router)