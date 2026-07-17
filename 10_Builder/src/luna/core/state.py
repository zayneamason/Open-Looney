"""
Engine State Machine
====================

Lifecycle states and transitions for the Luna Engine.
"""

from enum import Enum, auto


class EngineState(Enum):
    """
    Engine lifecycle states.

    Transitions:
        STARTING -> RUNNING <-> PAUSED -> SLEEPING -> STOPPED
                        |                     ^
                        +---------------------+
    """
    STARTING = auto()   # Initializing actors, loading state
    RUNNING = auto()    # Core loop active, processing events
    PAUSED = auto()     # Loop halted but state in memory (quick resume)
    SLEEPING = auto()   # State serialized to disk (zero CPU)
    STOPPED = auto()    # Shutdown complete

    def can_transition_to(self, target: "EngineState") -> bool:
        """Check if transition to target state is valid."""
        valid_transitions = {
            EngineState.STARTING: {EngineState.RUNNING, EngineState.STOPPED},
            EngineState.RUNNING: {EngineState.PAUSED, EngineState.STOPPED},
            EngineState.PAUSED: {EngineState.RUNNING, EngineState.SLEEPING, EngineState.STOPPED},
            EngineState.SLEEPING: {EngineState.RUNNING, EngineState.STOPPED},
            EngineState.STOPPED: set(),  # Terminal state
        }
        return target in valid_transitions.get(self, set())
