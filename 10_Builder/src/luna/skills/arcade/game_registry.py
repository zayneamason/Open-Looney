"""Game registry — discovers and resolves available arcade games."""

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional


@dataclass
class GameInfo:
    id: str
    title: str
    description: str
    file: str
    resolution: tuple[int, int]
    controls: dict[str, str]


GAMES: dict[str, GameInfo] = {
    "steve_j_savage": GameInfo(
        id="steve_j_savage",
        title="Steve J Savage vs. ALIENS",
        description="Dual machine gun alien defense. Survive 120 seconds of escalating waves.",
        file="steve_j_savage.py",
        resolution=(256, 144),
        controls={
            "SPACE": "Fire burst",
            "LEFT": "Reverse spin direction",
            "RIGHT": "Activate SAVAGE MODE",
        },
    ),
    "moon_wisdom": GameInfo(
        id="moon_wisdom",
        title="Moon Wisdom",
        description="Test your cosmic knowledge with Luna's trivia challenge.",
        file="moon_wisdom_chat.py",
        resolution=(256, 144),
        controls={
            "SPACE": "Select answer",
            "LEFT": "Previous",
            "RIGHT": "Next",
        },
    ),
    "rocket_raccoon": GameInfo(
        id="rocket_raccoon",
        title="Rocket Raccoon",
        description="Pilot Luna's cousin through an asteroid field.",
        file="rocket_raccoon.py",
        resolution=(256, 144),
        controls={
            "SPACE": "Boost",
            "LEFT": "Steer left",
            "RIGHT": "Steer right",
        },
    ),
}


def get_games_dir() -> Path:
    return Path(__file__).parent / "games"


def list_available() -> list[GameInfo]:
    games_dir = get_games_dir()
    return [g for g in GAMES.values() if (games_dir / g.file).exists()]


def get_game(game_id: str) -> Optional[GameInfo]:
    return GAMES.get(game_id)


def resolve_game_from_query(query: str) -> Optional[GameInfo]:
    """Fuzzy-match a game from natural language."""
    q = query.lower()
    if any(kw in q for kw in ("savage", "steve", "alien")):
        return GAMES.get("steve_j_savage")
    # Default: first available game
    available = list_available()
    return available[0] if available else None


def list_all() -> list[dict]:
    """Return all games with an 'available' flag (True if .py file exists)."""
    games_dir = get_games_dir()
    return [{**asdict(g), "available": (games_dir / g.file).exists()} for g in GAMES.values()]


def game_info_dict(game: GameInfo) -> dict:
    return asdict(game)
