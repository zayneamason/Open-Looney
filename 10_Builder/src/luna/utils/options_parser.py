"""Parse [OPTIONS] blocks from LLM responses."""

import re

_OPTIONS_PATTERN = re.compile(
    r'\[OPTIONS\]\s*\n((?:\(\d+\)\s*.+\n?)+)\[/OPTIONS\]\s*$',
    re.MULTILINE
)


def extract_options(text: str) -> tuple[str, list[dict] | None]:
    """Extract [OPTIONS] block from response text.

    Returns (cleaned_text, options_list_or_None).
    Options: [{"label": "...", "value": "..."}]
    """
    match = _OPTIONS_PATTERN.search(text)
    if not match:
        return text, None

    options = []
    for line in match.group(1).strip().split('\n'):
        line = line.strip()
        m = re.match(r'\((\d+)\)\s*(.+)', line)
        if m:
            label = m.group(2).strip()
            options.append({"label": label, "value": label})

    if not options:
        return text, None

    cleaned = text[:match.start()].rstrip()
    return cleaned, options


def build_options_widget(options: list[dict], prompt: str = None) -> dict:
    """Build an options widget dict for frontend consumption."""
    return {
        "type": "options",
        "skill": None,
        "data": {
            "prompt": prompt,
            "options": options,
            "style": "buttons",
        },
        "latex": None,
    }
