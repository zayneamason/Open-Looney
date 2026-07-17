"""Instance maturity detection — fresh / young / mature.

Used by WATCHDOG to adjust thresholds and by startup logging to suppress
irrelevant warnings in compiled (Nuitka) builds.
"""

import logging
import sqlite3
import sys
from enum import Enum
from pathlib import Path

from luna.core.paths import project_root, memory_matrix_path

logger = logging.getLogger(__name__)


class Maturity(Enum):
    FRESH = "fresh"    # <100 nodes — just deployed, no real usage
    YOUNG = "young"    # 100–10000 nodes — some usage, still growing
    MATURE = "mature"  # 10000+ nodes — established instance


def detect_maturity() -> Maturity:
    """Determine instance maturity from memory node count."""
    db_path = memory_matrix_path()
    if not db_path.exists():
        return Maturity.FRESH
    try:
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA busy_timeout=5000")
        nodes = conn.execute("SELECT COUNT(*) FROM memory_nodes").fetchone()[0]
        conn.close()
        if nodes < 100:
            return Maturity.FRESH
        elif nodes < 10000:
            return Maturity.YOUNG
        return Maturity.MATURE
    except Exception:
        return Maturity.FRESH


def is_compiled() -> bool:
    """Return True if running as a Nuitka-compiled binary."""
    return getattr(sys, 'frozen', False)


def compiled_debug(log: logging.Logger, msg: str, *args):
    """Log at DEBUG in compiled mode, WARNING otherwise.

    Use this for warnings that are expected/irrelevant in compiled builds
    (e.g. missing MLX, tiktoken, sqlite-vec) but useful in dev.
    """
    if is_compiled():
        log.debug(msg, *args)
    else:
        log.warning(msg, *args)
