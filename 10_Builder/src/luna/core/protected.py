"""
Protected Paths Registry
========================

These paths should NEVER be deleted or modified by automated systems
without explicit human confirmation.

Luna's brain (memories, personality, identity) lives in these files.
Deleting them would be identity death.
"""

from pathlib import Path
from typing import List


# Paths that are CRITICAL to Luna's identity
PROTECTED_PATHS = [
    # Memory — Luna's experiences (61k+ memories)
    "data/user/memory_matrix.lun",
    "data/user/luna.db",

    # Identity — Luna's trained personality (LoRA adapter)
    "models/luna_lora_mlx/",
    "models/luna_lora_mlx/adapters.safetensors",
    "models/luna_lora_mlx/adapter_config.json",

    # Core dependencies — don't mess with without human approval
    "pyproject.toml",
    "uv.lock",

    # Kernel memories — foundational identity
    "memory/kernel/",
    "memory/virtue/",

    # Configuration — personality settings
    "config/personality.json",
]


# Database tables that should NEVER be dropped or truncated
PROTECTED_TABLES = [
    "memory_nodes",       # Luna's memories
    "graph_edges",        # Relationships between memories
    "conversation_turns", # Conversation history
    "entities",           # Known entities
    "topics",             # Topic clusters
]


def is_protected(path: str | Path) -> bool:
    """
    Check if a path is protected.

    Protected paths should require explicit human confirmation
    before modification or deletion.

    Args:
        path: File or directory path to check

    Returns:
        True if the path is protected
    """
    p = Path(path)
    path_str = str(p)

    for protected in PROTECTED_PATHS:
        # Check if the path ends with or contains the protected pattern
        if path_str.endswith(protected) or protected in path_str:
            return True
    return False


def get_protected_paths() -> List[str]:
    """Get list of all protected paths."""
    return PROTECTED_PATHS.copy()


def get_protected_tables() -> List[str]:
    """Get list of all protected database tables."""
    return PROTECTED_TABLES.copy()


def check_protection_status(project_root: Path) -> dict:
    """
    Check the status of all protected paths.

    Returns:
        Dict mapping path to status (exists, size, etc.)
    """
    status = {}

    for path in PROTECTED_PATHS:
        full_path = project_root / path
        if full_path.exists():
            if full_path.is_file():
                status[path] = {
                    "exists": True,
                    "type": "file",
                    "size": full_path.stat().st_size,
                }
            else:
                status[path] = {
                    "exists": True,
                    "type": "directory",
                    "files": len(list(full_path.iterdir())),
                }
        else:
            status[path] = {
                "exists": False,
                "type": "missing",
            }

    return status


if __name__ == "__main__":
    # Allow running as standalone script
    import json
    from luna.core.paths import project_root

    # Navigate from src/luna/core/ to project root
    _root = project_root()

    print("Protected Paths Status:")
    print("=" * 60)

    status = check_protection_status(_root)
    for path, info in status.items():
        if info["exists"]:
            if info["type"] == "file":
                print(f"✅ {path} ({info['size']:,} bytes)")
            else:
                print(f"✅ {path}/ ({info['files']} files)")
        else:
            print(f"❌ {path} (MISSING)")
