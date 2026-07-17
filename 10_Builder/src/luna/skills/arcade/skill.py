"""ArcadeSkill — launch and manage Pyxel arcade games."""

import re
import logging
from ..base import Skill, SkillResult
from .game_registry import (
    list_available, get_game, resolve_game_from_query,
    get_games_dir, game_info_dict,
)
from .process_manager import ProcessManager

logger = logging.getLogger(__name__)


def _parse_action(query: str) -> str:
    """Detect sub-action from query: launch, stop, list, status."""
    q = query.lower().strip()
    if q.startswith("/arcade"):
        rest = q[len("/arcade"):].strip()
        if rest.startswith("stop") or rest.startswith("quit") or rest.startswith("kill"):
            return "stop"
        if rest.startswith("list") or rest.startswith("games"):
            return "list"
        if rest.startswith("status"):
            return "status"
        return "launch"
    if re.search(r"\b(stop|quit|close|exit|end)\b.{0,15}\b(game|arcade)\b", q):
        return "stop"
    if re.search(r"\b(list|show|what).{0,15}\b(games?|arcade)\b", q):
        return "list"
    return "launch"


class ArcadeSkill(Skill):
    name = "arcade"
    description = "Launch retro arcade games from Luna's collection"
    triggers = [
        r"\b(play|launch|start|run)\b.{0,20}\b(game|arcade)\b",
        r"\bsteve j savage\b",
        r"\barcade\b",
        r"\blet'?s play\b",
    ]

    def __init__(self, config: dict = None):
        self._config = config or {}

    def is_available(self) -> bool:
        try:
            import pyxel  # noqa: F401
            return True
        except ImportError:
            return False

    async def execute(self, query: str, context: dict) -> SkillResult:
        pm = ProcessManager.get()
        action = _parse_action(query)

        if action == "stop":
            stopped = await pm.stop()
            msg = "Game stopped." if stopped else "No game is running."
            return SkillResult(
                success=True, skill_name=self.name,
                result_str=msg,
                data={"action": "stopped", "was_running": stopped},
            )

        if action == "list":
            games = list_available()
            return SkillResult(
                success=True, skill_name=self.name,
                result_str=f"{len(games)} game(s) available.",
                data={
                    "action": "listed",
                    "available_games": [game_info_dict(g) for g in games],
                },
            )

        if action == "status":
            status = pm.status()
            if status:
                return SkillResult(
                    success=True, skill_name=self.name,
                    result_str=f"{status['title']} running ({status['running_seconds']}s).",
                    data={"action": "status", "game": status},
                )
            return SkillResult(
                success=True, skill_name=self.name,
                result_str="No game is currently running.",
                data={"action": "status", "game": None},
            )

        # --- Launch ---
        # Check if already running
        current = pm.status()
        if current:
            return SkillResult(
                success=True, skill_name=self.name,
                result_str=f"{current['title']} is already running (PID {current['pid']}).",
                data={"action": "already_running", "game": current},
            )

        game = resolve_game_from_query(query)
        if not game:
            available = list_available()
            if not available:
                return SkillResult(
                    success=False, skill_name=self.name,
                    fallthrough=True,
                    error="No arcade games installed.",
                )
            return SkillResult(
                success=False, skill_name=self.name,
                fallthrough=True,
                error=f"Couldn't identify which game. Available: {', '.join(g.title for g in available)}",
            )

        game_path = get_games_dir() / game.file
        if not game_path.exists():
            return SkillResult(
                success=False, skill_name=self.name,
                fallthrough=True,
                error=f"Game file not found: {game.file}",
            )

        try:
            gp = await pm.launch(game.id, game_path, game.title)
            return SkillResult(
                success=True, skill_name=self.name,
                result_str=f"Launched {game.title}!",
                data={
                    "action": "launched",
                    "game": game.title,
                    "game_id": game.id,
                    "pid": gp.pid,
                    "controls": game.controls,
                },
            )
        except Exception as e:
            logger.warning("[ARCADE] Launch failed: %s", e)
            return SkillResult(
                success=False, skill_name=self.name,
                fallthrough=True, error=str(e),
            )

    def narration_hint(self, result: SkillResult) -> str:
        return (
            "An arcade game was launched in a separate window. "
            "Be enthusiastic and playful — mention the game by name. "
            "Briefly describe the controls. Keep it fun, 80s arcade energy."
        )
