"""
Eden adapter — high-level interface for Luna Engine.

Provides create_image, create_video, chat_with_agent, etc.
Handles task lifecycle (create -> poll -> result) transparently.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from .client import EdenClient, EdenAPIError
from .config import EdenConfig
from .types import (
    Agent,
    Creation,
    CreationsPage,
    MediaType,
    Session,
    SessionBudget,
    SessionCreateRequest,
    Task,
    TaskCreateRequest,
    TaskStatus,
)

logger = logging.getLogger("luna.eden")


class EdenAdapter:
    """
    High-level Eden API adapter.

    Usage:
        config = EdenConfig.load()
        adapter = EdenAdapter(config)

        async with adapter:
            result = await adapter.create_image("a cyberpunk cityscape")
            print(result.first_output_url)
    """

    def __init__(self, config: Optional[EdenConfig] = None):
        self.config = config or EdenConfig.load()
        self._client: Optional[EdenClient] = None

    async def __aenter__(self) -> "EdenAdapter":
        self._client = EdenClient(self.config)
        await self._client.__aenter__()
        return self

    async def __aexit__(self, *exc) -> None:
        if self._client:
            await self._client.__aexit__(*exc)
            self._client = None

    @property
    def client(self) -> EdenClient:
        if self._client is None:
            raise RuntimeError("EdenAdapter not initialized. Use 'async with'.")
        return self._client

    # ── Task Operations ────────────────────────────────────────

    async def create_task(
        self,
        prompt: str,
        media_type: MediaType = MediaType.IMAGE,
        tool: str = "create",
        public: bool = False,
        extra_args: Optional[dict[str, Any]] = None,
    ) -> Task:
        """
        Create a generation task and return immediately.
        Use poll_task() or wait_for_task() to get the result.
        """
        args: dict[str, Any] = {"prompt": prompt}
        if media_type == MediaType.VIDEO:
            args["output"] = "video"
        if extra_args:
            args.update(extra_args)

        request = TaskCreateRequest(tool=tool, args=args, makePublic=public)
        data = await self.client.post(
            "/v2/tasks/create",
            json=request.model_dump(by_alias=True),
        )

        task_data = data.get("task", data)
        return Task.model_validate(task_data)

    async def poll_task(self, task_id: str) -> Task:
        """Poll a task once and return current state."""
        data = await self.client.get(f"/v2/tasks/{task_id}")
        task_data = data.get("task", data)
        return Task.model_validate(task_data)

    async def wait_for_task(
        self,
        task_id: str,
        poll_interval: Optional[float] = None,
        max_attempts: Optional[int] = None,
    ) -> Task:
        """
        Poll a task until completion or failure.
        Returns the final Task state.
        Raises EdenAPIError if max attempts exceeded.
        """
        interval = poll_interval or self.config.poll_interval_seconds
        attempts = max_attempts or self.config.poll_max_attempts

        for attempt in range(attempts):
            task = await self.poll_task(task_id)

            if task.is_terminal:
                if task.is_failed:
                    logger.warning(f"Eden task {task_id} failed: {task.error}")
                else:
                    logger.info(f"Eden task {task_id} completed")
                return task

            # Back off slightly over time
            wait = interval * (self.config.poll_backoff_factor ** min(attempt, 10))
            await asyncio.sleep(wait)

        raise EdenAPIError(0, f"Task {task_id} did not complete after {attempts} polls")

    # ── Convenience Methods ────────────────────────────────────

    async def create_image(
        self,
        prompt: str,
        wait: bool = True,
        **kwargs,
    ) -> Task:
        """Create an image. If wait=True, blocks until complete."""
        task = await self.create_task(prompt, MediaType.IMAGE, **kwargs)
        if wait:
            return await self.wait_for_task(task.id)
        return task

    async def create_video(
        self,
        prompt: str,
        wait: bool = True,
        **kwargs,
    ) -> Task:
        """Create a video. If wait=True, blocks until complete."""
        task = await self.create_task(prompt, MediaType.VIDEO, **kwargs)
        if wait:
            return await self.wait_for_task(task.id)
        return task

    # ── Agent Operations ───────────────────────────────────────

    async def list_agents(self) -> list[Agent]:
        """List available Eden agents."""
        data = await self.client.get("/v2/agents")
        docs = data.get("docs", [])
        return [Agent.model_validate(a) for a in docs]

    async def get_agent(self, agent_id: str) -> Optional[Agent]:
        """Get a specific agent by ID."""
        try:
            data = await self.client.get(f"/v2/agents/{agent_id}")
            agent_data = data.get("agent", data)
            return Agent.model_validate(agent_data)
        except EdenAPIError as e:
            if e.status_code == 404:
                return None
            raise

    # ── Session Operations ─────────────────────────────────────

    async def create_session(
        self,
        agent_ids: list[str],
        content: Optional[str] = None,
        title: Optional[str] = None,
        manna_budget: Optional[float] = None,
    ) -> str:
        """
        Create a new chat session with one or more agents.
        Returns session_id.
        """
        budget = SessionBudget(
            manna_budget=manna_budget or self.config.default_manna_budget,
            turn_budget=self.config.default_turn_budget,
        )

        request = SessionCreateRequest(
            agent_ids=agent_ids,
            content=content,
            title=title,
            budget=budget,
        )

        data = await self.client.post(
            "/v2/sessions",
            json=request.model_dump(exclude_none=True),
        )
        return data["session_id"]

    async def send_message(
        self,
        session_id: str,
        content: str,
        attachments: Optional[list[str]] = None,
    ) -> str:
        """Send a message to an existing session. Returns session_id."""
        payload: dict[str, Any] = {
            "session_id": session_id,
            "content": content,
        }
        if attachments:
            payload["attachments"] = attachments

        data = await self.client.post("/v2/sessions", json=payload)
        return data["session_id"]

    async def get_session(self, session_id: str) -> Optional[Session]:
        """Get full session state including messages."""
        try:
            data = await self.client.get(f"/v2/sessions/{session_id}")
            session_data = data.get("session", data)
            return Session.model_validate(session_data)
        except EdenAPIError as e:
            if e.status_code == 404:
                return None
            raise

    # ── Creation Gallery ───────────────────────────────────────

    async def get_creations(
        self,
        limit: int = 20,
        cursor: Optional[str] = None,
        media_type: Optional[MediaType] = None,
    ) -> CreationsPage:
        """Fetch creations from the gallery."""
        params: dict[str, Any] = {"limit": str(limit)}
        if cursor:
            params["cursor"] = cursor
        if media_type:
            params["filter"] = f"output_type;{media_type.value}"

        data = await self.client.get("/v2/feed-cursor/creations", params=params)
        return CreationsPage.model_validate(data)

    # ── Health Check ───────────────────────────────────────────

    async def health_check(self) -> bool:
        """Verify Eden API is reachable and key is valid."""
        try:
            await self.client.get("/v2/agents")
            return True
        except Exception as e:
            logger.warning(f"Eden health check failed: {e}")
            return False
