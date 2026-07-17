"""CSV parser for .lun cartridge builds."""

from __future__ import annotations

import csv
import io
from pathlib import Path

from .base import BaseParser
from .tabular import (
    column_letter,
    header_names,
    make_node,
    row_summary,
    stringify_value,
    value_type,
)


class CSVParser(BaseParser):
    """Parse CSV files into document/section/table/row/cell nodes."""

    def parse(self, source_path: Path) -> list[dict]:
        source_path = Path(source_path)
        text = self._read_text(source_path)
        rows = list(csv.reader(io.StringIO(text, newline="")))
        return self._parse_rows(rows, source_path.stem)

    def _read_text(self, source_path: Path) -> str:
        try:
            return source_path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            return source_path.read_text(encoding="latin-1")

    def _parse_rows(self, raw_rows: list[list[str]], title: str) -> list[dict]:
        nodes: list[dict] = [make_node("document", None, None, 0, {"title": title})]
        root_idx = 0

        first_row_idx = self._first_nonempty_row(raw_rows)
        if first_row_idx is None:
            nodes.append(make_node("section", title, root_idx, 0, {"source_format": "csv"}))
            return nodes

        data_rows = raw_rows[first_row_idx:]
        max_cols = max(len(row) for row in data_rows)
        padded_rows = [row + [""] * (max_cols - len(row)) for row in data_rows]
        headers, headers_from_source = header_names(padded_rows[0])

        section_idx = len(nodes)
        nodes.append(
            make_node(
                "section",
                title,
                root_idx,
                0,
                {"source_format": "csv", "table_count": 1},
            )
        )
        table_idx = len(nodes)
        nodes.append(
            make_node(
                "table",
                None,
                section_idx,
                0,
                {
                    "source_format": "csv",
                    "source_row_start": first_row_idx + 1,
                    "source_row_count": len(padded_rows),
                    "column_count": max_cols,
                    "headers_from_source": headers_from_source,
                    "headers": headers,
                },
            )
        )

        for row_pos, row in enumerate(padded_rows):
            source_row = first_row_idx + row_pos + 1
            is_header = row_pos == 0
            summary = "" if is_header else row_summary(headers, row)
            row_meta = {
                "source_row": source_row,
                "header": is_header,
                "row_summary": summary,
            }
            row_idx = len(nodes)
            nodes.append(make_node("row", None, table_idx, row_pos, row_meta))

            for col_pos, value in enumerate(row):
                col_index = col_pos + 1
                letter = column_letter(col_index)
                content = stringify_value(value)
                nodes.append(
                    make_node(
                        "cell",
                        content,
                        row_idx,
                        col_pos,
                        {
                            "sheet": title,
                            "address": f"{letter}{source_row}",
                            "row": source_row,
                            "column": col_index,
                            "column_letter": letter,
                            "column_name": headers[col_pos],
                            "value": value,
                            "display_value": content,
                            "value_type": value_type(value),
                        },
                    )
                )

        return nodes

    def _first_nonempty_row(self, rows: list[list[str]]) -> int | None:
        for idx, row in enumerate(rows):
            if any(cell.strip() for cell in row):
                return idx
        return None
