"""ReadingSkill — document reading via markitdown / pypdf fallback."""

import re
import logging
from pathlib import Path
from ..base import Skill, SkillResult

logger = logging.getLogger(__name__)

_FILE_PATTERN = re.compile(
    r"""(?:["'])?([/~][\w./\- ]+\.(?:pdf|docx|doc|xlsx|pptx|txt|md|yaml|yml|json|py|js|jsx|ts|tsx|csv))(?:["'])?""",
    re.IGNORECASE,
)


def _extract_file_path(query: str) -> str | None:
    """Extract a file path from the query."""
    match = _FILE_PATTERN.search(query)
    if match:
        path = match.group(1)
        # Expand ~ to home directory
        if path.startswith("~"):
            path = str(Path(path).expanduser())
        return path
    return None


def _read_with_markitdown(path: str) -> str | None:
    """Try reading with markitdown."""
    try:
        from markitdown import MarkItDown
        md = MarkItDown()
        result = md.convert(path)
        return result.text_content if result else None
    except ImportError:
        return None
    except Exception as e:
        logger.debug(f"[READING] markitdown failed: {e}")
        return None


def _read_with_pypdf(path: str) -> str | None:
    """Fallback: read PDF with pypdf."""
    if not path.lower().endswith(".pdf"):
        return None
    try:
        from pypdf import PdfReader
        reader = PdfReader(path)
        pages = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                pages.append(text)
        return "\n\n".join(pages) if pages else None
    except ImportError:
        return None
    except Exception as e:
        logger.debug(f"[READING] pypdf failed: {e}")
        return None


def _read_plain_text(path: str) -> str | None:
    """Fallback: read as plain text."""
    try:
        with open(path, "r", errors="replace") as f:
            return f.read()
    except Exception as e:
        logger.debug(f"[READING] plain text read failed: {e}")
        return None


class ReadingSkill(Skill):
    name = "reading"
    description = "Document reading and extraction"
    triggers = [
        r"\b(read|open|parse|extract)\b.{0,30}\.(pdf|docx|doc|xlsx|pptx)\b",
        r"\b(what('?s| is) in|summarize|load)\b.{0,20}\b.{0,10}\.(pdf|doc)\b",
    ]

    def __init__(self, config: dict = None):
        self._config = config or {}
        self._max_file_size_mb = self._config.get("max_file_size_mb", 50)
        self._max_chars = self._config.get("max_chars", 50000)
        self._allowed_extensions = self._config.get(
            "allowed_extensions",
            ["pdf", "docx", "doc", "xlsx", "pptx", "txt", "md"],
        )

    def is_available(self) -> bool:
        # At minimum we can read plain text files
        return True

    async def execute(self, query: str, context: dict) -> SkillResult:
        file_path = _extract_file_path(query)
        if not file_path:
            return SkillResult(
                success=False, skill_name=self.name,
                fallthrough=True, error="No file path found in query",
            )

        path = Path(file_path)
        if not path.exists():
            return SkillResult(
                success=False, skill_name=self.name,
                fallthrough=True, error=f"File not found: {file_path}",
            )

        # Check extension
        ext = path.suffix.lstrip(".").lower()
        if ext not in self._allowed_extensions:
            return SkillResult(
                success=False, skill_name=self.name,
                fallthrough=True, error=f"Extension .{ext} not allowed",
            )

        # Check file size
        size_mb = path.stat().st_size / (1024 * 1024)
        if size_mb > self._max_file_size_mb:
            return SkillResult(
                success=False, skill_name=self.name,
                fallthrough=True, error=f"File too large ({size_mb:.1f}MB > {self._max_file_size_mb}MB)",
            )

        # Try reading in order: markitdown → pypdf → plain text
        text = _read_with_markitdown(file_path)
        if text is None:
            text = _read_with_pypdf(file_path)
        if text is None:
            text = _read_plain_text(file_path)
        if text is None:
            logger.warning("[READING] Could not extract text from file: %s", file_path)
            return SkillResult(
                success=False, skill_name=self.name,
                fallthrough=True, error="Could not extract text from file",
            )

        # Truncate if needed
        char_count = len(text)
        if char_count > self._max_chars:
            text = text[:self._max_chars]

        preview = text[:500]
        file_name = path.name

        return SkillResult(
            success=True,
            skill_name=self.name,
            result=text,
            result_str=f"Read {file_name}: {char_count:,} characters extracted",
            data={
                "file_path": str(file_path),
                "file_name": file_name,
                "char_count": char_count,
                "content": text,
                "preview": preview,
            },
        )

    def narration_hint(self, result: SkillResult) -> str:
        return (
            "A document was read and its content is shown in the widget below. "
            "Summarize the key points briefly. Ask if the user wants to explore specific sections."
        )
