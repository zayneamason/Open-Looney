"""
Temporal Awareness — Clock injection, session gap detection, thread inheritance.

Pure functions. No DB writes. No side effects.
Called once per prompt assembly.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional
import logging

from luna.extraction.types import Thread, ThreadStatus

logger = logging.getLogger(__name__)


@dataclass
class TemporalContext:
    """Computed temporal state for a single prompt assembly."""

    # Clock
    now: datetime = field(default_factory=datetime.now)
    time_of_day: str = "day"              # morning | afternoon | evening | night
    day_of_week: str = "Monday"
    date_formatted: str = ""              # "Tuesday, February 18, 2026"

    # Session gap
    session_start: Optional[datetime] = None
    last_interaction: Optional[datetime] = None
    gap_duration: Optional[timedelta] = None
    gap_category: str = "first_ever"      # continuation | short_break | new_day
                                           # | multi_day | long_absence | first_ever

    # Thread inheritance
    active_thread: Optional[Thread] = None
    parked_threads: list = field(default_factory=list)
    resumable_summary: Optional[str] = None

    # Derived
    is_greeting_appropriate: bool = True
    continuity_hint: str = ""


def build_temporal_context(
    session_start: Optional[datetime] = None,
    active_thread: Optional[Thread] = None,
    parked_threads: Optional[list] = None,
    last_interaction: Optional[datetime] = None,
) -> TemporalContext:
    """
    Pure function. Computes temporal context from clock + thread state.

    Args:
        session_start: When this session began
        active_thread: Currently active thread (from Librarian)
        parked_threads: Parked threads (from Librarian._thread_cache)
        last_interaction: Last message timestamp from any session.
                         If None, falls back to most recent parked_at.

    Returns:
        TemporalContext ready for prompt injection.
    """
    now = datetime.now()
    parked = parked_threads or []

    # ── Clock ──
    hour = now.hour
    if 5 <= hour < 12:
        time_of_day = "morning"
    elif 12 <= hour < 17:
        time_of_day = "afternoon"
    elif 17 <= hour < 21:
        time_of_day = "evening"
    else:
        time_of_day = "night"

    # ── Gap detection ──
    # If no explicit last_interaction, derive from most recent parked thread
    effective_last = last_interaction
    if effective_last is None and parked:
        parked_with_times = [t for t in parked if t.parked_at is not None]
        if parked_with_times:
            most_recent = max(parked_with_times, key=lambda t: t.parked_at)
            effective_last = most_recent.parked_at

    gap = None
    gap_category = "first_ever"

    if effective_last:
        gap = now - effective_last
        minutes = gap.total_seconds() / 60

        if minutes < 5:
            gap_category = "continuation"
        elif minutes < 120:
            gap_category = "short_break"
        elif gap.days < 1:
            gap_category = "new_day"
        elif gap.days <= 7:
            gap_category = "multi_day"
        else:
            gap_category = "long_absence"

    # ── Thread inheritance ──
    continuity = _build_continuity_hint(
        gap_category, active_thread, parked, time_of_day
    )

    resumable = None
    with_tasks = [t for t in parked if t.open_tasks and t.status == ThreadStatus.PARKED]
    if with_tasks:
        resumable = f"{len(with_tasks)} parked thread{'s' if len(with_tasks) != 1 else ''} with open tasks"

    return TemporalContext(
        now=now,
        time_of_day=time_of_day,
        day_of_week=now.strftime("%A"),
        date_formatted=now.strftime("%A, %B %d, %Y"),
        session_start=session_start,
        last_interaction=effective_last,
        gap_duration=gap,
        gap_category=gap_category,
        active_thread=active_thread,
        parked_threads=parked,
        resumable_summary=resumable,
        is_greeting_appropriate=gap_category not in ("continuation",),
        continuity_hint=continuity,
    )


def _build_continuity_hint(
    gap_category: str,
    active_thread: Optional[Thread],
    parked_threads: list,
    time_of_day: str,
) -> str:
    """Format thread state into natural prompt language based on gap."""

    sections = []

    if gap_category == "continuation":
        # No injection needed — mid-conversation
        return ""

    if gap_category == "short_break":
        if active_thread:
            sections.append(f"You were discussing: {active_thread.topic}")
        return "\n".join(sections)

    if gap_category in ("new_day", "multi_day"):
        # Surface parked threads with open tasks
        with_tasks = [
            t for t in parked_threads
            if t.open_tasks and t.status == ThreadStatus.PARKED
        ]
        without_tasks = [
            t for t in parked_threads
            if not t.open_tasks and t.status == ThreadStatus.PARKED
        ][:2]  # Cap

        if with_tasks:
            sections.append("## Parked Threads (with open tasks)")
            for t in with_tasks[:3]:
                age = _humanize_age(t.parked_at)
                sections.append(
                    f'- "{t.topic}" — parked {age} '
                    f'({t.turn_count} turns, {len(t.open_tasks)} open)'
                )

        if without_tasks:
            topics = ", ".join(f'"{t.topic}"' for t in without_tasks)
            sections.append(f"Also parked (no open tasks): {topics}")

        if gap_category == "multi_day":
            sections.append(
                "\nDon't dump thread context unprompted. "
                "Let the user lead — surface these only if relevant."
            )

        return "\n".join(sections)

    if gap_category == "long_absence":
        total = len(parked_threads)
        with_tasks = sum(1 for t in parked_threads if t.open_tasks)
        if total > 0:
            sections.append(
                f"You have {total} parked thread{'s' if total != 1 else ''} "
                f"({with_tasks} with open tasks)."
            )
        sections.append(
            "Don't info-dump. Let the user set the pace. "
            "Surface threads only when directly relevant."
        )
        return "\n".join(sections)

    # first_ever
    return ""


def _humanize_age(dt: Optional[datetime]) -> str:
    """Convert a datetime to a human-readable age string."""
    if dt is None:
        return "some time ago"

    delta = datetime.now() - dt
    minutes = delta.total_seconds() / 60

    if minutes < 60:
        return f"{int(minutes)} minutes ago"

    hours = minutes / 60
    if hours < 24:
        return f"{int(hours)} hour{'s' if int(hours) != 1 else ''} ago"

    days = delta.days
    if days == 1:
        return "yesterday"
    if days < 7:
        return f"{days} days ago"

    weeks = days // 7
    if weeks == 1:
        return "last week"

    return f"{weeks} weeks ago"
