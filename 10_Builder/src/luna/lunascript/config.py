"""LunaScript configuration — loaded from config/lunascript.yaml."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


@dataclass
class LunaScriptConfig:
    enabled: bool = True
    decay_halflife: float = 100.0
    epsilon: float = 0.15
    epsilon_decay: float = 0.995
    epsilon_floor: float = 0.02
    veto_sigma: float = 2.0
    max_retries: int = 1
    calibration_interval_sessions: int = 50
    min_corpus_size: int = 50
    feature_toggles: dict[str, bool] = field(default_factory=dict)
    forbidden_phrases: list[str] = field(default_factory=lambda: [
        "I'd be happy to",
        "Certainly!",
        "As an AI",
        "I don't have personal",
        "Let me help you with",
        "I appreciate you",
        "That's a great question",
    ])

    @classmethod
    def from_yaml(cls, path: Path) -> "LunaScriptConfig":
        path = Path(path)
        if not path.exists():
            return cls()
        with open(path) as f:
            raw = yaml.safe_load(f) or {}
        cfg = raw.get("lunascript", raw)
        return cls(
            enabled=cfg.get("enabled", True),
            decay_halflife=cfg.get("decay_halflife", 100.0),
            epsilon=cfg.get("epsilon", 0.15),
            epsilon_decay=cfg.get("epsilon_decay", 0.995),
            epsilon_floor=cfg.get("epsilon_floor", 0.02),
            veto_sigma=cfg.get("veto_sigma", 2.0),
            max_retries=cfg.get("max_retries", 1),
            calibration_interval_sessions=cfg.get("calibration_interval_sessions", 50),
            min_corpus_size=cfg.get("min_corpus_size", 50),
            feature_toggles=cfg.get("features", {}),
            forbidden_phrases=cfg.get("forbidden_phrases", [
                "I'd be happy to", "Certainly!", "As an AI",
                "I don't have personal", "Let me help you with",
                "I appreciate you", "That's a great question",
            ]),
        )
