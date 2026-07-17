"""
Eden Policy — Guardrails for Luna's Creative Powers
=====================================================

Controls what Eden operations Luna can perform autonomously
vs what requires explicit user approval.

This is the USER'S control surface over Luna's Eden abilities.
The kill switch, the budget cap, the approval gate.

Usage:
    policy = EdenPolicy.load()  # From config/eden.json

    if not policy.enabled:
        raise RuntimeError("Eden disabled by policy")

    if policy.requires_approval("eden_create_image"):
        # Ask user for confirmation before proceeding
        ...

    if not policy.check_budget():
        # Budget exceeded for this session
        ...
"""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from luna.core.paths import config_dir

logger = logging.getLogger(__name__)


# Default policy values
_DEFAULT_AUTO_APPROVE = ["eden_health", "eden_list_agents"]
_DEFAULT_REQUIRE_APPROVAL = ["eden_create_image", "eden_create_video", "eden_chat"]
_DEFAULT_MAX_GENERATIONS = 20
_DEFAULT_MAX_CHATS = 50


@dataclass
class EdenPolicy:
    """
    Policy governing Luna's use of Eden creative tools.

    The user maintains control through:
    - enabled: Master kill switch (False = all Eden tools disabled)
    - auto_approve: Tools Luna can use without asking
    - require_approval: Tools that need user confirmation first
    - max_generations_per_session: Cap on image/video generations
    - max_chats_per_session: Cap on agent chat turns
    - audit_to_memory: Whether to log every Eden call to memory
    """

    enabled: bool = True
    auto_approve: list[str] = field(default_factory=lambda: list(_DEFAULT_AUTO_APPROVE))
    require_approval: list[str] = field(default_factory=lambda: list(_DEFAULT_REQUIRE_APPROVAL))
    max_generations_per_session: int = _DEFAULT_MAX_GENERATIONS
    max_chats_per_session: int = _DEFAULT_MAX_CHATS
    audit_to_memory: bool = True

    # Runtime counters (not persisted)
    _generation_count: int = field(default=0, repr=False)
    _chat_count: int = field(default=0, repr=False)

    def requires_approval(self, tool_name: str) -> bool:
        """Check if a tool requires user approval before execution."""
        if not self.enabled:
            return True  # Everything needs approval when disabled
        if tool_name in self.auto_approve:
            return False
        if tool_name in self.require_approval:
            return True
        # Unknown tools default to requiring approval (safe default)
        return True

    def check_budget(self, tool_name: str) -> bool:
        """
        Check if the session budget allows this operation.

        Returns True if within budget, False if exceeded.
        """
        if not self.enabled:
            return False

        if tool_name in ("eden_create_image", "eden_create_video"):
            return self._generation_count < self.max_generations_per_session
        elif tool_name == "eden_chat":
            return self._chat_count < self.max_chats_per_session

        return True  # Non-generation tools have no budget

    def record_usage(self, tool_name: str) -> None:
        """Record that a tool was used (for budget tracking)."""
        if tool_name in ("eden_create_image", "eden_create_video"):
            self._generation_count += 1
        elif tool_name == "eden_chat":
            self._chat_count += 1

    def reset_session(self) -> None:
        """Reset session counters (called on new session start)."""
        self._generation_count = 0
        self._chat_count = 0

    @property
    def generation_budget_remaining(self) -> int:
        """How many more generations are allowed this session."""
        return max(0, self.max_generations_per_session - self._generation_count)

    @property
    def chat_budget_remaining(self) -> int:
        """How many more chat turns are allowed this session."""
        return max(0, self.max_chats_per_session - self._chat_count)

    def to_dict(self) -> dict:
        """Serialize policy to dict (for config file)."""
        return {
            "enabled": self.enabled,
            "auto_approve": self.auto_approve,
            "require_approval": self.require_approval,
            "max_generations_per_session": self.max_generations_per_session,
            "max_chats_per_session": self.max_chats_per_session,
            "audit_to_memory": self.audit_to_memory,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "EdenPolicy":
        """Create policy from dict."""
        return cls(
            enabled=data.get("enabled", True),
            auto_approve=data.get("auto_approve", list(_DEFAULT_AUTO_APPROVE)),
            require_approval=data.get("require_approval", list(_DEFAULT_REQUIRE_APPROVAL)),
            max_generations_per_session=data.get("max_generations_per_session", _DEFAULT_MAX_GENERATIONS),
            max_chats_per_session=data.get("max_chats_per_session", _DEFAULT_MAX_CHATS),
            audit_to_memory=data.get("audit_to_memory", True),
        )

    @classmethod
    def load(cls, config_path: Optional[str] = None) -> "EdenPolicy":
        """
        Load policy from config/eden.json.

        Falls back to defaults if no policy section exists.
        """
        if config_path is None:
            config_path = str(config_dir() / "eden.json")

        try:
            with open(config_path) as f:
                config = json.load(f)

            policy_data = config.get("policy", {})
            if policy_data:
                logger.info(f"Loaded Eden policy from {config_path}")
                return cls.from_dict(policy_data)
            else:
                logger.info("No policy section in eden.json, using defaults")
                return cls()
        except FileNotFoundError:
            logger.info("No eden.json found, using default policy")
            return cls()
        except Exception as e:
            logger.warning(f"Failed to load Eden policy: {e}, using defaults")
            return cls()

    def save(self, config_path: Optional[str] = None) -> None:
        """Save policy back to config/eden.json."""
        if config_path is None:
            config_path = str(config_dir() / "eden.json")

        try:
            # Read existing config
            try:
                with open(config_path) as f:
                    config = json.load(f)
            except FileNotFoundError:
                config = {}

            # Update policy section
            config["policy"] = self.to_dict()

            with open(config_path, "w") as f:
                json.dump(config, f, indent=2)
                f.write("\n")

            logger.info(f"Saved Eden policy to {config_path}")
        except Exception as e:
            logger.error(f"Failed to save Eden policy: {e}")
