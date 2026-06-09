"""
G.O.A.T. Orchestration Agent — Per-session Agent Manager

Maintains a pool of Strands Agent instances keyed by AgentCore session ID.
Each session gets its own Agent with preserved conversation history across
turns, enabling multi-turn flows like the Capture_Confirmation_Prompt.

Memory pressure: LRU eviction with configurable max sessions and TTL.
Container restarts: Sessions are lost (acceptable for a demo — the user
  simply starts a new conversation).
Concurrency: AgentCore serializes requests per session, so no locking needed.
"""

import time
from collections import OrderedDict
from typing import Optional, Callable

from strands import Agent

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

#: Maximum number of concurrent sessions held in memory.
#: When exceeded, the least-recently-used session is evicted.
MAX_SESSIONS = 50

#: Time-to-live in seconds for idle sessions. Sessions not accessed
#: within this window are evicted on the next access or cleanup pass.
SESSION_TTL_SECONDS = 30 * 60  # 30 minutes


class _SessionEntry:
    """Wrapper holding an Agent instance and its last-access timestamp."""

    __slots__ = ("agent", "last_accessed")

    def __init__(self, agent: Agent):
        self.agent = agent
        self.last_accessed = time.monotonic()

    def touch(self) -> None:
        self.last_accessed = time.monotonic()

    @property
    def is_expired(self) -> bool:
        return (time.monotonic() - self.last_accessed) > SESSION_TTL_SECONDS


class SessionManager:
    """LRU + TTL session pool for Strands Agent instances.

    Usage::

        manager = SessionManager(agent_factory=create_agent)
        agent = manager.get_or_create("session-123")
        # agent.stream_async(prompt) — conversation history preserved

    The ``agent_factory`` callable is invoked with no arguments when a
    new session needs to be created. It should return a fully configured
    ``strands.Agent`` instance.
    """

    def __init__(self, agent_factory: Callable[[], Agent]):
        self._factory = agent_factory
        # OrderedDict maintains insertion/access order for LRU eviction
        self._sessions: OrderedDict[str, _SessionEntry] = OrderedDict()

    def get_or_create(self, session_id: str) -> Agent:
        """Return the Agent for ``session_id``, creating one if needed.

        Moves the session to the end of the LRU order on access.
        Triggers eviction of expired and over-limit sessions.
        """
        self._evict_expired()

        if session_id in self._sessions:
            entry = self._sessions[session_id]
            entry.touch()
            # Move to end (most recently used)
            self._sessions.move_to_end(session_id)
            return entry.agent

        # Create new session
        agent = self._factory()
        entry = _SessionEntry(agent)
        self._sessions[session_id] = entry
        self._sessions.move_to_end(session_id)

        # Evict oldest if over capacity
        while len(self._sessions) > MAX_SESSIONS:
            self._sessions.popitem(last=False)

        return agent

    def remove(self, session_id: str) -> None:
        """Explicitly remove a session (e.g., on user sign-out)."""
        self._sessions.pop(session_id, None)

    def _evict_expired(self) -> None:
        """Remove sessions that have exceeded the TTL."""
        now = time.monotonic()
        expired_keys = [
            key for key, entry in self._sessions.items()
            if (now - entry.last_accessed) > SESSION_TTL_SECONDS
        ]
        for key in expired_keys:
            del self._sessions[key]

    @property
    def active_count(self) -> int:
        """Number of sessions currently in the pool."""
        return len(self._sessions)
