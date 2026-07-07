"""
Pool Saturator Lambda — maintains persistent MySQL connections to saturate
the RDS connection pool for Scenario K (connection pool exhaustion demo).

Purpose
-------
This Lambda function is part of the GOAT Network Diagnostics demo suite,
Scenario K. It maintains 6 persistent MySQL connections to an RDS instance
configured with ``max_connections=5``, ensuring the connection pool is fully
saturated when the demo is performed. This causes subsequent connection
attempts (e.g., from svc-alpha via the ``db_connectivity_probe`` tool) to
fail with MySQL error 1040 ("Too many connections").

Trigger
-------
EventBridge rule fires every 5 minutes to keep the Lambda warm and
connections alive. Reserved concurrency of 1 ensures a single instance
maintains all connections.

Connection Lifecycle
--------------------
- Global ``connections`` list persists across warm invocations
- Dead connections are cleaned up at the start of each invocation
- New connections are opened until 6 are active (or pool is full)
- Keep-alive pings maintain existing connections

Environment Variables
---------------------
``DB_ENDPOINT``
    Required. The RDS MySQL endpoint hostname.
``DB_USERNAME``
    Required. The database username for authentication.
``DB_PASSWORD``
    Required. The database password for authentication.
``TARGET_CONNECTIONS``
    Optional. Number of connections to maintain. Defaults to 6.
``DB_PORT``
    Optional. MySQL port. Defaults to 3306.

Requirements: 1.3, 1.4, 1.7
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List

import pymysql
import pymysql.err

LOGGER = logging.getLogger()
LOGGER.setLevel(logging.INFO)

# Globals persist across warm invocations — this is intentional.
# The Lambda maintains persistent connections to saturate the pool.
connections: List[pymysql.connections.Connection] = []


def _clean_dead_connections() -> int:
    """Remove connections that are no longer open.

    Returns the number of connections removed.
    """
    before = len(connections)
    connections[:] = [c for c in connections if c.open]
    removed = before - len(connections)
    if removed > 0:
        LOGGER.info(
            "PoolSaturator: cleaned %d dead connections, %d remaining",
            removed,
            len(connections),
        )
    return removed


def _keep_alive() -> int:
    """Ping all active connections to keep them alive.

    Connections that fail the ping are removed from the pool.
    Returns the number of connections that failed the keep-alive.
    """
    failed = 0
    alive = []
    for conn in connections:
        try:
            conn.ping(reconnect=False)
            alive.append(conn)
        except Exception as exc:
            LOGGER.warning(
                "PoolSaturator: connection keep-alive failed, removing: %s",
                exc,
            )
            failed += 1
            try:
                conn.close()
            except Exception:
                pass
    connections[:] = alive
    return failed


def _open_connections(
    endpoint: str,
    username: str,
    password: str,
    port: int,
    target: int,
) -> Dict[str, Any]:
    """Open new connections until we reach the target count.

    Gracefully handles error 1040 (Too many connections) by stopping
    connection attempts — this means the pool is already saturated.

    Returns a dict with connection attempt results.
    """
    opened = 0
    pool_saturated_by_error = False

    while len(connections) < target:
        try:
            conn = pymysql.connect(
                host=endpoint,
                user=username,
                password=password,
                port=port,
                database="information_schema",
                connect_timeout=5,
                read_timeout=300,  # Keep alive for 5 minutes
            )
            connections.append(conn)
            opened += 1
            LOGGER.info(
                "PoolSaturator: opened connection %d/%d",
                len(connections),
                target,
            )
        except pymysql.err.OperationalError as e:
            if e.args[0] == 1040:
                # Too many connections — pool already saturated
                LOGGER.info(
                    "PoolSaturator: received error 1040 (Too many connections). "
                    "Pool is saturated with %d connections held.",
                    len(connections),
                )
                pool_saturated_by_error = True
                break
            else:
                LOGGER.error(
                    "PoolSaturator: unexpected OperationalError opening connection: %s",
                    e,
                )
                raise
        except Exception as e:
            LOGGER.error(
                "PoolSaturator: unexpected error opening connection: %s",
                e,
            )
            raise

    return {
        "opened": opened,
        "pool_saturated_by_error": pool_saturated_by_error,
    }


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Lambda entry point — maintain persistent MySQL connections to
    saturate the RDS connection pool.

    Args:
        event: EventBridge scheduled event payload (ignored).
        context: Lambda runtime context (unused).

    Returns:
        Dict with current connection pool state.
    """
    endpoint = os.environ.get("DB_ENDPOINT")
    username = os.environ.get("DB_USERNAME")
    password = os.environ.get("DB_PASSWORD")
    port = int(os.environ.get("DB_PORT", "3306"))
    target = int(os.environ.get("TARGET_CONNECTIONS", "6"))

    if not endpoint:
        raise RuntimeError(
            "PoolSaturator: DB_ENDPOINT environment variable is required"
        )
    if not username:
        raise RuntimeError(
            "PoolSaturator: DB_USERNAME environment variable is required"
        )
    if not password:
        raise RuntimeError(
            "PoolSaturator: DB_PASSWORD environment variable is required"
        )

    LOGGER.info(
        "PoolSaturator: invoked. endpoint=%s target=%d current_connections=%d",
        endpoint,
        target,
        len(connections),
    )

    # Phase 1: Clean up dead connections
    _clean_dead_connections()

    # Phase 2: Keep existing connections alive
    keep_alive_failures = _keep_alive()

    # Phase 3: Open new connections to reach target
    result = _open_connections(endpoint, username, password, port, target)

    active = len(connections)
    pool_saturated = active >= 5 or result["pool_saturated_by_error"]

    LOGGER.info(
        "PoolSaturator: complete. active_connections=%d pool_saturated=%s "
        "opened=%d keep_alive_failures=%d",
        active,
        pool_saturated,
        result["opened"],
        keep_alive_failures,
    )

    return {
        "active_connections": active,
        "target_connections": target,
        "pool_saturated": pool_saturated,
        "connections_opened_this_invocation": result["opened"],
        "keep_alive_failures": keep_alive_failures,
        "pool_saturated_by_error_1040": result["pool_saturated_by_error"],
    }
