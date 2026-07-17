"""
KOZMO Fountain Screenplay Parser

Parses .fountain format screenplays into structured scene data.

Fountain is a plain-text markup format for screenwriting:
- Scene headers: INT./EXT. lines
- Character names: ALL CAPS lines before dialogue
- Dialogue: Lines after character name
- Action: Everything else
- Transitions: Lines ending in TO: (CUT TO:, FADE TO:)

This module extracts:
- Scene list with headers
- Characters present per scene
- Dialogue counts per character
- Scene locations and time-of-day

Reference: https://fountain.io/syntax
"""

import re
from pathlib import Path
from typing import Optional, List, Dict, Tuple

from .types import FountainScene, FountainDocument


# =============================================================================
# Fountain Syntax Patterns
# =============================================================================

# Scene headers: INT. or EXT. (or INT/EXT.)
SCENE_HEADER_RE = re.compile(
    r"^(INT\.|EXT\.|INT\./EXT\.|INT/EXT\.)\s+(.+)$",
    re.IGNORECASE,
)

# Character name: ALL CAPS line (may have (V.O.) or (O.S.) suffix)
CHARACTER_RE = re.compile(
    r"^([A-Z][A-Z\s\-'\.]+?)(\s*\(.*\))?\s*$"
)

# Transition: ends with TO: (e.g., CUT TO:, FADE TO:)
TRANSITION_RE = re.compile(r"^[A-Z\s]+TO:\s*$")

# Forced scene header: line starting with .
FORCED_SCENE_RE = re.compile(r"^\.\s*(.+)$")

# Parenthetical: (wrylies) in dialogue
PARENTHETICAL_RE = re.compile(r"^\(.*\)$")

# Title page key: Key: Value
TITLE_PAGE_RE = re.compile(r"^([A-Za-z\s]+):\s*(.*)$")


# =============================================================================
# Parser
# =============================================================================


def parse_fountain(text: str) -> FountainDocument:
    """
    Parse Fountain-format screenplay text into structured document.

    Args:
        text: Raw Fountain screenplay text

    Returns:
        FountainDocument with scenes, characters, and metadata
    """
    lines = text.split("\n")
    scenes: List[FountainScene] = []
    all_characters: set = set()
    title_page: Dict[str, str] = {}

    current_scene: Optional[FountainScene] = None
    current_character: Optional[str] = None
    in_title_page = True
    prev_line_blank = True

    for line in lines:
        stripped = line.strip()

        # Title page parsing (before first blank line after content)
        if in_title_page:
            if not stripped:
                if title_page:
                    in_title_page = False
                continue
            match = TITLE_PAGE_RE.match(stripped)
            if match:
                title_page[match.group(1).strip()] = match.group(2).strip()
                continue
            else:
                in_title_page = False

        # Empty line
        if not stripped:
            current_character = None
            prev_line_blank = True
            continue

        # Scene header
        scene_match = SCENE_HEADER_RE.match(stripped)
        forced_match = FORCED_SCENE_RE.match(stripped)

        if scene_match or forced_match:
            # Save previous scene
            if current_scene:
                scenes.append(current_scene)

            header = stripped if scene_match else forced_match.group(1)
            location, time_of_day = _parse_scene_header(header)

            current_scene = FountainScene(
                header=header,
                location=location,
                time_of_day=time_of_day,
                characters_present=[],
                has_dialogue={},
            )
            current_character = None
            prev_line_blank = False
            continue

        # Transition (skip)
        if TRANSITION_RE.match(stripped):
            prev_line_blank = False
            continue

        # Parenthetical (skip, part of dialogue)
        if PARENTHETICAL_RE.match(stripped):
            prev_line_blank = False
            continue

        # Character name (must follow blank line)
        if prev_line_blank and current_scene:
            char_match = CHARACTER_RE.match(stripped)
            if char_match and not _is_action(stripped):
                char_name = char_match.group(1).strip()
                current_character = char_name

                # Track character presence
                if char_name not in current_scene.characters_present:
                    current_scene.characters_present.append(char_name)
                all_characters.add(char_name)

                prev_line_blank = False
                continue

        # Dialogue (line after character name)
        if current_character and current_scene:
            current_scene.has_dialogue[current_character] = (
                current_scene.has_dialogue.get(current_character, 0) + 1
            )
            prev_line_blank = False
            continue

        # Action line (anything else)
        current_character = None
        prev_line_blank = False

    # Don't forget the last scene
    if current_scene:
        scenes.append(current_scene)

    return FountainDocument(
        scenes=scenes,
        characters=sorted(all_characters),
        title_page=title_page,
    )


def parse_fountain_file(path: Path) -> FountainDocument:
    """
    Parse Fountain screenplay from file.

    Args:
        path: Path to .fountain file

    Returns:
        FountainDocument

    Raises:
        FileNotFoundError: If file doesn't exist
    """
    if not path.exists():
        raise FileNotFoundError(f"Fountain file not found: {path}")

    text = path.read_text(encoding="utf-8")
    return parse_fountain(text)


# =============================================================================
# Header Parsing
# =============================================================================


def _parse_scene_header(header: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Extract location and time-of-day from scene header.

    Examples:
        "INT. THE CROOKED NAIL - EVENING" → ("THE CROOKED NAIL", "EVENING")
        "EXT. CITY STREETS - DAY" → ("CITY STREETS", "DAY")
        "INT./EXT. CARRIAGE" → ("CARRIAGE", None)

    Args:
        header: Scene header string

    Returns:
        Tuple of (location, time_of_day)
    """
    # Remove INT./EXT. prefix
    text = SCENE_HEADER_RE.sub(r"\2", header).strip()

    # Split on " - " for time of day
    if " - " in text:
        parts = text.rsplit(" - ", 1)
        location = parts[0].strip()
        time_of_day = parts[1].strip()
        return location, time_of_day

    return text, None


def _is_action(text: str) -> bool:
    """
    Heuristic to distinguish action lines from character names.

    Character names are typically short (1-3 words) and ALL CAPS.
    Action lines that happen to be all caps tend to be longer.

    Args:
        text: Line to check

    Returns:
        True if this looks like an action line, not a character name
    """
    words = text.split()

    # Very long all-caps lines are probably action, not character names
    if len(words) > 4:
        return True

    # Lines with punctuation (except periods and hyphens) are likely action
    if any(c in text for c in "!?,;:\""):
        return True

    return False


# =============================================================================
# Utility
# =============================================================================


def extract_characters(doc: FountainDocument) -> List[str]:
    """
    Get all unique characters from document.

    Args:
        doc: Parsed Fountain document

    Returns:
        Sorted list of character names
    """
    return doc.characters


def scenes_for_character(
    doc: FountainDocument, character: str
) -> List[FountainScene]:
    """
    Get all scenes where a character appears.

    Args:
        doc: Parsed Fountain document
        character: Character name (case-sensitive, ALL CAPS)

    Returns:
        List of scenes where character is present
    """
    return [
        scene
        for scene in doc.scenes
        if character in scene.characters_present
    ]


def dialogue_count(doc: FountainDocument) -> Dict[str, int]:
    """
    Get total dialogue line count per character across all scenes.

    Args:
        doc: Parsed Fountain document

    Returns:
        Dict mapping character name to total dialogue line count
    """
    totals: Dict[str, int] = {}
    for scene in doc.scenes:
        for char, count in scene.has_dialogue.items():
            totals[char] = totals.get(char, 0) + count
    return totals
