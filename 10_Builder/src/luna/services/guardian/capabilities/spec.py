"""
Guardian Capability Contract — typed, discoverable, read-only.

A CapabilitySpec is Guardian's "agent card" entry for one capability.
A CapabilityResult is what invoking it returns.

The shape here is the blueprint for every future Guardian capability
(qa_triage, diagnostic_analysis, citation_verification, etc.).
"""

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CapabilitySpec(BaseModel):
    """One Guardian capability's card — what it does, how to call it."""

    name: str = Field(..., description="Unique capability name, e.g. 'qa_triage'")
    description: str = Field(..., description="Short human-readable purpose")
    input_schema: dict = Field(
        default_factory=dict,
        description="JSON-schema fragment for inputs (empty = no inputs required)",
    )
    output_schema: dict = Field(
        default_factory=dict,
        description="JSON-schema fragment for the CapabilityResult.data payload",
    )
    read_only: bool = Field(
        default=True,
        description="Must be True for Guardian capabilities — writes are not allowed here",
    )
    latency_class: Literal["fast", "medium", "slow"] = Field(
        default="fast",
        description="fast <100ms, medium <1s, slow >1s (local reads only)",
    )
    fallback_behavior: str = Field(
        default="Return status='no_data' when sub-sources are unavailable; never raise.",
        description="What the capability returns when data is unavailable",
    )


class CapabilityResult(BaseModel):
    """The payload returned from invoking a capability."""

    capability: str
    status: Literal["ok", "no_data", "error"]
    data: dict = Field(default_factory=dict)
    as_of: datetime = Field(default_factory=_utcnow)
    source_notes: list[str] = Field(
        default_factory=list,
        description="Provenance / degradation notes (e.g. 'node_status unavailable')",
    )

    def to_jsonable(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
