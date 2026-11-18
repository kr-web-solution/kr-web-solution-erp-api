from fastapi import APIRouter
from .v1.router import v1_router

api_router = APIRouter()

@api_router.get("/health", tags=["Health"])
@api_router.get("/api/health", tags=["Health"])
async def health_check():
    return {
        "status": "healthy",
        "service": "KR Workspace Internal ERP & CRM API",
        "version": "1.0.0",
        "company": "KR Web Solution",
        "location": "Salem, Tamil Nadu, India",
    }

# Mount /api/v1/...
api_router.include_router(v1_router, prefix="/api")
