"""
Eden.art API type definitions.

Pydantic models for Eden REST API request/response payloads.
Reference: Eden_Project/hello-eden/src/lib/eden.ts
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# ── Enums ──────────────────────────────────────────────────────

class TaskStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class MediaType(str, Enum):
    IMAGE = "image"
    VIDEO = "video"


class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    EDEN = "eden"


# ── Media / Creation ──────────────────────────────────────────

class MediaAttributes(BaseModel):
    mime_type: Optional[str] = Field(None, alias="mimeType")
    width: Optional[int] = None
    height: Optional[int] = None
    aspect_ratio: Optional[float] = Field(None, alias="aspectRatio")

    model_config = {"populate_by_name": True}


class TaskOutput(BaseModel):
    """Single output from a completed task."""
    url: Optional[str] = None
    uri: Optional[str] = None
    filename: Optional[str] = None
    media_attributes: Optional[MediaAttributes] = Field(None, alias="mediaAttributes")

    model_config = {"populate_by_name": True}

    @property
    def resolved_url(self) -> Optional[str]:
        return self.url or self.uri


class TaskResult(BaseModel):
    """Result block from task polling."""
    output: list[TaskOutput] = Field(default_factory=list)


# ── Tasks ─────────────────────────────────────────────────────

class TaskCreateRequest(BaseModel):
    """POST /v2/tasks/create"""
    tool: str = "create"
    args: dict[str, Any] = Field(default_factory=dict)
    make_public: bool = Field(False, alias="makePublic")

    model_config = {"populate_by_name": True}


class Task(BaseModel):
    """Task response from Eden API."""
    id: str = Field(alias="_id")
    status: TaskStatus = TaskStatus.PENDING
    result: Optional[list[TaskResult]] = None
    error: Optional[str] = None

    model_config = {"populate_by_name": True}

    @property
    def is_complete(self) -> bool:
        return self.status == TaskStatus.COMPLETED

    @property
    def is_failed(self) -> bool:
        return self.status == TaskStatus.FAILED

    @property
    def is_terminal(self) -> bool:
        return self.is_complete or self.is_failed

    @property
    def first_output_url(self) -> Optional[str]:
        """Convenience: URL of the first output, if any."""
        if self.result and len(self.result) > 0 and self.result[0].output:
            return self.result[0].output[0].resolved_url
        return None


# ── Agents ────────────────────────────────────────────────────

class AgentModel(BaseModel):
    """LoRA model attached to an agent."""
    lora: str
    use_when: Optional[str] = None


class AgentSuggestion(BaseModel):
    label: str
    prompt: str


class Agent(BaseModel):
    """Eden agent definition."""
    id: str = Field(alias="_id")
    name: str
    description: Optional[str] = None
    image: Optional[str] = None
    persona: Optional[str] = None
    greeting: Optional[str] = None
    models: list[AgentModel] = Field(default_factory=list)
    suggestions: list[AgentSuggestion] = Field(default_factory=list)
    tools: dict[str, bool] = Field(default_factory=dict)
    public: bool = False
    owner: Optional[dict[str, Any]] = None
    created_at: Optional[datetime] = Field(None, alias="createdAt")

    model_config = {"populate_by_name": True}


# ── Sessions ──────────────────────────────────────────────────

class SessionBudget(BaseModel):
    manna_budget: Optional[float] = None
    token_budget: Optional[int] = None
    turn_budget: Optional[int] = None
    tokens_spent: Optional[int] = None
    manna_spent: Optional[float] = None
    turns_spent: Optional[int] = None


class ToolCall(BaseModel):
    """Tool call within a session message."""
    id: str
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    status: Optional[str] = None
    result: Optional[list[dict[str, Any]]] = None
    error: Optional[Any] = None


class SessionMessage(BaseModel):
    """Single message in a session."""
    id: str = Field(alias="_id")
    role: MessageRole
    content: str = ""
    agent_id: Optional[str] = None
    thinking: Optional[str] = None
    tool_calls: Optional[list[ToolCall]] = None
    attachments: Optional[list[str]] = None
    created_at: Optional[datetime] = Field(None, alias="createdAt")

    model_config = {"populate_by_name": True}


class SessionCreateRequest(BaseModel):
    """POST /v2/sessions — create new session."""
    agent_ids: list[str]
    content: Optional[str] = None
    scenario: Optional[str] = None
    budget: Optional[SessionBudget] = None
    title: Optional[str] = None


class SessionMessageRequest(BaseModel):
    """POST /v2/sessions — send message to existing session."""
    session_id: str
    content: str
    attachments: Optional[list[str]] = None
    agent_ids: Optional[list[str]] = None


class Session(BaseModel):
    """Full session state."""
    id: str = Field(alias="_id")
    agent_ids: list[str] = Field(default_factory=list)
    messages: list[SessionMessage] = Field(default_factory=list)
    budget: Optional[SessionBudget] = None
    title: Optional[str] = None
    status: str = "active"
    created_at: Optional[datetime] = Field(None, alias="createdAt")

    model_config = {"populate_by_name": True}


# ── Creations (Gallery) ──────────────────────────────────────

class Creation(BaseModel):
    """A creation in Eden's gallery."""
    id: str = Field(alias="_id")
    uri: Optional[str] = None
    url: Optional[str] = None
    thumbnail: Optional[str] = None
    media_attributes: Optional[MediaAttributes] = Field(None, alias="mediaAttributes")
    prompt: Optional[str] = None
    tool: Optional[str] = None
    like_count: int = Field(0, alias="likeCount")
    created_at: Optional[datetime] = Field(None, alias="createdAt")

    model_config = {"populate_by_name": True}

    @property
    def resolved_url(self) -> Optional[str]:
        return self.url or self.uri


class CreationsPage(BaseModel):
    """Paginated creations response."""
    docs: list[Creation] = Field(default_factory=list)
    next_cursor: Optional[str] = Field(None, alias="nextCursor")
    has_more: bool = False

    model_config = {"populate_by_name": True}
