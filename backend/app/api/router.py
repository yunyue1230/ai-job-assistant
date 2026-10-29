from fastapi import APIRouter

from backend.app.api.routes import (
    auth,
    interviews,
    knowledge_bases,
    resumes,
    schedules,
    settings,
    speech,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(resumes.router)
api_router.include_router(knowledge_bases.router)
api_router.include_router(interviews.router)
api_router.include_router(schedules.router)
api_router.include_router(settings.router)
api_router.include_router(speech.router)
