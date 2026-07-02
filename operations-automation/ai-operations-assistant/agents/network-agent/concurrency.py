"""
Concurrency limiter for SSM-based diagnostic commands.

Implements Requirements 8.1, 8.2, 8.4, 8.5 of the GOAT Network Diagnostics
spec: at most 3 simultaneous SSM commands globally across all target instances,
and at most 1 per individual instance.

The :class:`SSMConcurrencyLimiter` uses threading locks to provide thread-safe
acquire/release semantics and exposes a :meth:`slot` context manager for
exception-safe usage in action handlers.

Typical usage::

    limiter = SSMConcurrencyLimiter()

    with limiter.slot(instance_id):
        # execute SSM command
        ...
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Dict


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


class ConcurrencyLimitError(Exception):
    """Raised when a concurrency slot cannot be acquired.

    Attributes:
        message: Human-readable description of which limit was reached.
        instance_id: The instance ID for which acquisition was attempted,
            or ``None`` if the global limit was reached before checking
            per-instance limits.
    """

    def __init__(self, message: str, instance_id: str = None) -> None:
        super().__init__(message)
        self.message = message
        self.instance_id = instance_id

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.message


# ---------------------------------------------------------------------------
# Limiter
# ---------------------------------------------------------------------------


class SSMConcurrencyLimiter:
    """Thread-safe concurrency limiter for SSM-based diagnostic commands.

    Enforces two limits:
      * **Global limit** (default 3): the maximum number of SSM commands
        in-flight across all target instances combined.
      * **Per-instance limit** (default 1): the maximum number of SSM
        commands in-flight for any single target instance.

    Args:
        global_limit: Maximum simultaneous SSM commands across all
            instances.  Defaults to 3 (Requirement 8.1).
        per_instance_limit: Maximum simultaneous SSM commands per
            individual instance.  Defaults to 1 (Requirement 8.4).
    """

    def __init__(
        self,
        global_limit: int = 3,
        per_instance_limit: int = 1,
    ) -> None:
        self._global_limit = global_limit
        self._per_instance_limit = per_instance_limit
        self._lock = threading.Lock()
        self._global_count: int = 0
        self._instance_counts: Dict[str, int] = {}

    def acquire(self, instance_id: str) -> None:
        """Acquire a concurrency slot for the given instance.

        Checks the global limit first, then the per-instance limit.

        Args:
            instance_id: The EC2 instance ID for which to acquire a slot.

        Raises:
            ConcurrencyLimitError: If the global limit (3) or per-instance
                limit (1) has been reached.
        """
        with self._lock:
            if self._global_count >= self._global_limit:
                raise ConcurrencyLimitError(
                    "Global SSM concurrency limit reached "
                    f"({self._global_limit} commands in-flight). "
                    "Please retry after current diagnostics complete.",
                    instance_id=instance_id,
                )

            current = self._instance_counts.get(instance_id, 0)
            if current >= self._per_instance_limit:
                raise ConcurrencyLimitError(
                    f"A diagnostic is already running on instance "
                    f"{instance_id}. Per-instance concurrency limit "
                    f"({self._per_instance_limit}) reached.",
                    instance_id=instance_id,
                )

            self._global_count += 1
            self._instance_counts[instance_id] = current + 1

    def release(self, instance_id: str) -> None:
        """Release a concurrency slot for the given instance.

        Decrements both the global counter and the per-instance counter.

        Args:
            instance_id: The EC2 instance ID for which to release a slot.
        """
        with self._lock:
            self._global_count = max(0, self._global_count - 1)
            current = self._instance_counts.get(instance_id, 0)
            if current <= 1:
                self._instance_counts.pop(instance_id, None)
            else:
                self._instance_counts[instance_id] = current - 1

    @contextmanager
    def slot(self, instance_id: str):
        """Context manager that acquires and releases a concurrency slot.

        Ensures the slot is released even if an exception occurs during
        the diagnostic execution.

        Args:
            instance_id: The EC2 instance ID for which to hold a slot.

        Yields:
            None

        Raises:
            ConcurrencyLimitError: If a slot cannot be acquired.
        """
        self.acquire(instance_id)
        try:
            yield
        finally:
            self.release(instance_id)


__all__ = [
    "ConcurrencyLimitError",
    "SSMConcurrencyLimiter",
]
