"""
G.O.A.T. CUR Agent - AWS Cost and Usage Report queries via Amazon Athena
Plain Python handler with BedrockAgentCoreApp (sync entrypoint)

Receives structured JSON payloads from the orchestration agent's @tool functions,
routes to domain-specific handler functions, and queries CUR data via Athena.
NO Strands Agent SDK, NO Agent class, NO @tool decorators.
"""
import json
import logging
import os
import boto3
from datetime import datetime, timezone
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from aws_utils import get_region

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

app = BedrockAgentCoreApp()
AWS_REGION = get_region()

# Athena configuration — read from environment or use defaults
ATHENA_DATABASE = os.environ.get("ATHENA_DATABASE", "athenacurcfn_cost_and_usage_report")
ATHENA_TABLE = os.environ.get("ATHENA_TABLE", "cost_and_usage_report")
ATHENA_OUTPUT_LOCATION = os.environ.get("ATHENA_OUTPUT_LOCATION", "")
ATHENA_WORKGROUP = os.environ.get("ATHENA_WORKGROUP", "primary")

# Polling configuration for Athena query completion
ATHENA_POLL_INTERVAL_SECONDS = 1
ATHENA_MAX_POLL_SECONDS = 60

# Error message for missing CUR table
CUR_TABLE_NOT_FOUND_ERROR = (
    "The Cost and Usage Report (CUR) table was not found in the configured Athena "
    "database. CUR integration has not been configured for this account. "
    "To set up CUR:\n"
    "  1. Create a Cost and Usage Report in the AWS Billing console\n"
    "  2. Configure the report to deliver to Amazon S3\n"
    "  3. Set up an AWS Glue crawler or use the CUR CloudFormation template "
    "to create the Athena table\n"
    "  4. Set the ATHENA_DATABASE and ATHENA_TABLE environment variables "
    "for this agent\n"
    "For details, see: https://docs.aws.amazon.com/cur/latest/userguide/cur-create.html"
)


def _is_table_not_found_error(error: Exception) -> bool:
    """Check if the error indicates the CUR table does not exist."""
    error_str = str(error)
    return (
        "TABLE_NOT_FOUND" in error_str
        or "Table not found" in error_str
        or "does not exist" in error_str.lower()
        or "FAILED: SemanticException" in error_str
    )


import re

# Pattern for valid SQL identifiers and date values
_SAFE_IDENTIFIER = re.compile(r"^[a-zA-Z0-9_\-.:/ ]+$")
_SAFE_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}:\d{2})?$")


def _sanitize_sql_value(value: str, value_type: str = "string") -> str:
    """Sanitize a value for safe inclusion in SQL queries.

    Prevents SQL injection by validating format and escaping single quotes.
    Raises ValueError if the value contains suspicious patterns.
    """
    if not value:
        raise ValueError("Empty value")

    # Block obvious injection patterns
    dangerous = ["--", ";", "/*", "*/", "xp_", "UNION", "DROP", "DELETE", "INSERT", "UPDATE", "ALTER"]
    upper_val = value.upper()
    for pattern in dangerous:
        if pattern in upper_val:
            raise ValueError(f"Potentially unsafe SQL value: {value}")

    if value_type == "date":
        if not _SAFE_DATE.match(value):
            raise ValueError(f"Invalid date format: {value}")
        return value

    if value_type == "identifier":
        if not _SAFE_IDENTIFIER.match(value):
            raise ValueError(f"Invalid identifier: {value}")
        return value

    # Escape single quotes for string values
    return value.replace("'", "''")  # nosemgrep: B608


def _execute_athena_query(query: str) -> dict:
    """Execute an Athena query and wait for completion.

    Returns the query results or raises an exception on failure.
    Implements polling with configurable interval and timeout.
    """
    athena_client = boto3.client("athena", region_name=AWS_REGION)

    # Start query execution
    start_params = {
        "QueryString": query,
        "QueryExecutionContext": {"Database": ATHENA_DATABASE},
        "WorkGroup": ATHENA_WORKGROUP,
    }

    # Only set OutputLocation if explicitly configured via environment variable.
    # Otherwise, rely on the Athena workgroup's default output location.
    if ATHENA_OUTPUT_LOCATION:
        start_params["ResultConfiguration"] = {
            "OutputLocation": ATHENA_OUTPUT_LOCATION,
        }

    response = athena_client.start_query_execution(**start_params)
    query_execution_id = response["QueryExecutionId"]

    # Wait for query completion using boto3 waiter (no manual sleep/polling)
    waiter = athena_client.get_waiter("query_complete")
    try:
        waiter.wait(
            QueryExecutionId=query_execution_id,
            WaiterConfig={"Delay": ATHENA_POLL_INTERVAL_SECONDS, "MaxAttempts": ATHENA_MAX_POLL_SECONDS // ATHENA_POLL_INTERVAL_SECONDS},
        )
    except Exception as wait_err:
        # Check if query failed vs timed out
        status_response = athena_client.get_query_execution(QueryExecutionId=query_execution_id)
        state = status_response["QueryExecution"]["Status"]["State"]
        if state in ("FAILED", "CANCELLED"):
            reason = status_response["QueryExecution"]["Status"].get("StateChangeReason", "Unknown error")
            raise RuntimeError(f"Athena query {state.lower()}: {reason}") from wait_err
        # Timeout — cancel the query
        try:
            athena_client.stop_query_execution(QueryExecutionId=query_execution_id)
        except Exception:
            pass
        raise TimeoutError(
            f"Athena query did not complete within {ATHENA_MAX_POLL_SECONDS} seconds. "
            f"Query execution ID: {query_execution_id}"
        ) from wait_err

    # Query succeeded — fetch results
    results_response = athena_client.get_query_results(QueryExecutionId=query_execution_id)
    return _parse_athena_results(results_response)


def _parse_athena_results(results_response: dict) -> dict:
    """Parse Athena query results into a structured format.

    Returns a dict with 'columns' (list of column names) and 'rows' (list of dicts).
    """
    result_set = results_response.get("ResultSet", {})
    column_info = result_set.get("ResultSetMetadata", {}).get("ColumnInfo", [])
    columns = [col["Name"] for col in column_info]

    raw_rows = result_set.get("Rows", [])
    rows = []

    # First row is the header — skip it
    for raw_row in raw_rows[1:]:
        data = raw_row.get("Data", [])
        row = {}
        for i, cell in enumerate(data):
            col_name = columns[i] if i < len(columns) else f"col_{i}"
            row[col_name] = cell.get("VarCharValue", "")
        rows.append(row)

    return {"columns": columns, "rows": rows, "rowCount": len(rows)}


def _format_currency(amount: float, unit: str = "USD") -> str:
    """Format a numeric amount as a currency string."""
    return f"${amount:,.2f} {unit}"


def _validate_read_only_query(sql_query: str) -> str | None:
    """Validate that a SQL query is a single read-only statement.

    Returns None if the query is safe, or an error message string if it
    should be rejected. Guards against DDL/DML and multi-statement
    injection in LLM-generated queries passed to the raw query path.
    """
    stripped = sql_query.strip().rstrip(";").strip()
    if not stripped:
        return "Empty SQL query."

    # Must be a read-only statement.
    upper = stripped.upper()
    if not (upper.startswith("SELECT") or upper.startswith("WITH")):
        return "Only read-only SELECT/WITH queries are permitted against CUR data."

    # Reject statement-terminating semicolons (multi-statement payloads).
    # A trailing semicolon was already stripped above, so any remaining
    # semicolon indicates a second statement.
    if ";" in stripped:
        return "Multiple SQL statements are not permitted."

    # Block mutating / DDL keywords as whole words anywhere in the query.
    forbidden = (
        "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE",
        "TRUNCATE", "MERGE", "GRANT", "REVOKE", "REPLACE",
    )
    tokens = set(re.split(r"[^A-Z_]+", upper))
    blocked = sorted(tokens & set(forbidden))
    if blocked:
        return f"Disallowed SQL keyword(s) in query: {', '.join(blocked)}."

    return None


def handle_query_cur(params: dict) -> dict:
    """Execute a custom SQL query against the CUR data in Athena.

    Required params: query (SQL string)
    Optional params: maxRows
    """
    try:
        sql_query = params.get("query")
        if not sql_query:
            return {
                "success": False,
                "error": "A SQL query string is required for CUR data queries.",
            }

        # Safety: only allow read-only (SELECT/WITH) statements. The query
        # is generated by the orchestration agent's LLM output, so we must
        # reject any DDL/DML or multi-statement payloads before handing it
        # to Athena (prevents prompt-injection-driven data mutation).
        validation_error = _validate_read_only_query(sql_query)
        if validation_error:
            logger.warning("Rejected unsafe CUR query: %s", validation_error)
            return {"success": False, "error": validation_error}

        max_rows = int(params.get("maxRows") or params.get("max_rows", 100))
        if "LIMIT" not in sql_query.upper():
            sql_query = f"{sql_query.rstrip(';')} LIMIT {max_rows}"

        results = _execute_athena_query(sql_query)

        return {
            "success": True,
            "domain": "cur",
            "data": results,
            "formattedText": _format_query_results(results),
            "metadata": {
                "sourceApi": "athena:StartQueryExecution",
                "queryTimestamp": datetime.now(timezone.utc).isoformat(),
                "dataFreshness": "depends on CUR delivery schedule (up to 24 hours)",
            },
        }

    except (RuntimeError, TimeoutError) as e:
        if _is_table_not_found_error(e):
            return {"success": False, "error": CUR_TABLE_NOT_FOUND_ERROR}
        return {"success": False, "error": f"CUR query failed: {str(e)}"}
    except Exception as e:
        if _is_table_not_found_error(e):
            return {"success": False, "error": CUR_TABLE_NOT_FOUND_ERROR}
        return {"success": False, "error": f"CUR query failed: {str(e)}"}


def handle_resource_costs(params: dict) -> dict:
    """Query resource-level cost breakdown from CUR data.

    Optional params: startDate, endDate, service, resourceId, maxRows
    """
    try:
        start_date = params.get("startDate") or params.get("start_date")
        end_date = params.get("endDate") or params.get("end_date")
        service = params.get("service")
        resource_id = params.get("resourceId") or params.get("resource_id")
        max_rows = int(params.get("maxRows") or params.get("max_rows", 50))

        # Build the SQL query with sanitized inputs
        conditions = []
        if start_date:
            safe_start = _sanitize_sql_value(start_date, "date")
            conditions.append(
                f"line_item_usage_start_date >= TIMESTAMP '{safe_start}'"
            )
        if end_date:
            safe_end = _sanitize_sql_value(end_date, "date")
            conditions.append(
                f"line_item_usage_end_date <= TIMESTAMP '{safe_end}'"
            )
        if service:
            safe_service = _sanitize_sql_value(service)
            conditions.append(f"product_product_name = '{safe_service}'")
        if resource_id:
            safe_resource = _sanitize_sql_value(resource_id)
            conditions.append(f"line_item_resource_id = '{safe_resource}'")

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        sql_query = (  # nosec B608 — conditions built from validated enum values, not raw user SQL
            f"SELECT "
            f"line_item_resource_id, "
            f"product_product_name, "
            f"line_item_usage_type, "
            f"SUM(CAST(line_item_unblended_cost AS DOUBLE)) AS total_cost, "
            f"SUM(CAST(line_item_usage_amount AS DOUBLE)) AS total_usage, "
            f"line_item_currency_code "
            f"FROM {ATHENA_TABLE} "
            f"{where_clause} "
            f"GROUP BY line_item_resource_id, product_product_name, "
            f"line_item_usage_type, line_item_currency_code "
            f"ORDER BY total_cost DESC "
            f"LIMIT {max_rows}"
        )

        results = _execute_athena_query(sql_query)

        return {
            "success": True,
            "domain": "cur",
            "data": results,
            "formattedText": _format_resource_costs(results),
            "metadata": {
                "sourceApi": "athena:StartQueryExecution",
                "queryTimestamp": datetime.now(timezone.utc).isoformat(),
                "dataFreshness": "depends on CUR delivery schedule (up to 24 hours)",
            },
        }

    except (RuntimeError, TimeoutError) as e:
        if _is_table_not_found_error(e):
            return {"success": False, "error": CUR_TABLE_NOT_FOUND_ERROR}
        return {"success": False, "error": f"Resource costs query failed: {str(e)}"}
    except Exception as e:
        if _is_table_not_found_error(e):
            return {"success": False, "error": CUR_TABLE_NOT_FOUND_ERROR}
        return {"success": False, "error": f"Resource costs query failed: {str(e)}"}


def handle_usage_patterns(params: dict) -> dict:
    """Analyze usage patterns from CUR data grouped by service and time.

    Optional params: startDate, endDate, service, granularity (DAILY/MONTHLY), maxRows
    """
    try:
        start_date = params.get("startDate") or params.get("start_date")
        end_date = params.get("endDate") or params.get("end_date")
        service = params.get("service")
        granularity = params.get("granularity", "DAILY").upper()
        max_rows = int(params.get("maxRows") or params.get("max_rows", 100))

        # Determine date truncation based on granularity
        if granularity == "MONTHLY":
            date_trunc = "DATE_TRUNC('month', line_item_usage_start_date)"
        else:
            date_trunc = "DATE_TRUNC('day', line_item_usage_start_date)"

        conditions = []
        if start_date:
            safe_start = _sanitize_sql_value(start_date, "date")
            conditions.append(
                f"line_item_usage_start_date >= TIMESTAMP '{safe_start}'"
            )
        if end_date:
            safe_end = _sanitize_sql_value(end_date, "date")
            conditions.append(
                f"line_item_usage_end_date <= TIMESTAMP '{safe_end}'"
            )
        if service:
            safe_service = _sanitize_sql_value(service)
            conditions.append(f"product_product_name = '{safe_service}'")

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        sql_query = (  # nosec B608 — conditions built from validated enum values, not raw user SQL
            f"SELECT "
            f"{date_trunc} AS usage_period, "
            f"product_product_name, "
            f"SUM(CAST(line_item_unblended_cost AS DOUBLE)) AS total_cost, "
            f"SUM(CAST(line_item_usage_amount AS DOUBLE)) AS total_usage, "
            f"COUNT(*) AS line_item_count "
            f"FROM {ATHENA_TABLE} "
            f"{where_clause} "
            f"GROUP BY {date_trunc}, product_product_name "
            f"ORDER BY usage_period DESC, total_cost DESC "
            f"LIMIT {max_rows}"
        )

        results = _execute_athena_query(sql_query)

        return {
            "success": True,
            "domain": "cur",
            "data": results,
            "formattedText": _format_usage_patterns(results, granularity),
            "metadata": {
                "sourceApi": "athena:StartQueryExecution",
                "queryTimestamp": datetime.now(timezone.utc).isoformat(),
                "dataFreshness": "depends on CUR delivery schedule (up to 24 hours)",
            },
        }

    except (RuntimeError, TimeoutError) as e:
        if _is_table_not_found_error(e):
            return {"success": False, "error": CUR_TABLE_NOT_FOUND_ERROR}
        return {"success": False, "error": f"Usage patterns query failed: {str(e)}"}
    except Exception as e:
        if _is_table_not_found_error(e):
            return {"success": False, "error": CUR_TABLE_NOT_FOUND_ERROR}
        return {"success": False, "error": f"Usage patterns query failed: {str(e)}"}


def _format_query_results(results: dict) -> str:
    """Format raw Athena query results as a readable table."""
    rows = results.get("rows", [])
    columns = results.get("columns", [])

    if not rows:
        return "No results returned from the CUR query."

    lines = [f"CUR Query Results ({len(rows)} rows)", ""]

    # Header
    lines.append("  " + " | ".join(columns))
    lines.append("  " + "-" * (sum(len(c) + 3 for c in columns)))

    # Rows (show up to 20 for formatted output)
    for row in rows[:20]:
        values = [str(row.get(col, "")) for col in columns]
        lines.append("  " + " | ".join(values))

    if len(rows) > 20:
        lines.append(f"  ... and {len(rows) - 20} more rows")

    return "\n".join(lines)


def _format_resource_costs(results: dict) -> str:
    """Format resource-level cost breakdown."""
    rows = results.get("rows", [])

    if not rows:
        return "No resource cost data found for the specified criteria."

    lines = [f"Resource Cost Breakdown ({len(rows)} resources)", ""]

    for row in rows[:20]:
        resource_id = row.get("line_item_resource_id", "N/A")
        service = row.get("product_product_name", "N/A")
        usage_type = row.get("line_item_usage_type", "N/A")
        total_cost = float(row.get("total_cost", 0))
        total_usage = row.get("total_usage", "N/A")
        currency = row.get("line_item_currency_code", "USD")

        lines.append(
            f"  Resource: {resource_id} | Service: {service} | "
            f"Usage Type: {usage_type} | "
            f"Cost: {_format_currency(total_cost, currency)} | "
            f"Usage: {total_usage}"
        )

    if len(rows) > 20:
        lines.append(f"  ... and {len(rows) - 20} more resources")

    return "\n".join(lines)


def _format_usage_patterns(results: dict, granularity: str) -> str:
    """Format usage pattern analysis results."""
    rows = results.get("rows", [])

    if not rows:
        return "No usage pattern data found for the specified criteria."

    lines = [f"Usage Patterns ({granularity}, {len(rows)} entries)", ""]

    for row in rows[:20]:
        period = row.get("usage_period", "N/A")
        service = row.get("product_product_name", "N/A")
        total_cost = float(row.get("total_cost", 0))
        total_usage = row.get("total_usage", "N/A")
        line_items = row.get("line_item_count", "N/A")

        lines.append(
            f"  {period} | {service} | "
            f"Cost: {_format_currency(total_cost)} | "
            f"Usage: {total_usage} | "
            f"Line Items: {line_items}"
        )

    if len(rows) > 20:
        lines.append(f"  ... and {len(rows) - 20} more entries")

    return "\n".join(lines)


def handle_action(action: str, params: dict) -> dict:
    """Route to the appropriate handler based on action."""
    handlers = {
        "query_cur_data": handle_query_cur,
        "get_resource_costs": handle_resource_costs,
        "analyze_usage_patterns": handle_usage_patterns,
    }
    handler = handlers.get(action)
    if not handler:
        return {
            "success": False,
            "error": f"Unknown action: {action}. Supported actions: {', '.join(handlers.keys())}",
        }
    return handler(params)


@app.entrypoint
def main_handler(payload):
    """
    Main entry point for the CUR Agent.
    Receives JSON payload, routes to handler based on action field.
    Synchronous — returns dict, not async generator.

    Payload format: {"action": "query_cur_data", "params": {...}}
    """
    try:
        if isinstance(payload, str):
            payload = json.loads(payload)

        action = payload.get("action")
        if not action:
            if payload.get("prompt"):
                logger.warning("Received raw prompt instead of structured action, defaulting to get_resource_costs")
            else:
                logger.warning("No action in payload, defaulting to get_resource_costs")
            action = "get_resource_costs"

        params = payload.get("params", {})
        return handle_action(action, params)

    except Exception as e:
        return {"success": False, "error": f"Request failed: {str(e)}"}


if __name__ == "__main__":
    app.run()
