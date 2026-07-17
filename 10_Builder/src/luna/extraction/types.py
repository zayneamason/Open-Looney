"""
Extraction Types for Luna Engine
=================================

Dataclasses shared between Scribe (extraction) and Librarian (filing).
These define the contract for the extraction pipeline.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional
import uuid


class ExtractionType(str, Enum):
    """Types of knowledge that can be extracted."""
    FACT = "FACT"               # Something known to be true
    DECISION = "DECISION"       # A choice that was made
    PROBLEM = "PROBLEM"         # An unresolved issue
    ASSUMPTION = "ASSUMPTION"   # Believed but unverified
    CONNECTION = "CONNECTION"   # Relationship between entities
    ACTION = "ACTION"           # Something done or to be done
    OUTCOME = "OUTCOME"         # Result of action or decision
    QUESTION = "QUESTION"       # A question asked or to be answered
    PREFERENCE = "PREFERENCE"   # User preference or opinion
    OBSERVATION = "OBSERVATION" # Something noticed or observed
    MEMORY = "MEMORY"           # A memory or recollection shared
    CORRECTION = "CORRECTION"   # User or Luna corrected a previous claim
    DELEGATION_RESULT = "DELEGATION_RESULT"  # LunaScript delegation round-trip outcome


class SourceProvenance(str, Enum):
    """Where extracted information came from."""
    TOLD = "told"               # User explicitly stated this
    RETRIEVED = "retrieved"     # Came from a document or dataroom
    INFERRED = "inferred"       # Luna's inference (lower confidence)
    CORRECTED = "corrected"     # User or Luna corrected a previous claim
    OBSERVED = "observed"       # Scribe observed from conversation flow


@dataclass
class Chunk:
    """
    A semantic chunk of conversation for extraction.

    Chunks are the unit of extraction - small enough to process
    but large enough for context.
    """
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    content: str = ""
    tokens: int = 0
    source_id: str = ""
    turn_ids: list[int] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)

    def __len__(self) -> int:
        return self.tokens


@dataclass
class ExtractedObject:
    """
    A piece of structured knowledge extracted from conversation.

    This is what Scribe produces and Librarian files.
    """
    type: ExtractionType
    content: str
    confidence: float = 1.0  # 0.0-1.0
    entities: list[str] = field(default_factory=list)
    source_id: str = ""
    metadata: dict = field(default_factory=dict)
    provenance: str = "told"  # SourceProvenance value

    def __post_init__(self):
        # Ensure type is ExtractionType enum
        if isinstance(self.type, str):
            self.type = ExtractionType(self.type)

        # Clamp confidence to valid range
        self.confidence = max(0.0, min(1.0, self.confidence))

    def is_high_confidence(self) -> bool:
        """Check if this extraction is high confidence (>= 0.7)."""
        return self.confidence >= 0.7

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "type": self.type.value,
            "content": self.content,
            "confidence": self.confidence,
            "entities": self.entities,
            "source_id": self.source_id,
            "metadata": self.metadata,
            "provenance": self.provenance,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ExtractedObject":
        """Create from dictionary."""
        return cls(
            type=ExtractionType(data["type"]),
            content=data["content"],
            confidence=data.get("confidence", 1.0),
            entities=data.get("entities", []),
            source_id=data.get("source_id", ""),
            metadata=data.get("metadata", {}),
            provenance=data.get("provenance", "told"),
        )


@dataclass
class ExtractedEdge:
    """
    A relationship between entities extracted from conversation.

    Edges connect nodes in the Memory Matrix graph.
    """
    from_ref: str       # Entity name or ID
    to_ref: str         # Entity name or ID
    edge_type: str      # "works_on", "decided", "caused", etc.
    confidence: float = 1.0
    role: Optional[str] = None  # "author", "subject", etc.
    source_id: str = ""

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "from_ref": self.from_ref,
            "to_ref": self.to_ref,
            "edge_type": self.edge_type,
            "confidence": self.confidence,
            "role": self.role,
            "source_id": self.source_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ExtractedEdge":
        """Create from dictionary."""
        return cls(
            from_ref=data["from_ref"],
            to_ref=data["to_ref"],
            edge_type=data["edge_type"],
            confidence=data.get("confidence", 1.0),
            role=data.get("role"),
            source_id=data.get("source_id", ""),
        )


@dataclass
class ExtractionOutput:
    """
    Complete output from an extraction operation.

    Contains all objects and edges extracted from a chunk.
    """
    objects: list[ExtractedObject] = field(default_factory=list)
    edges: list[ExtractedEdge] = field(default_factory=list)
    source_id: str = ""
    extraction_time_ms: int = 0
    flow_signal: Optional["FlowSignal"] = None  # Layer 2: conversational flow state
    # Origin modality of the turn ("text", "voice", "api", "lunafm", etc.).
    # Carried so the Librarian can stamp resulting MemoryNodes with a
    # canonical `luna:{modality}:{session_id}` source for cross-modal search
    # and provenance. Defaults to "text" for legacy callers.
    modality: str = "text"

    def __len__(self) -> int:
        return len(self.objects)

    def is_empty(self) -> bool:
        """Check if extraction produced nothing."""
        return len(self.objects) == 0 and len(self.edges) == 0

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        result = {
            "objects": [obj.to_dict() for obj in self.objects],
            "edges": [edge.to_dict() for edge in self.edges],
            "source_id": self.source_id,
            "extraction_time_ms": self.extraction_time_ms,
            "modality": self.modality,
        }
        if self.flow_signal:
            result["flow_signal"] = self.flow_signal.to_dict()
        return result

    @classmethod
    def from_dict(cls, data: dict) -> "ExtractionOutput":
        """Create from dictionary."""
        flow_data = data.get("flow_signal")
        return cls(
            objects=[ExtractedObject.from_dict(o) for o in data.get("objects", [])],
            edges=[ExtractedEdge.from_dict(e) for e in data.get("edges", [])],
            source_id=data.get("source_id", ""),
            extraction_time_ms=data.get("extraction_time_ms", 0),
            flow_signal=FlowSignal.from_dict(flow_data) if flow_data else None,
            modality=data.get("modality", "text"),
        )


@dataclass
class FilingResult:
    """
    Result of filing an extraction into the Memory Matrix.

    Returned by Librarian after processing an ExtractionOutput.
    """
    nodes_created: list[str] = field(default_factory=list)
    nodes_merged: list[tuple[str, str]] = field(default_factory=list)  # (name, existing_id)
    edges_created: list[str] = field(default_factory=list)
    edges_skipped: list[str] = field(default_factory=list)
    filing_time_ms: int = 0

    # Layer 4: Task Ledger — real node IDs for ACTION/OUTCOME tracking
    action_node_ids: list[str] = field(default_factory=list)
    outcome_node_ids: list[str] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.nodes_created) + len(self.nodes_merged)

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "nodes_created": self.nodes_created,
            "nodes_merged": self.nodes_merged,
            "edges_created": self.edges_created,
            "edges_skipped": self.edges_skipped,
            "filing_time_ms": self.filing_time_ms,
            "action_node_ids": self.action_node_ids,
            "outcome_node_ids": self.outcome_node_ids,
        }


# =============================================================================
# EXTRACTION CONFIGURATION
# =============================================================================

@dataclass
class ExtractionConfig:
    """
    Configuration for extraction backend.

    Allows runtime switching between Claude models and local inference.
    """
    backend: str = "haiku"  # "local", "haiku", "sonnet", "disabled"
    batch_size: int = 5     # Turns per extraction call
    min_content_length: int = 10  # Skip very short messages

    # Model-specific settings
    temperature: float = 0.3  # Lower = more deterministic extractions
    max_tokens: int = 1024    # Max response tokens


# =============================================================================
# FLOW AWARENESS TYPES (Layer 2)
# =============================================================================

class ConversationMode(str, Enum):
    """The current conversational mode detected by Scribe."""
    FLOW = "FLOW"                     # Continuing on-topic
    RECALIBRATION = "RECALIBRATION"   # Topic shift detected
    AMEND = "AMEND"                   # Course correction within flow


@dataclass
class FlowSignal:
    """
    Scribe's assessment of conversational continuity.

    Emitted with every extraction. Consumed by Librarian
    for thread management decisions.
    """
    mode: ConversationMode

    # Topic tracking
    current_topic: str              # Brief label for what's being discussed
    topic_entities: list[str] = field(default_factory=list)

    # Continuity metrics
    continuity_score: float = 1.0   # 0.0 (total shift) to 1.0 (same thread)
    entity_overlap: float = 1.0     # Jaccard similarity vs recent turns

    # Open threads detected
    open_threads: list[str] = field(default_factory=list)

    # Amend specifics (only populated in AMEND mode)
    correction_target: str = ""

    # Debug
    signals_detected: list[str] = field(default_factory=list)

    # Sovereignty: where was flow detection performed
    detection_method: str = "local"  # "local" | "cloud" — must always be "local"

    def to_dict(self) -> dict:
        return {
            "mode": self.mode.value,
            "current_topic": self.current_topic,
            "topic_entities": self.topic_entities,
            "continuity_score": self.continuity_score,
            "entity_overlap": self.entity_overlap,
            "open_threads": self.open_threads,
            "correction_target": self.correction_target,
            "signals_detected": self.signals_detected,
            "detection_method": self.detection_method,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "FlowSignal":
        return cls(
            mode=ConversationMode(data["mode"]),
            current_topic=data.get("current_topic", ""),
            topic_entities=data.get("topic_entities", []),
            continuity_score=data.get("continuity_score", 1.0),
            entity_overlap=data.get("entity_overlap", 1.0),
            open_threads=data.get("open_threads", []),
            correction_target=data.get("correction_target", ""),
            signals_detected=data.get("signals_detected", []),
            detection_method=data.get("detection_method", "local"),
        )


class ContinuityDisposition(str, Enum):
    """Librarian's thread-attachment decision, separate from conversational mode."""
    ATTACH_ACTIVE = "attach_active"
    RESUME_PARKED = "resume_parked"
    CREATE_NEW = "create_new"


@dataclass
class ContinuityDecision:
    """
    Result of the Librarian continuity pre-gate.

    Separates conversational mode (FlowSignal.mode) from the concrete
    thread-attachment disposition so thread operations are explicit and
    testable in isolation.
    """
    disposition: ContinuityDisposition
    signal_mode: ConversationMode
    entity_overlap: float
    parked_overlap: float = 0.0
    target_thread_id: Optional[str] = None
    rationale: str = ""


# =============================================================================
# THREAD MANAGEMENT TYPES (Layer 3)
# =============================================================================

class ThreadStatus(str, Enum):
    """Thread lifecycle states."""
    ACTIVE = "active"
    PARKED = "parked"
    RESUMED = "resumed"     # Transient — becomes ACTIVE on next turn
    CLOSED = "closed"


@dataclass
class Thread:
    """
    A named stretch of conversational attention.

    Persisted as a THREAD node in Memory Matrix.
    """
    id: str
    topic: str
    status: ThreadStatus = ThreadStatus.ACTIVE
    entities: list[str] = field(default_factory=list)
    entity_node_ids: list[str] = field(default_factory=list)
    open_tasks: list[str] = field(default_factory=list)
    turn_count: int = 0
    started_at: datetime = field(default_factory=datetime.now)
    parked_at: Optional[datetime] = None
    resumed_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    project_slug: Optional[str] = None
    study_context_sections: list[str] = field(default_factory=list)
    parent_thread_id: Optional[str] = None
    resume_count: int = 0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "topic": self.topic,
            "status": self.status.value,
            "entities": self.entities,
            "entity_node_ids": self.entity_node_ids,
            "open_tasks": self.open_tasks,
            "turn_count": self.turn_count,
            "started_at": self.started_at.isoformat(),
            "parked_at": self.parked_at.isoformat() if self.parked_at else None,
            "resumed_at": self.resumed_at.isoformat() if self.resumed_at else None,
            "closed_at": self.closed_at.isoformat() if self.closed_at else None,
            "project_slug": self.project_slug,
            "study_context_sections": self.study_context_sections,
            "parent_thread_id": self.parent_thread_id,
            "resume_count": self.resume_count,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Thread":
        return cls(
            id=data["id"],
            topic=data["topic"],
            status=ThreadStatus(data.get("status", "active")),
            entities=data.get("entities", []),
            entity_node_ids=data.get("entity_node_ids", []),
            open_tasks=data.get("open_tasks", []),
            turn_count=data.get("turn_count", 0),
            started_at=datetime.fromisoformat(data["started_at"]) if data.get("started_at") else datetime.now(),
            parked_at=datetime.fromisoformat(data["parked_at"]) if data.get("parked_at") else None,
            resumed_at=datetime.fromisoformat(data["resumed_at"]) if data.get("resumed_at") else None,
            closed_at=datetime.fromisoformat(data["closed_at"]) if data.get("closed_at") else None,
            project_slug=data.get("project_slug"),
            study_context_sections=data.get("study_context_sections", []),
            parent_thread_id=data.get("parent_thread_id"),
            resume_count=data.get("resume_count", 0),
        )


# Backend configurations
EXTRACTION_BACKENDS = {
    "haiku": {
        "model": "claude-haiku-4-5-20251001",
        "cost_per_1k_input": 0.0008,
        "cost_per_1k_output": 0.004,
        "quality": "good",
    },
    "sonnet": {
        "model": "claude-sonnet-4-5-20250929",
        "cost_per_1k_input": 0.003,
        "cost_per_1k_output": 0.015,
        "quality": "excellent",
    },
    "local": {
        "model": "qwen-3b",
        "cost_per_1k_input": 0,
        "cost_per_1k_output": 0,
        "quality": "basic",
    },
}
