"""Skill registry — registers, discovers, and executes skills."""

import asyncio
import logging
import time
from typing import Optional

from .base import Skill, SkillResult
from .config import SkillsConfig
from .detector import SkillDetector

logger = logging.getLogger(__name__)


class SkillRegistry:
    """Central registry for deterministic skills."""

    def __init__(self, config: Optional[SkillsConfig] = None):
        self.config = config or SkillsConfig()
        self.detector = SkillDetector(self.config)
        self._skills: dict[str, Skill] = {}
        self._plugin_dirs: list = []

    def register(self, skill: Skill) -> None:
        """Register a skill if it's available."""
        if skill.is_available():
            self._skills[skill.name] = skill
            logger.debug(f"[SKILLS] Registered: {skill.name}")
        else:
            logger.debug(f"[SKILLS] Skipped (unavailable): {skill.name}")

    def get(self, name: str) -> Optional[Skill]:
        return self._skills.get(name)

    def list_available(self) -> list[str]:
        return list(self._skills.keys())

    def register_defaults(self) -> None:
        """Register all built-in skills that are available."""
        # Phase 1: Math + Diagnostic
        try:
            from .math.skill import MathSkill
            if self.config.math.get("enabled", True):
                self.register(MathSkill(self.config.math))
        except ImportError:
            logger.debug("[SKILLS] MathSkill not available (missing sympy?)")

        try:
            from .diagnostic.skill import DiagnosticSkill
            if self.config.diagnostic.get("enabled", True):
                self.register(DiagnosticSkill(self.config.diagnostic))
        except ImportError:
            logger.debug("[SKILLS] DiagnosticSkill not available")

        # Phase 2: Logic + Reading
        try:
            from .logic.skill import LogicSkill
            if self.config.logic.get("enabled", True):
                self.register(LogicSkill(self.config.logic))
        except ImportError:
            logger.debug("[SKILLS] LogicSkill not available (missing sympy?)")

        try:
            from .reading.skill import ReadingSkill
            if self.config.reading.get("enabled", True):
                self.register(ReadingSkill(self.config.reading))
        except ImportError:
            logger.debug("[SKILLS] ReadingSkill not available")

        # Phase 3: Eden + Analytics
        try:
            from .eden.skill import EdenSkill
            if self.config.eden.get("enabled", True):
                self.register(EdenSkill(self.config.eden))
        except ImportError:
            logger.debug("[SKILLS] EdenSkill not available")

        try:
            from .analytics.skill import AnalyticsSkill
            if self.config.analytics.get("enabled", True):
                self.register(AnalyticsSkill(self.config.analytics))
        except ImportError:
            logger.debug("[SKILLS] AnalyticsSkill not available")

        # Phase 4: Formatting (fallthrough only)
        try:
            from .formatting.skill import FormattingSkill
            if self.config.formatting.get("enabled", True):
                self.register(FormattingSkill(self.config.formatting))
        except ImportError:
            logger.debug("[SKILLS] FormattingSkill not available")

        # Phase 5: Arcade
        try:
            from .arcade.skill import ArcadeSkill
            if self.config.arcade.get("enabled", True):
                self.register(ArcadeSkill(self.config.arcade))
        except ImportError:
            logger.debug("[SKILLS] ArcadeSkill not available (missing pyxel?)")

        # Phase 6: Library Research (first companion research skill)
        try:
            from .library_research.skill import LibraryResearchSkill
            if self.config.library_research.get("enabled", True):
                self.register(LibraryResearchSkill(self.config.library_research))
        except ImportError:
            logger.debug("[SKILLS] LibraryResearchSkill not available")

        # Phase 7: Memory Recall (Matrix-backed source-aware recall)
        try:
            from .memory_recall.skill import MemoryRecallSkill
            if self.config.memory_recall.get("enabled", True):
                self.register(MemoryRecallSkill(self.config.memory_recall))
        except ImportError:
            logger.debug("[SKILLS] MemoryRecallSkill not available")

    def register_plugins(self, plugin_dir) -> None:
        """Discover and load plugin skills from a directory.

        Each plugin is a subdirectory containing __init__.py that exports
        a SkillClass attribute (a Skill subclass). Dependencies are loaded
        from plugins/_lib/ if it exists.
        """
        from pathlib import Path
        plugin_dir = Path(plugin_dir)
        if not plugin_dir.exists():
            return
        if plugin_dir not in self._plugin_dirs:
            self._plugin_dirs.append(plugin_dir)

        # Add bundled plugin deps to sys.path
        import sys
        lib_dir = plugin_dir / "_lib"
        if lib_dir.exists() and str(lib_dir) not in sys.path:
            sys.path.insert(0, str(lib_dir))

        for entry in sorted(plugin_dir.iterdir()):
            if not entry.is_dir() or entry.name.startswith(("_", ".")):
                continue
            init_path = entry / "__init__.py"
            if not init_path.exists():
                continue
            try:
                import importlib.util
                spec = importlib.util.spec_from_file_location(
                    f"luna_plugin_{entry.name}", str(init_path)
                )
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                skill_cls = getattr(module, "SkillClass", None)
                if skill_cls and issubclass(skill_cls, Skill):
                    cfg = getattr(self.config, entry.name, {"enabled": True})
                    if isinstance(cfg, dict) and cfg.get("enabled", True):
                        self.register(skill_cls(cfg))
                        logger.info("[SKILLS] Plugin loaded: %s from %s", skill_cls.name, entry.name)
                    elif not isinstance(cfg, dict):
                        self.register(skill_cls({"enabled": True}))
                        logger.info("[SKILLS] Plugin loaded: %s from %s", skill_cls.name, entry.name)
                else:
                    logger.debug("[SKILLS] Plugin %s has no SkillClass export", entry.name)
            except Exception as e:
                logger.warning("[SKILLS] Plugin %s failed to load: %s", entry.name, e)

    def reload_config(self, config: SkillsConfig):
        """Hot-reload detection config without restarting engine."""
        self.config = config
        self.detector = SkillDetector(config)
        self._skills = {}
        self.register_defaults()
        for plugin_dir in self._plugin_dirs:
            self.register_plugins(plugin_dir)

    async def execute(
        self,
        skill_name: str,
        query: str,
        context: dict = None,
    ) -> SkillResult:
        """Execute a skill with timeout protection."""
        skill = self._skills.get(skill_name)
        if not skill:
            return SkillResult(
                success=False, skill_name=skill_name,
                fallthrough=True, error=f"Skill '{skill_name}' not registered",
            )

        timeout_ms = self.config.max_execution_ms
        start = time.perf_counter()

        try:
            result = await asyncio.wait_for(
                skill.execute(query, context or {}),
                timeout=timeout_ms / 1000.0,
            )
            result.execution_ms = (time.perf_counter() - start) * 1000
            return result
        except asyncio.TimeoutError:
            elapsed = (time.perf_counter() - start) * 1000
            logger.warning("[SKILLS] %s timed out after %.0fms", skill_name, elapsed)
            return SkillResult(
                success=False, skill_name=skill_name,
                fallthrough=True, error=f"Timeout after {elapsed:.0f}ms",
                execution_ms=elapsed,
            )
        except Exception as e:
            elapsed = (time.perf_counter() - start) * 1000
            logger.warning("[SKILLS] %s execution failed after %.0fms: %s", skill_name, elapsed, e)
            return SkillResult(
                success=False, skill_name=skill_name,
                fallthrough=True, error=str(e),
                execution_ms=elapsed,
            )
