"""
Guardian Capabilities Routes — discovery + direct invocation.

- GET  /guardian/api/capabilities            → list of CapabilitySpec (agent card)
- POST /guardian/api/capabilities/{name}     → invoke capability, returns CapabilityResult
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from luna.services.guardian.capabilities import (
    CapabilityNotFound,
    get_registry,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/capabilities")


@router.get("")
async def list_capabilities() -> dict:
    """Return Guardian's capability card list."""
    registry = get_registry()
    return {
        "capabilities": [spec.model_dump(mode="json") for spec in registry.list_specs()]
    }


@router.post("/{name}")
async def invoke_capability(name: str, inputs: dict | None = None) -> dict:
    """Invoke a Guardian capability by name."""
    registry = get_registry()
    try:
        result = await registry.invoke(name, inputs or {})
    except CapabilityNotFound:
        raise HTTPException(status_code=404, detail=f"Unknown capability: {name}")

    return result.to_jsonable()
