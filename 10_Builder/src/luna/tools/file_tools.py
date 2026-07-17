"""
File Tools for Luna Engine
===========================

File system tools that allow Luna to read, write, and explore
the file system.

Based on Part XIV (Agentic Architecture) of the Luna Engine Bible.
"""

import asyncio
import logging
from pathlib import Path
from typing import Optional
from functools import partial

from .registry import Tool

logger = logging.getLogger(__name__)


# =============================================================================
# ASYNC FILE HELPERS
# =============================================================================

def _sync_read_file(file_path: Path) -> str:
    """Synchronous file read (runs in executor)."""
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()


def _sync_write_file(file_path: Path, content: str) -> int:
    """Synchronous file write (runs in executor)."""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)
    return len(content.encode("utf-8"))


# =============================================================================
# TOOL IMPLEMENTATIONS
# =============================================================================

async def read_file(path: str) -> str:
    """
    Read the contents of a file.

    Args:
        path: Path to the file to read

    Returns:
        File contents as string

    Raises:
        FileNotFoundError: If file doesn't exist
        PermissionError: If file can't be read
    """
    file_path = Path(path).expanduser().resolve()

    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    if not file_path.is_file():
        raise ValueError(f"Path is not a file: {path}")

    # Run synchronous read in executor to avoid blocking
    loop = asyncio.get_event_loop()
    content = await loop.run_in_executor(None, partial(_sync_read_file, file_path))

    logger.debug(f"Read {len(content)} chars from {path}")
    return content


async def write_file(path: str, content: str) -> dict:
    """
    Write content to a file.

    Args:
        path: Path to the file to write
        content: Content to write to the file

    Returns:
        Dict with success status and bytes written

    Raises:
        PermissionError: If file can't be written
    """
    file_path = Path(path).expanduser().resolve()

    # Run synchronous write in executor to avoid blocking
    loop = asyncio.get_event_loop()
    bytes_written = await loop.run_in_executor(
        None, partial(_sync_write_file, file_path, content)
    )

    logger.debug(f"Wrote {len(content)} chars to {path}")

    return {
        "success": True,
        "path": str(file_path),
        "bytes_written": bytes_written,
    }


async def list_directory(
    path: str,
    include_hidden: bool = False,
    recursive: bool = False,
    max_depth: int = 3,
) -> list[dict]:
    """
    List contents of a directory.

    Args:
        path: Path to the directory
        include_hidden: If True, include hidden files (starting with .)
        recursive: If True, list subdirectories recursively
        max_depth: Maximum recursion depth (only used if recursive=True)

    Returns:
        List of dicts with file/directory info

    Raises:
        FileNotFoundError: If directory doesn't exist
        NotADirectoryError: If path is not a directory
    """
    dir_path = Path(path).expanduser().resolve()

    if not dir_path.exists():
        raise FileNotFoundError(f"Directory not found: {path}")

    if not dir_path.is_dir():
        raise NotADirectoryError(f"Path is not a directory: {path}")

    def _list_dir(current_path: Path, depth: int) -> list[dict]:
        """Recursively list directory contents."""
        items = []

        try:
            for item in sorted(current_path.iterdir()):
                # Skip hidden files unless requested
                if not include_hidden and item.name.startswith("."):
                    continue

                item_info = {
                    "name": item.name,
                    "path": str(item),
                    "type": "directory" if item.is_dir() else "file",
                }

                if item.is_file():
                    try:
                        item_info["size"] = item.stat().st_size
                    except OSError:
                        item_info["size"] = None

                items.append(item_info)

                # Recurse into subdirectories
                if recursive and item.is_dir() and depth < max_depth:
                    item_info["contents"] = _list_dir(item, depth + 1)

        except PermissionError:
            logger.warning(f"Permission denied: {current_path}")

        return items

    items = _list_dir(dir_path, 0)
    logger.debug(f"Listed {len(items)} items in {path}")

    return items


async def file_exists(path: str) -> bool:
    """
    Check if a file or directory exists.

    Args:
        path: Path to check

    Returns:
        True if path exists, False otherwise
    """
    file_path = Path(path).expanduser().resolve()
    return file_path.exists()


async def get_file_info(path: str) -> dict:
    """
    Get detailed information about a file or directory.

    Args:
        path: Path to the file or directory

    Returns:
        Dict with file information

    Raises:
        FileNotFoundError: If path doesn't exist
    """
    file_path = Path(path).expanduser().resolve()

    if not file_path.exists():
        raise FileNotFoundError(f"Path not found: {path}")

    stat = file_path.stat()

    return {
        "path": str(file_path),
        "name": file_path.name,
        "type": "directory" if file_path.is_dir() else "file",
        "size": stat.st_size if file_path.is_file() else None,
        "created": stat.st_ctime,
        "modified": stat.st_mtime,
        "accessed": stat.st_atime,
        "is_symlink": file_path.is_symlink(),
        "extension": file_path.suffix if file_path.is_file() else None,
    }


# =============================================================================
# TOOL DEFINITIONS
# =============================================================================

read_file_tool = Tool(
    name="read_file",
    description="Read the contents of a file. Returns the file content as a string.",
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "The path to the file to read"
            }
        },
        "required": ["path"]
    },
    execute=read_file,
    requires_confirmation=False,
    timeout_seconds=10,
)

write_file_tool = Tool(
    name="write_file",
    description="Write content to a file. Creates parent directories if needed. This action modifies the filesystem.",
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "The path to the file to write"
            },
            "content": {
                "type": "string",
                "description": "The content to write to the file"
            }
        },
        "required": ["path", "content"]
    },
    execute=write_file,
    requires_confirmation=True,  # File writes require confirmation
    timeout_seconds=30,
)

list_directory_tool = Tool(
    name="list_directory",
    description="List the contents of a directory. Returns a list of files and subdirectories with their types and sizes.",
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "The path to the directory to list"
            },
            "include_hidden": {
                "type": "boolean",
                "description": "Whether to include hidden files (starting with .)",
                "default": False
            },
            "recursive": {
                "type": "boolean",
                "description": "Whether to list subdirectories recursively",
                "default": False
            },
            "max_depth": {
                "type": "integer",
                "description": "Maximum recursion depth (only used if recursive=True)",
                "default": 3
            }
        },
        "required": ["path"]
    },
    execute=list_directory,
    requires_confirmation=False,
    timeout_seconds=30,
)

file_exists_tool = Tool(
    name="file_exists",
    description="Check if a file or directory exists at the given path.",
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "The path to check"
            }
        },
        "required": ["path"]
    },
    execute=file_exists,
    requires_confirmation=False,
    timeout_seconds=5,
)

get_file_info_tool = Tool(
    name="get_file_info",
    description="Get detailed information about a file or directory including size, timestamps, and type.",
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "The path to get information about"
            }
        },
        "required": ["path"]
    },
    execute=get_file_info,
    requires_confirmation=False,
    timeout_seconds=5,
)


# =============================================================================
# CONVENIENCE EXPORTS
# =============================================================================

ALL_FILE_TOOLS = [
    read_file_tool,
    write_file_tool,
    list_directory_tool,
    file_exists_tool,
    get_file_info_tool,
]


def register_file_tools(registry) -> None:
    """
    Register all file tools with a ToolRegistry.

    Args:
        registry: The ToolRegistry to register tools with
    """
    for tool in ALL_FILE_TOOLS:
        registry.register(tool)
    logger.info(f"Registered {len(ALL_FILE_TOOLS)} file tools")
