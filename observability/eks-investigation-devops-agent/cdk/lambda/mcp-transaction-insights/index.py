"""
Payment Transaction Insights — Lambda handler for AgentCore Gateway.

Each tool is invoked by the Gateway as a separate Lambda invocation.
The event contains the tool name and arguments from the MCP tools/call request.
"""

import os
import json
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import boto3
import pg8000.native

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Lazy-loaded DB config and credentials
_db_config = None
_db_creds = None


def _init():
    global _db_config
    if _db_config:
        return
    _db_config = {
        "host": os.environ.get("DB_HOST", "localhost"),
        "port": int(os.environ.get("DB_PORT", "5432")),
        "database": os.environ.get("DB_NAME", "paymentdb"),
        "ssl_mode": os.environ.get("DB_SSL_MODE", "require"),
        "secret_arn": os.environ.get("DB_SECRET_ARN", ""),
    }


def _get_creds():
    global _db_creds
    if _db_creds:
        return _db_creds
    _init()
    if _db_config["secret_arn"]:
        client = boto3.client("secretsmanager")
        resp = client.get_secret_value(SecretId=_db_config["secret_arn"])
        secret = json.loads(resp["SecretString"])
        _db_creds = {"username": secret["username"], "password": secret["password"]}
    else:
        _db_creds = {
            "username": os.environ.get("DB_USER", "mcp_readonly"),
            "password": os.environ.get("DB_PASSWORD", ""),
        }
    return _db_creds


def _query(sql, **params):
    _init()
    creds = _get_creds()
    conn = pg8000.native.Connection(
        host=_db_config["host"], port=_db_config["port"],
        database=_db_config["database"],
        user=creds["username"], password=creds["password"],
        ssl_context=True if _db_config["ssl_mode"] == "require" else None,
        timeout=5,
    )
    try:
        conn.run("SET default_transaction_read_only = on")
        rows = conn.run(sql, **params) if params else conn.run(sql)
        cols = [d["name"] for d in conn.columns] if conn.columns else []
        result = []
        for row in rows:
            d = {}
            for i, col in enumerate(cols):
                v = row[i]
                if isinstance(v, Decimal):
                    v = float(v)
                elif hasattr(v, "isoformat"):
                    v = v.isoformat()
                d[col] = v
            result.append(d)
        return result
    finally:
        conn.close()


def get_transaction_summary(minutes=30):
    minutes = min(max(int(minutes), 1), 60)
    rows = _query(
        "SELECT status, COUNT(*) AS count, COALESCE(SUM(amount), 0) AS total_amount, "
        "MIN(created_at) AS earliest, MAX(created_at) AS latest "
        "FROM transactions WHERE created_at >= NOW() - make_interval(mins => :minutes) "
        "GROUP BY status ORDER BY count DESC", minutes=minutes)
    return {
        "window_minutes": minutes,
        "queried_at": datetime.now(timezone.utc).isoformat(),
        "total_transactions": sum(r["count"] for r in rows),
        "total_amount": sum(r["total_amount"] for r in rows),
        "by_status": rows,
    }


def get_recent_failures(minutes=30, limit=20):
    minutes = min(max(int(minutes), 1), 60)
    limit = min(max(int(limit), 1), 50)
    rows = _query(
        "SELECT t.id, t.merchant_id, m.name AS merchant_name, t.amount, t.currency, "
        "t.status, t.error_code, t.error_message, t.correlation_id, t.created_at "
        "FROM transactions t LEFT JOIN merchants m ON t.merchant_id = m.id "
        "WHERE t.status = 'FAILED' AND t.created_at >= NOW() - make_interval(mins => :minutes) "
        "ORDER BY t.created_at DESC LIMIT :lim", minutes=minutes, lim=limit)
    return {
        "window_minutes": minutes,
        "queried_at": datetime.now(timezone.utc).isoformat(),
        "failure_count": len(rows),
        "failures": rows,
    }


def get_processing_gap():
    cr = _query("SELECT MAX(updated_at) AS v FROM transactions WHERE status = 'CAPTURED'")
    ar = _query("SELECT MAX(created_at) AS v FROM transactions")
    sr = _query("SELECT COUNT(*) AS c, COALESCE(SUM(amount),0) AS a FROM transactions "
                "WHERE status='AUTHORIZED' AND created_at <= NOW() - INTERVAL '5 minutes'")
    now = datetime.now(timezone.utc)
    lc = cr[0]["v"] if cr else None
    gap = None
    if lc:
        lc_dt = datetime.fromisoformat(lc) if isinstance(lc, str) else lc
        if lc_dt.tzinfo is None:
            lc_dt = lc_dt.replace(tzinfo=timezone.utc)
        gap = int((now - lc_dt).total_seconds())
    return {
        "queried_at": now.isoformat(),
        "last_successful_capture": lc,
        "gap_seconds": gap,
        "gap_human": f"{gap // 60}m {gap % 60}s" if gap else "no captures found",
        "last_transaction_any_status": ar[0]["v"] if ar else None,
        "stuck_authorized": {
            "count": sr[0]["c"] if sr else 0,
            "total_amount": sr[0]["a"] if sr else 0,
        },
    }


def get_incident_impact(start_time, end_time, baseline_hours=1):
    """Analyze business impact of an incident over an absolute time range.

    Uses the transaction_events table (state transition log) when available,
    falling back to the transactions table for basic counts.
    """
    # Validate and parse ISO timestamps
    try:
        st = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        et = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
    except (ValueError, AttributeError) as e:
        return {"error": "Invalid timestamp format. Use ISO 8601 (e.g. 2026-04-24T14:00:00Z).", "detail": str(e)}

    if et <= st:
        return {"error": "end_time must be after start_time."}

    baseline_hours = min(max(float(baseline_hours), 0.5), 24)
    duration_min = int((et - st).total_seconds() / 60)

    # --- Transactions created during the incident window ---
    during = _query(
        "SELECT status, COUNT(*) AS count, COALESCE(SUM(amount), 0) AS total_amount "
        "FROM transactions WHERE created_at >= :st AND created_at < :et "
        "GROUP BY status ORDER BY count DESC", st=st, et=et)

    # --- Baseline: average rate over the specified period before the incident ---
    baseline_minutes = baseline_hours * 60
    baseline = _query(
        "SELECT COUNT(*) AS count, COALESCE(SUM(amount), 0) AS total_amount "
        "FROM transactions WHERE status = 'CAPTURED' "
        "AND created_at >= :baseline_start AND created_at < :st",
        baseline_start=st - timedelta(hours=baseline_hours), st=st)

    baseline_count = baseline[0]["count"] if baseline else 0
    baseline_amount = baseline[0]["total_amount"] if baseline else 0

    # Rate per minute, then projected to incident duration
    rate_per_min_count = baseline_count / baseline_minutes if baseline_count > 0 else 0
    rate_per_min_amount = baseline_amount / baseline_minutes if baseline_amount > 0 else 0
    expected_count = round(rate_per_min_count * duration_min, 1)
    expected_amount = round(rate_per_min_amount * duration_min, 2)

    # --- Affected merchants ---
    merchants = _query(
        "SELECT m.name AS merchant_name, COUNT(*) AS txn_count, "
        "COALESCE(SUM(t.amount), 0) AS total_amount "
        "FROM transactions t LEFT JOIN merchants m ON t.merchant_id = m.id "
        "WHERE t.created_at >= :st AND t.created_at < :et "
        "AND t.status IN ('FAILED', 'AUTHORIZED', 'PENDING') "
        "GROUP BY m.name ORDER BY total_amount DESC", st=st, et=et)

    # --- State transition analysis (if transaction_events table exists) ---
    transition_analysis = None
    try:
        # Average time from AUTHORIZED to CAPTURED during the window
        avg_capture = _query(
            "SELECT AVG(EXTRACT(EPOCH FROM (cap.occurred_at - auth.occurred_at))) AS avg_seconds, "
            "COUNT(*) AS sample_count "
            "FROM transaction_events auth "
            "JOIN transaction_events cap ON auth.transaction_id = cap.transaction_id "
            "WHERE auth.status = 'AUTHORIZED' AND cap.status = 'CAPTURED' "
            "AND auth.occurred_at >= :st AND auth.occurred_at < :et",
            st=st, et=et)

        # Same metric for baseline period
        avg_capture_baseline = _query(
            "SELECT AVG(EXTRACT(EPOCH FROM (cap.occurred_at - auth.occurred_at))) AS avg_seconds, "
            "COUNT(*) AS sample_count "
            "FROM transaction_events auth "
            "JOIN transaction_events cap ON auth.transaction_id = cap.transaction_id "
            "WHERE auth.status = 'AUTHORIZED' AND cap.status = 'CAPTURED' "
            "AND auth.occurred_at >= :baseline_start AND auth.occurred_at < :st",
            baseline_start=st - timedelta(hours=baseline_hours), st=st)

        # Transactions authorized during window that were never captured
        never_captured = _query(
            "SELECT COUNT(*) AS count, COALESCE(SUM(t.amount), 0) AS total_amount "
            "FROM transaction_events auth "
            "JOIN transactions t ON auth.transaction_id = t.id "
            "WHERE auth.status = 'AUTHORIZED' AND auth.occurred_at >= :st AND auth.occurred_at < :et "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM transaction_events cap "
            "  WHERE cap.transaction_id = auth.transaction_id AND cap.status = 'CAPTURED'"
            ")", st=st, et=et)

        transition_analysis = {
            "avg_capture_time_seconds": avg_capture[0]["avg_seconds"] if avg_capture and avg_capture[0]["avg_seconds"] else None,
            "avg_capture_time_baseline_seconds": avg_capture_baseline[0]["avg_seconds"] if avg_capture_baseline and avg_capture_baseline[0]["avg_seconds"] else None,
            "capture_sample_count": avg_capture[0]["sample_count"] if avg_capture else 0,
            "never_captured": {
                "count": never_captured[0]["count"] if never_captured else 0,
                "total_amount": never_captured[0]["total_amount"] if never_captured else 0,
            },
        }
    except Exception:
        # transaction_events table may not exist yet — graceful fallback
        logger.info("transaction_events table not available, skipping transition analysis")

    total_during = sum(r["count"] for r in during)
    total_amount_during = sum(r["total_amount"] for r in during)

    result = {
        "queried_at": datetime.now(timezone.utc).isoformat(),
        "incident_window": {"start": st.isoformat(), "end": et.isoformat(), "duration_minutes": duration_min},
        "during_incident": {
            "total_transactions": total_during,
            "total_amount": total_amount_during,
            "by_status": during,
        },
        "baseline_comparison": {
            "method": f"{baseline_hours}-hour average projected to incident duration",
            "baseline_period_hours": baseline_hours,
            "baseline_captured_count": baseline_count,
            "baseline_captured_amount": baseline_amount,
            "rate_per_minute": {"count": round(rate_per_min_count, 2), "amount": round(rate_per_min_amount, 2)},
            "expected_during_window": {"count": expected_count, "amount": expected_amount},
            "estimated_lost_transactions": max(0, round(expected_count - total_during, 1)),
            "estimated_lost_revenue": max(0, round(expected_amount - total_amount_during, 2)),
        },
        "affected_merchants": merchants,
        "merchant_count": len(merchants),
    }

    if transition_analysis:
        result["transition_analysis"] = transition_analysis

    return result


TOOLS = {
    "get_transaction_summary": get_transaction_summary,
    "get_recent_failures": get_recent_failures,
    "get_processing_gap": get_processing_gap,
    "get_incident_impact": get_incident_impact,
}


def handler(event, context):
    """AgentCore Gateway Lambda handler.
    
    The Gateway sends arguments in the event and the tool name in context.client_context.
    """
    logger.info("Event: %s", json.dumps(event, default=str))

    # Extract tool name from Gateway context (format: targetName___toolName)
    tool_name = ""
    try:
        full_tool_name = context.client_context.custom.get("bedrockAgentCoreToolName", "")
        delimiter = "___"
        if delimiter in full_tool_name:
            tool_name = full_tool_name[full_tool_name.index(delimiter) + len(delimiter):]
        else:
            tool_name = full_tool_name
    except (AttributeError, TypeError):
        # Direct invocation (not via Gateway) — fall back to event-based routing
        tool_name = event.pop("name", "") or event.pop("toolName", "")

    logger.info("Tool: %s", tool_name)

    # For direct invocation, arguments are nested; for Gateway, event IS the arguments
    if "arguments" in event:
        arguments = event["arguments"]
        if isinstance(arguments, str):
            arguments = json.loads(arguments)
    else:
        arguments = event

    if tool_name not in TOOLS:
        return {"error": f"Unknown tool: {tool_name}", "available": list(TOOLS.keys())}

    try:
        result = TOOLS[tool_name](**arguments)
        return result
    except Exception as e:
        logger.error("Tool %s failed: %s", tool_name, e)
        return {"error": "tool_execution_failed", "message": str(e)}
