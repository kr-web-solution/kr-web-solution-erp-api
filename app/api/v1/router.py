from fastapi import APIRouter
from ...routers.auth import router as auth_router
from ...routers.users import router as users_router
from ...routers.clients import router as clients_router
from ...routers.leads import router as leads_router
from ...routers.projects import router as projects_router
from ...routers.invoices import router as invoices_router
from ...routers.notes import router as notes_router
from ...routers.settings import router as settings_router
from ...routers.dashboard import router as dashboard_router
from ...routers.engagements import router as engagements_router
from ...routers.staff import router as staff_router
from ...routers.company_expenses import router as company_expenses_router

v1_router = APIRouter(prefix="/v1")

v1_router.include_router(auth_router)
v1_router.include_router(users_router)
v1_router.include_router(clients_router)
v1_router.include_router(leads_router)
v1_router.include_router(projects_router)
v1_router.include_router(invoices_router)
v1_router.include_router(notes_router)
v1_router.include_router(settings_router)
v1_router.include_router(dashboard_router)
v1_router.include_router(engagements_router)
v1_router.include_router(staff_router)
v1_router.include_router(company_expenses_router)
