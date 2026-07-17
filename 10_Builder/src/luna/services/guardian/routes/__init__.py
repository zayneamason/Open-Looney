"""
Guardian Routes Package

Combines sub-routers into a single router for server.py.
Mirrors the Kozmo routes pattern exactly.
"""

from fastapi import APIRouter

from luna.core.paths import local_dir

# Demo data root
GUARDIAN_DATA_ROOT = local_dir() / "guardian"

from .threads import router as threads_router
from .knowledge import router as knowledge_router
from .entities import router as entities_router
from .membrane import router as membrane_router
from .graph import router as graph_router
from .ambassador import router as ambassador_router
from .capabilities import router as capabilities_router

router = APIRouter(prefix="/guardian/api", tags=["guardian"])
router.include_router(threads_router)
router.include_router(knowledge_router)
router.include_router(entities_router)
router.include_router(membrane_router)
router.include_router(graph_router)
router.include_router(ambassador_router)
router.include_router(capabilities_router)
