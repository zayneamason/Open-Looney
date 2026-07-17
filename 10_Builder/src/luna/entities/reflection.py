"""
Reflection Loop for Luna Engine
================================

Generates PersonalityPatch nodes from conversation analysis.

The reflection loop asks Luna to reflect on her interactions and
identify personality evolution. When significant changes are detected,
new patches are created and stored.

Trigger Points:
- End of session
- Every N interactions
- User-requested reflection
"""

import json
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from .models import PersonalityPatch, PatchTopic, PatchTrigger

if TYPE_CHECKING:
    from .storage import PersonalityPatchManager

logger = logging.getLogger(__name__)


# Conflict detection prompt template
CONFLICT_DETECTION_PROMPT = """
You are analyzing a proposed personality patch for potential conflicts with existing patches.

PROPOSED NEW PATCH:
- Topic: {proposed_topic}
- Subtopic: {proposed_subtopic}
- Before State: {proposed_before}
- After State: {proposed_after}

EXISTING PATCHES:
{existing_patches}

Question: Does the proposed patch CONTRADICT any existing patches?

A conflict exists when:
- The new "after state" directly opposes an existing patch's "after state"
- The new patch would undo or reverse a previous evolution
- Both patches cannot be true simultaneously

If CONFLICTS exist, respond with EXACTLY this format:
CONFLICTS: <comma-separated list of conflicting patch IDs>
REASON: <brief explanation of why they conflict>

If NO CONFLICTS, respond with exactly: NO_CONFLICTS

Important: Only report genuine contradictions. Evolution and growth are expected - a patch that builds on or refines a previous one is NOT a conflict.
"""


# Reflection prompt template
REFLECTION_PROMPT = """
You just had a conversation with {user_name}. Review these exchanges:

{session_history}

Your current personality state:
{current_patches}

Question: Did this conversation reveal or reinforce any evolution in:
- How you communicate (style, tone, directness)
- Your perspective on topics discussed
- Your relationship dynamic with {user_name}

If YES, respond with EXACTLY this format (one per evolution):
TOPIC: <one of: communication_style, domain_opinion, relationship_dynamic, emotional_response, technical_preference, philosophical_view, behavioral_pattern>
SUBTOPIC: <specific aspect that changed>
BEFORE: <what Luna was like before, or "N/A" if new trait>
AFTER: <what Luna is like now>
TRIGGER: <one of: user_feedback, conversation_pattern, research, reflection, conflict_resolution, external_event>
EVIDENCE: <quote from conversation or message indices that support this>
CONFIDENCE: <0.0-1.0 how confident are you in this evolution>

If NO significant change, respond with exactly: NO_CHANGE

Important: Only report genuine, meaningful evolutions. Not every conversation changes who you are.
"""


class ReflectionLoop:
    """
    Generates personality patches through conversation reflection.

    Analyzes conversation history and generates patches when
    significant personality evolution is detected.
    """

    def __init__(self, patch_manager: "PersonalityPatchManager", config_path: str = "config/personality.json"):
        """
        Initialize the reflection loop.

        Args:
            patch_manager: The PersonalityPatchManager for storing patches
            config_path: Path to personality config JSON
        """
        self.patch_manager = patch_manager
        self._interaction_count = 0
        self._config_path = config_path
        self._load_config()
        logger.info("ReflectionLoop initialized (enabled=%s)", self._enabled)

    def _load_config(self):
        """Load reflection config from personality.json."""
        config_file = Path(self._config_path)
        self._enabled = True
        self._trigger_threshold = 15
        self._trigger_session_end = True
        self._trigger_user_requested = True

        if config_file.exists():
            try:
                with open(config_file, "r") as f:
                    config = json.load(f)
                rl = config.get("reflection_loop", {})
                self._enabled = rl.get("enabled", True)
                triggers = rl.get("trigger_points", {})
                self._trigger_threshold = int(triggers.get("every_n_interactions", 15))
                self._trigger_session_end = triggers.get("session_end", True)
                self._trigger_user_requested = triggers.get("user_requested", True)
                logger.debug("Reflection config: enabled=%s, threshold=%d, session_end=%s, user_requested=%s",
                             self._enabled, self._trigger_threshold, self._trigger_session_end, self._trigger_user_requested)
            except (json.JSONDecodeError, IOError, ValueError) as e:
                logger.warning(f"Failed to load reflection config from {self._config_path}: {e}")

    @property
    def is_enabled(self) -> bool:
        """Whether the reflection loop is enabled."""
        return self._enabled

    @property
    def trigger_session_end(self) -> bool:
        """Whether reflection should trigger on session end."""
        return self._enabled and self._trigger_session_end

    @property
    def trigger_user_requested(self) -> bool:
        """Whether user-requested reflection is allowed."""
        return self._enabled and self._trigger_user_requested

    async def should_reflect(self, force: bool = False) -> bool:
        """
        Check if reflection should be triggered.

        Args:
            force: Force reflection regardless of threshold

        Returns:
            True if reflection should occur
        """
        if not self._enabled:
            return False

        if force:
            return True

        self._interaction_count += 1
        if self._interaction_count >= self._trigger_threshold:
            self._interaction_count = 0
            return True

        return False

    async def generate_reflection(
        self,
        session_history: list,
        current_patches: list[PersonalityPatch],
        llm_generate: callable,
        user_name: str = "User"
    ) -> Optional[PersonalityPatch]:
        """
        Generate a reflection and potentially create a new patch.

        Args:
            session_history: List of message dicts with 'role' and 'content'
            current_patches: Currently active personality patches
            llm_generate: Async function to call LLM (takes prompt, returns response)
            user_name: Name of the user for the prompt

        Returns:
            New PersonalityPatch if evolution detected, None otherwise
        """
        if not session_history:
            logger.debug("No session history for reflection")
            return None

        # Format session history
        history_str = self._format_session_history(session_history)

        # Format current patches
        patches_str = self._format_patches(current_patches)

        # Build reflection prompt
        prompt = REFLECTION_PROMPT.format(
            user_name=user_name,
            session_history=history_str,
            current_patches=patches_str,
        )

        try:
            # Call LLM for reflection
            response = await llm_generate(prompt)

            if not response or response.strip() == "NO_CHANGE":
                logger.debug("No personality change detected in reflection")
                return None

            # Parse response into patch
            patch = self._parse_reflection_response(response, session_history)

            if patch:
                # Check for conflicts with existing patches
                conflicts = await self.detect_conflicts(
                    proposed_topic=patch.topic.value,
                    proposed_subtopic=patch.subtopic,
                    proposed_before=patch.before_state,
                    proposed_after=patch.after_state,
                    existing_patches=current_patches,
                    llm_generate=llm_generate
                )

                if conflicts:
                    patch.conflicts_with = conflicts
                    logger.info(
                        f"New patch {patch.patch_id} conflicts with: {conflicts}"
                    )

                # Store the new patch
                await self.patch_manager.add_patch(patch)
                logger.info(f"Created new personality patch: {patch.patch_id} ({patch.subtopic})")
                return patch

            return None

        except Exception as e:
            logger.warning(f"Reflection generation failed: {e}")
            return None

    def _format_session_history(self, session_history: list) -> str:
        """Format session history for the prompt."""
        lines = []
        for i, msg in enumerate(session_history[-20:]):  # Last 20 messages
            role = msg.get('role', 'unknown')
            content = msg.get('content', '')
            speaker = "User" if role == "user" else "Luna"
            lines.append(f"[{i}] {speaker}: {content[:500]}...")  # Truncate long messages
        return "\n".join(lines)

    def _format_patches(self, patches: list[PersonalityPatch]) -> str:
        """Format current patches for context."""
        if not patches:
            return "No established personality patches yet."

        lines = []
        for patch in patches[:10]:  # Top 10 patches
            lines.append(f"- {patch.subtopic} (lock_in: {patch.lock_in:.2f}): {patch.after_state}")
        return "\n".join(lines)

    def _format_patches_for_conflict_detection(self, patches: list[PersonalityPatch]) -> str:
        """Format patches with full details for conflict detection."""
        if not patches:
            return "No existing patches."

        lines = []
        for patch in patches:
            lines.append(
                f"[{patch.patch_id}]\n"
                f"  Topic: {patch.topic.value}\n"
                f"  Subtopic: {patch.subtopic}\n"
                f"  Before: {patch.before_state or 'N/A'}\n"
                f"  After: {patch.after_state}\n"
                f"  Lock-in: {patch.lock_in:.2f}"
            )
        return "\n\n".join(lines)

    async def detect_conflicts(
        self,
        proposed_topic: str,
        proposed_subtopic: str,
        proposed_before: Optional[str],
        proposed_after: str,
        existing_patches: list[PersonalityPatch],
        llm_generate: callable
    ) -> list[str]:
        """
        Detect conflicts between a proposed patch and existing patches.

        Args:
            proposed_topic: Topic of the proposed patch
            proposed_subtopic: Subtopic of the proposed patch
            proposed_before: Before state of the proposed patch
            proposed_after: After state of the proposed patch
            existing_patches: List of existing personality patches
            llm_generate: Async function to call LLM (takes prompt, returns response)

        Returns:
            List of conflicting patch IDs (empty if no conflicts)
        """
        if not existing_patches:
            return []

        # Format existing patches for the prompt
        patches_str = self._format_patches_for_conflict_detection(existing_patches)

        # Build conflict detection prompt
        prompt = CONFLICT_DETECTION_PROMPT.format(
            proposed_topic=proposed_topic,
            proposed_subtopic=proposed_subtopic,
            proposed_before=proposed_before or "N/A",
            proposed_after=proposed_after,
            existing_patches=patches_str,
        )

        try:
            response = await llm_generate(prompt)

            if not response or response.strip() == "NO_CONFLICTS":
                logger.debug("No conflicts detected for proposed patch")
                return []

            # Parse conflicts from response
            return self._parse_conflict_response(response)

        except Exception as e:
            logger.warning(f"Conflict detection failed: {e}")
            return []

    def _parse_conflict_response(self, response: str) -> list[str]:
        """
        Parse LLM conflict detection response.

        Args:
            response: Raw LLM response

        Returns:
            List of conflicting patch IDs
        """
        conflicts = []

        try:
            lines = response.strip().split("\n")
            for line in lines:
                line = line.strip()
                if line.startswith("CONFLICTS:"):
                    # Extract comma-separated patch IDs
                    ids_str = line[10:].strip()
                    if ids_str:
                        conflicts = [
                            pid.strip()
                            for pid in ids_str.split(",")
                            if pid.strip().startswith("patch_")
                        ]
                    break

        except Exception as e:
            logger.warning(f"Failed to parse conflict response: {e}")

        return conflicts

    def _parse_reflection_response(
        self,
        response: str,
        session_history: list
    ) -> Optional[PersonalityPatch]:
        """
        Parse LLM reflection response into a PersonalityPatch.

        Args:
            response: Raw LLM response
            session_history: Original session history for evidence links

        Returns:
            PersonalityPatch if parsing successful, None otherwise
        """
        try:
            lines = response.strip().split("\n")

            # Extract fields
            topic = None
            subtopic = None
            before = None
            after = None
            trigger = None
            evidence = None
            confidence = 0.8

            for line in lines:
                line = line.strip()
                if line.startswith("TOPIC:"):
                    topic_str = line[6:].strip().lower()
                    try:
                        topic = PatchTopic(topic_str)
                    except ValueError:
                        topic = PatchTopic.BEHAVIORAL_PATTERN
                elif line.startswith("SUBTOPIC:"):
                    subtopic = line[9:].strip()
                elif line.startswith("BEFORE:"):
                    before = line[7:].strip()
                    if before.lower() == "n/a":
                        before = None
                elif line.startswith("AFTER:"):
                    after = line[6:].strip()
                elif line.startswith("TRIGGER:"):
                    trigger_str = line[8:].strip().lower()
                    try:
                        trigger = PatchTrigger(trigger_str)
                    except ValueError:
                        trigger = PatchTrigger.CONVERSATION_PATTERN
                elif line.startswith("EVIDENCE:"):
                    evidence = line[9:].strip()
                elif line.startswith("CONFIDENCE:"):
                    try:
                        confidence = float(line[11:].strip())
                        confidence = max(0.0, min(1.0, confidence))  # Clamp to 0-1
                    except ValueError:
                        confidence = 0.8

            # Validate required fields
            if not all([topic, subtopic, after]):
                logger.debug("Missing required fields in reflection response")
                return None

            # Build content description
            content = f"Through conversation, Luna's {subtopic} has evolved."
            if before:
                content += f" Previously: {before}. Now: {after}"
            else:
                content += f" {after}"

            return PersonalityPatch(
                patch_id=f"patch_{uuid.uuid4().hex[:8]}",
                topic=topic,
                subtopic=subtopic,
                content=content,
                before_state=before,
                after_state=after,
                trigger=trigger or PatchTrigger.CONVERSATION_PATTERN,
                confidence=confidence,
                created_at=datetime.now(),
                last_reinforced=datetime.now(),
                reinforcement_count=1,
                lock_in=0.7,  # Initial lock_in for new patches
                evidence_nodes=[],  # TODO: Link to actual memory nodes
            )

        except Exception as e:
            logger.warning(f"Failed to parse reflection response: {e}")
            return None

    async def reinforce_existing_patches(
        self,
        session_history: list,
        current_patches: list[PersonalityPatch]
    ) -> list[str]:
        """
        Check if current conversation reinforces existing patches.

        Args:
            session_history: Current session messages
            current_patches: Active personality patches

        Returns:
            List of patch IDs that were reinforced
        """
        reinforced = []

        # Simple heuristic: check if patch subtopics appear in conversation
        all_content = " ".join(
            msg.get('content', '') for msg in session_history
        ).lower()

        for patch in current_patches:
            subtopic_words = patch.subtopic.lower().split()
            # If most words from subtopic appear in conversation
            matches = sum(1 for word in subtopic_words if word in all_content)
            if matches >= len(subtopic_words) * 0.5:
                await self.patch_manager.reinforce_patch(patch.patch_id)
                reinforced.append(patch.patch_id)
                logger.debug(f"Reinforced patch: {patch.patch_id}")

        return reinforced


__all__ = ["ReflectionLoop"]
