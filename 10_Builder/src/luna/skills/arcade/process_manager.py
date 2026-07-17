"""Subprocess manager for arcade games — one game at a time."""

import asyncio
import logging
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Optional

logger = logging.getLogger(__name__)


@dataclass
class GameProcess:
    game_id: str
    title: str
    process: asyncio.subprocess.Process
    started_at: float
    pid: int


class ProcessManager:
    """Singleton that manages one running game subprocess."""

    _instance: ClassVar[Optional["ProcessManager"]] = None
    _current: Optional[GameProcess] = None

    @classmethod
    def get(cls) -> "ProcessManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def launch(self, game_id: str, game_path: Path, title: str) -> GameProcess:
        """Launch a game as a subprocess. Stops any existing game first."""
        if self._current and self._current.process.returncode is None:
            await self.stop()

        env = os.environ.copy()
        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(game_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        self._current = GameProcess(
            game_id=game_id,
            title=title,
            process=proc,
            started_at=time.time(),
            pid=proc.pid,
        )
        logger.info("[ARCADE] Launched %s (PID %d)", title, proc.pid)
        return self._current

    async def stop(self) -> bool:
        """Terminate the running game."""
        if self._current and self._current.process.returncode is None:
            logger.info("[ARCADE] Stopping %s (PID %d)", self._current.title, self._current.pid)
            self._current.process.terminate()
            try:
                await asyncio.wait_for(self._current.process.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                self._current.process.kill()
            self._current = None
            return True
        self._current = None
        return False

    def status(self) -> Optional[dict]:
        """Return current game status or None if nothing running."""
        if self._current and self._current.process.returncode is None:
            return {
                "game_id": self._current.game_id,
                "title": self._current.title,
                "pid": self._current.pid,
                "running_seconds": round(time.time() - self._current.started_at, 1),
            }
        self._current = None
        return None
