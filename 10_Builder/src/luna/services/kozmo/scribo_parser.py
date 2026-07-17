"""
SCRIBO Format Parser

Handles the .scribo document format:
- YAML frontmatter between --- delimiters
- Free-form body (prose + inline Fountain elements)
- [[VISUAL]] annotation extraction

This module does NOT know about projects or directories.
It works with raw text content.
"""

import re
import yaml
from typing import Tuple, Dict, List, Optional

from .types import ScriboFrontmatter


# =============================================================================
# [[VISUAL]] Annotation Parser
# =============================================================================


VISUAL_PATTERN = re.compile(
    r'\[\[VISUAL\s+'
    r'(\d+:\d{2})-(\d+:\d{2})'  # timecodes: start-end
    r'\s*[—–\-]\s*'              # separator (em dash, en dash, or hyphen)
    r'(.+?)'                      # prompt text
    r'\]\]',
    re.DOTALL,
)


def _timecode_to_seconds(tc: str) -> float:
    """Convert 'M:SS' or 'MM:SS' timecode to seconds."""
    parts = tc.split(":")
    return int(parts[0]) * 60 + int(parts[1])


def extract_visual_annotations(body: str) -> List[Dict]:
    """
    Extract [[VISUAL]] annotations from scene body.

    Standard syntax:
        [[VISUAL {start_time}-{end_time} — {prompt_text}]]

    Returns list of dicts:
        {
            "start_time": "1:19",
            "end_time": "1:27",
            "start_seconds": 79.0,
            "end_seconds": 87.0,
            "duration": 8.0,
            "prompt": "A single file icon, luminous, floating in dark space.",
            "raw": "[[VISUAL 1:19-1:27 — A single file icon...]]"
        }
    """
    results = []
    seen = set()

    for match in VISUAL_PATTERN.finditer(body):
        start_tc = match.group(1)
        end_tc = match.group(2)
        prompt = match.group(3).strip()

        # Deduplicate by timecode + prompt hash
        dedup_key = f"{start_tc}|{end_tc}|{prompt}"
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        start_s = _timecode_to_seconds(start_tc)
        end_s = _timecode_to_seconds(end_tc)

        results.append({
            "start_time": start_tc,
            "end_time": end_tc,
            "start_seconds": start_s,
            "end_seconds": end_s,
            "duration": end_s - start_s,
            "prompt": prompt,
            "raw": match.group(0),
        })

    return results


# =============================================================================
# Frontmatter / Body Split
# =============================================================================


def parse_scribo(text: str) -> Tuple[ScriboFrontmatter, str]:
    """
    Split a .scribo file into frontmatter + body.

    Frontmatter: YAML between --- delimiters at the start of the file.
    Body: everything after the closing ---.

    Args:
        text: Raw .scribo file content

    Returns:
        (ScriboFrontmatter, body_string)

    Raises:
        ValueError: If frontmatter is malformed YAML
    """
    text = text.strip()

    if not text.startswith("---"):
        # No frontmatter — return defaults + entire text as body
        return ScriboFrontmatter(), text

    # Find closing ---
    second_marker = text.find("---", 3)
    if second_marker == -1:
        # Only opening --- with no close — treat entire text as body
        return ScriboFrontmatter(), text

    frontmatter_raw = text[3:second_marker].strip()
    body = text[second_marker + 3:].strip()

    # Parse YAML frontmatter
    try:
        data = yaml.safe_load(frontmatter_raw)
    except yaml.YAMLError as e:
        raise ValueError(f"Malformed frontmatter YAML: {e}")

    if not data or not isinstance(data, dict):
        return ScriboFrontmatter(), body

    fm = ScriboFrontmatter(
        type=data.get("type", "scene"),
        container=data.get("container"),
        characters_present=data.get("characters_present", []),
        location=data.get("location"),
        time=data.get("time"),
        status=data.get("status", "draft"),
        tags=data.get("tags", []),
        audio_file=data.get("audio_file"),
        audio_duration=data.get("audio_duration"),
    )

    return fm, body


def serialize_scribo(frontmatter: ScriboFrontmatter, body: str) -> str:
    """
    Combine frontmatter + body back into .scribo file format.

    Args:
        frontmatter: Parsed frontmatter model
        body: Body text

    Returns:
        Complete .scribo file content string
    """
    # Build frontmatter dict, omitting None/empty values
    data = {}
    data["type"] = frontmatter.type

    if frontmatter.container:
        data["container"] = frontmatter.container
    if frontmatter.characters_present:
        data["characters_present"] = frontmatter.characters_present
    if frontmatter.location:
        data["location"] = frontmatter.location
    if frontmatter.time:
        data["time"] = frontmatter.time

    data["status"] = frontmatter.status

    if frontmatter.tags:
        data["tags"] = frontmatter.tags
    if frontmatter.audio_file:
        data["audio_file"] = frontmatter.audio_file
    if frontmatter.audio_duration:
        data["audio_duration"] = frontmatter.audio_duration

    fm_str = yaml.dump(
        data,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    ).strip()

    return f"---\n{fm_str}\n---\n\n{body}\n"


# =============================================================================
# Fountain Element Extraction
# =============================================================================

# Fountain scene header prefixes
_SCENE_PREFIXES = re.compile(
    r"^(INT\.|EXT\.|INT\./EXT\.|I/E\.)\s*",
    re.IGNORECASE,
)

# Character name: ALL CAPS on its own line, optionally with (V.O.) etc.
# Must have at least 2 alpha chars, not be a transition (ending with TO:)
_CHARACTER_RE = re.compile(
    r"^([A-Z][A-Z .'\-]{1,}?)(?:\s*\(.*?\))?\s*$"
)

# Parenthetical: line starting with (
_PARENTHETICAL_RE = re.compile(r"^\(.*?\)\s*$")


def extract_fountain_elements(body: str) -> Dict:
    """
    Extract Fountain elements from mixed prose/Fountain body.

    Returns dict with:
        characters: list of ALL CAPS character names found
        dialogue_counts: dict of character -> line count
        scene_headers: list of scene header strings
        parentheticals: list of (character, parenthetical) tuples
    """
    lines = body.split("\n")
    characters = set()
    dialogue_counts: Dict[str, int] = {}
    scene_headers: List[str] = []
    parentheticals: List[Tuple[str, str]] = []

    current_character: Optional[str] = None
    in_dialogue = False

    for i, line in enumerate(lines):
        stripped = line.strip()

        if not stripped:
            # Blank line ends dialogue block
            in_dialogue = False
            current_character = None
            continue

        # Check scene header
        if _SCENE_PREFIXES.match(stripped):
            scene_headers.append(stripped)
            in_dialogue = False
            current_character = None
            continue

        # Check if this is a transition (e.g., "CUT TO:")
        if stripped.endswith("TO:") and stripped == stripped.upper():
            in_dialogue = False
            current_character = None
            continue

        # Check character name (ALL CAPS, not a scene header, not a transition)
        if not in_dialogue and _CHARACTER_RE.match(stripped):
            # Additional checks: not a single short word, has alpha chars
            name_part = re.sub(r"\s*\(.*?\)\s*$", "", stripped).strip()
            if len(name_part) >= 2 and name_part.isascii() and name_part == name_part.upper():
                # Exclude common non-character ALL CAPS (FADE IN, CUT TO, etc.)
                if name_part not in {"FADE IN", "FADE OUT", "CUT TO", "THE END",
                                     "DISSOLVE TO", "SMASH CUT TO", "CONTINUED"}:
                    current_character = name_part
                    characters.add(current_character)
                    in_dialogue = True
                    if current_character not in dialogue_counts:
                        dialogue_counts[current_character] = 0
                    continue

        # Check parenthetical
        if in_dialogue and _PARENTHETICAL_RE.match(stripped):
            if current_character:
                parentheticals.append((current_character, stripped))
            continue

        # If we're in dialogue, count this line
        if in_dialogue and current_character:
            dialogue_counts[current_character] = dialogue_counts.get(current_character, 0) + 1

    return {
        "characters": sorted(characters),
        "dialogue_counts": dialogue_counts,
        "scene_headers": scene_headers,
        "parentheticals": parentheticals,
    }


# =============================================================================
# Entity Reference Detection
# =============================================================================


def extract_entity_references(body: str, entities: List[Dict]) -> List[Dict]:
    """
    Extract entity references from body text.

    Detects:
    - Explicit @mentions: @CharacterName, @LocationName
    - Implicit mentions: entity names mentioned without @ prefix

    Args:
        body: Scene body text
        entities: List of entity dicts with 'name', 'slug', 'type', 'color'

    Returns:
        List of dicts with:
            entity_slug: str
            entity_name: str
            entity_type: str
            mention_type: 'explicit' | 'implicit'
            positions: List[int] (character positions in body)
    """
    references = {}

    # Build entity lookup by name (case-insensitive)
    entity_map = {e['name'].lower(): e for e in entities}

    # Pattern 1: Explicit @mentions
    explicit_pattern = re.compile(r'@([A-Z][A-Za-z\s]+)')
    for match in explicit_pattern.finditer(body):
        entity_name = match.group(1).strip()
        entity_key = entity_name.lower()

        if entity_key in entity_map:
            entity = entity_map[entity_key]
            slug = entity['slug']

            if slug not in references:
                references[slug] = {
                    'entity_slug': slug,
                    'entity_name': entity['name'],
                    'entity_type': entity['type'],
                    'mention_type': 'explicit',
                    'positions': []
                }
            references[slug]['positions'].append(match.start())

    # Pattern 2: Implicit mentions (entity name appears in text)
    for entity in entities:
        # Use word boundaries to avoid partial matches
        # e.g., "Sam" shouldn't match "Samuel" or "same"
        pattern = re.compile(r'\b' + re.escape(entity['name']) + r'\b', re.IGNORECASE)

        for match in pattern.finditer(body):
            # Skip if this position is already an @mention
            is_explicit = any(
                match.start() - 1 >= 0 and body[match.start() - 1] == '@'
                for _ in [1]  # Just need to check once
            )
            if is_explicit:
                continue

            slug = entity['slug']

            # Don't overwrite explicit mentions with implicit
            if slug in references and references[slug]['mention_type'] == 'explicit':
                references[slug]['positions'].append(match.start())
            elif slug not in references:
                references[slug] = {
                    'entity_slug': slug,
                    'entity_name': entity['name'],
                    'entity_type': entity['type'],
                    'mention_type': 'implicit',
                    'positions': [match.start()]
                }
            else:
                references[slug]['positions'].append(match.start())

    return list(references.values())


# =============================================================================
# Word Count
# =============================================================================


def word_count(body: str) -> int:
    """
    Word count excluding Fountain character names and parentheticals.
    Counts prose + dialogue text.

    Args:
        body: Body text (no frontmatter)

    Returns:
        Word count integer
    """
    lines = body.split("\n")
    counted_words = 0

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        # Skip scene headers
        if _SCENE_PREFIXES.match(stripped):
            continue

        # Skip character names (ALL CAPS lines)
        if _CHARACTER_RE.match(stripped):
            name_part = re.sub(r"\s*\(.*?\)\s*$", "", stripped).strip()
            if len(name_part) >= 2 and name_part == name_part.upper():
                if name_part not in {"FADE IN", "FADE OUT", "CUT TO", "THE END",
                                     "DISSOLVE TO", "SMASH CUT TO", "CONTINUED"}:
                    continue

        # Skip parentheticals
        if _PARENTHETICAL_RE.match(stripped):
            continue

        # Skip transitions
        if stripped.endswith("TO:") and stripped == stripped.upper():
            continue

        # Count words in this line
        counted_words += len(stripped.split())

    return counted_words
