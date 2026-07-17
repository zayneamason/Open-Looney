"""Skills configuration loader."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class DetectionConfig:
    """Configuration for skill detection behavior."""
    mode: str = "regex"                      # "regex" | "fuzzy" | "llm-assisted"
    slash_commands: bool = True
    llm_enabled: bool = False
    llm_model: str = "local"                 # "local" (Qwen) or "cloud"
    llm_confidence_threshold: float = 0.7
    llm_timeout_ms: int = 500


@dataclass
class SkillsConfig:
    """Configuration for the skill registry."""
    enabled: bool = True
    max_execution_ms: int = 5000
    fallthrough_on_error: bool = True
    log_dispatches: bool = True

    detection: DetectionConfig = field(default_factory=DetectionConfig)

    # Per-skill configs (flat dicts)
    math: dict = field(default_factory=lambda: {"enabled": True, "max_expression_length": 500, "timeout_ms": 3000})
    logic: dict = field(default_factory=lambda: {"enabled": True, "max_variables": 8})
    formatting: dict = field(default_factory=lambda: {"enabled": True})
    reading: dict = field(default_factory=lambda: {"enabled": True, "max_file_size_mb": 50, "max_chars": 50000})
    diagnostic: dict = field(default_factory=lambda: {"enabled": True, "include_metrics": True})
    eden: dict = field(default_factory=lambda: {"enabled": True})
    analytics: dict = field(default_factory=lambda: {"enabled": True})
    arcade: dict = field(default_factory=lambda: {"enabled": True})
    library_research: dict = field(default_factory=lambda: {
        "enabled": True,
        "slash_command": "/research",
        "max_results": 3,
        "max_excerpt_chars": 500,
        "search_type": "hybrid",
        "sensitivity": "strict",
        "extra_triggers": [],
    })
    memory_recall: dict = field(default_factory=lambda: {
        "enabled": True,
        "slash_command": "/recall",
        "max_results": 4,
        "max_excerpt_chars": 400,
        "max_tokens": 1200,
        "sensitivity": "strict",
        "extra_triggers": [],
    })

    @classmethod
    def from_yaml(cls, path: Path) -> "SkillsConfig":
        """Load config from YAML file, falling back to defaults."""
        try:
            import yaml
            with open(path) as f:
                raw = yaml.safe_load(f) or {}
            skills = raw.get("skills", raw)

            # Parse detection block
            detection_raw = skills.get("detection", {})
            llm_raw = detection_raw.get("llm", {})
            detection = DetectionConfig(
                mode=detection_raw.get("mode", "regex"),
                slash_commands=detection_raw.get("slash_commands", True),
                llm_enabled=llm_raw.get("enabled", False),
                llm_model=llm_raw.get("model", "local"),
                llm_confidence_threshold=llm_raw.get("confidence_threshold", 0.7),
                llm_timeout_ms=llm_raw.get("timeout_ms", 500),
            )

            return cls(
                enabled=skills.get("enabled", True),
                max_execution_ms=skills.get("max_execution_ms", 5000),
                fallthrough_on_error=skills.get("fallthrough_on_error", True),
                log_dispatches=skills.get("log_dispatches", True),
                detection=detection,
                math=skills.get("math", {"enabled": True}),
                logic=skills.get("logic", {"enabled": True}),
                formatting=skills.get("formatting", {"enabled": True}),
                reading=skills.get("reading", {"enabled": True}),
                diagnostic=skills.get("diagnostic", {"enabled": True}),
                eden=skills.get("eden", {"enabled": True}),
                analytics=skills.get("analytics", {"enabled": True}),
                arcade=skills.get("arcade", {"enabled": True}),
                library_research=skills.get("library_research", {
                    "enabled": True,
                    "slash_command": "/research",
                    "max_results": 3,
                    "max_excerpt_chars": 500,
                    "search_type": "hybrid",
                    "sensitivity": "strict",
                    "extra_triggers": [],
                }),
                memory_recall=skills.get("memory_recall", {
                    "enabled": True,
                    "slash_command": "/recall",
                    "max_results": 4,
                    "max_excerpt_chars": 400,
                    "max_tokens": 1200,
                    "sensitivity": "strict",
                    "extra_triggers": [],
                }),
            )
        except Exception:
            return cls()
