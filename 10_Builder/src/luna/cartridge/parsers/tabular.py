"""Shared helpers for tabular cartridge parsers."""

from __future__ import annotations

import re
from typing import Any


_CELL_REF_RE = re.compile(
    r"(?:(?:'([^']+)'|([A-Za-z0-9_ .]+))!)?(\$?[A-Z]{1,3}\$?\d+(?::\$?[A-Z]{1,3}\$?\d+)?)"
)
_FUNCTION_RE = re.compile(r"\b([A-Z][A-Z0-9.]*)\s*\(", re.IGNORECASE)


def make_node(
    type: str,
    content: str | None,
    parent_idx: int | None,
    position: int,
    meta: dict | None = None,
) -> dict:
    return {
        "type": type,
        "content": content,
        "parent_idx": parent_idx,
        "position": position,
        "meta": meta,
    }


def column_letter(index: int) -> str:
    """Return Excel-style column letters for a 1-based column index."""
    if index < 1:
        raise ValueError("column index must be 1-based")
    letters = []
    while index:
        index, remainder = divmod(index - 1, 26)
        letters.append(chr(65 + remainder))
    return "".join(reversed(letters))


def stringify_value(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def value_type(value: Any) -> str:
    if value is None:
        return "blank"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    return "text"


def header_names(values: list[Any]) -> tuple[list[str], bool]:
    """Return usable column names and whether the source row was valid as-is."""
    names = [stringify_value(v).strip() for v in values]
    normalized = [re.sub(r"\s+", " ", name).casefold() for name in names]
    nonblank = [name for name in names if name]
    text_like = [name for name in nonblank if re.search(r"[A-Za-z]", name)]
    valid = (
        bool(names)
        and len(nonblank) == len(names)
        and len(set(normalized)) == len(normalized)
        and len(text_like) >= max(1, len(names) // 2)
    )
    if valid:
        return names, True
    return [f"Column {column_letter(i)}" for i in range(1, len(names) + 1)], False


def row_summary(headers: list[str], row_values: list[Any]) -> str:
    parts = []
    for header, value in zip(headers, row_values):
        text = stringify_value(value).strip()
        if text:
            parts.append(f"{header}: {text}")
    return " | ".join(parts)


def formula_refs(formula: str | None, current_sheet: str | None = None) -> list[str]:
    if not formula or not formula.startswith("="):
        return []
    refs: list[str] = []
    for quoted_sheet, bare_sheet, coord in _CELL_REF_RE.findall(formula):
        sheet = quoted_sheet or bare_sheet or current_sheet
        clean_coord = coord.replace("$", "")
        refs.append(f"{sheet}!{clean_coord}" if sheet else clean_coord)
    return refs


def formula_functions(formula: str | None) -> list[str]:
    if not formula or not formula.startswith("="):
        return []
    return sorted({match.group(1).upper() for match in _FUNCTION_RE.finditer(formula)})

