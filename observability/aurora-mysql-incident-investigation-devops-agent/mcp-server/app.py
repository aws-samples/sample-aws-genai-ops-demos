import json
import uuid


TOOLS = [
    {
        "name": "get_service_dependencies",
        "description": "Returns the applications, teams, and stakeholders that depend on the specified Aurora database resource.",
        "inputSchema": {
            "type": "object",
            "properties": {"resource_id": {"type": "string", "description": "The Aurora cluster or instance identifier"}},
            "required": ["resource_id"],
        },
    },
    {
        "name": "get_cost_impact",
        "description": "Calculates the financial impact of an Aurora database incident including revenue loss and SLA breach status.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "resource_id": {"type": "string", "description": "The Aurora cluster or instance identifier"},
                "downtime_minutes": {"type": "number", "description": "Duration of degradation/downtime in minutes"},
            },
            "required": ["resource_id", "downtime_minutes"],
        },
    },
    {
        "name": "get_compliance_status",
        "description": "Returns compliance framework status, data classification, and incident reporting requirements for the Aurora resource.",
        "inputSchema": {
            "type": "object",
            "properties": {"resource_id": {"type": "string", "description": "The Aurora cluster or instance identifier"}},
            "required": ["resource_id"],
        },
    },
    {
        "name": "get_maintenance_context",
        "description": "Returns recent change/maintenance history for the Aurora resource to help correlate incidents with recent changes.",
        "inputSchema": {
            "type": "object",
            "properties": {"resource_id": {"type": "string", "description": "The Aurora cluster or instance identifier"}},
            "required": ["resource_id"],
        },
    },
]


def get_service_dependencies(resource_id):
    return {
        "resource_id": resource_id,
        "dependent_services": [
            {"name": "checkout-service", "criticality": "CRITICAL", "type": "OLTP writer", "access": "read-write", "connections": "connection pool max 120"},
            {"name": "order-history-api", "criticality": "HIGH", "type": "read replica consumer", "access": "read-only", "connections": "reader endpoint"},
            {"name": "analytics-etl", "criticality": "MEDIUM", "type": "nightly batch", "access": "read-only", "connections": "reader endpoint"},
        ],
        "on_call_team": "Data Platform / DBRE",
        "escalation_contact": "Director of Platform Engineering",
        "total_end_users_affected": "~18,000 active shopper sessions",
        "primary_workload": "e-commerce checkout and order management (OLTP)",
    }


def get_cost_impact(resource_id, downtime_minutes):
    revenue_per_minute = 5100
    sla_threshold = 20
    sla_penalty = 75000
    return {
        "resource_id": resource_id,
        "downtime_minutes": downtime_minutes,
        "revenue_per_minute_usd": revenue_per_minute,
        "avg_orders_per_min": 610,
        "estimated_revenue_loss_usd": revenue_per_minute * downtime_minutes,
        "failed_writes_note": "Write unavailability blocks checkout; read degradation slows order history and search.",
        "sla_penalty_threshold_min": sla_threshold,
        "sla_penalty_usd": sla_penalty,
        "sla_breach": downtime_minutes >= sla_threshold,
        "database_availability_sla": "99.95% (Multi-AZ Aurora)",
    }


def get_compliance_status(resource_id):
    return {
        "resource_id": resource_id,
        "frameworks": [
            {"name": "PCI-DSS", "status": "active", "mandatory_reporting_threshold_min": 15},
            {"name": "SOC 2 Type II", "status": "active", "mandatory_reporting_threshold_min": 60},
            {"name": "GDPR", "status": "active", "note": "Customer PII stored in customers table"},
        ],
        "data_classification": "Confidential — payment tokens, order and customer PII",
        "encryption_at_rest": "Enabled (AWS KMS)",
        "incident_response_policy": "IR-DB-2026-014",
    }


def get_maintenance_context(resource_id):
    return {
        "resource_id": resource_id,
        "recent_changes": [
            {"date": "T-2 days", "type": "parameter group change", "detail": "max_connections lowered during cost review", "risk": "MEDIUM"},
            {"date": "T-6 days", "type": "app deploy", "detail": "checkout-service v4.2 — new ORM connection pool defaults", "risk": "HIGH"},
            {"date": "T-14 days", "type": "engine minor upgrade", "detail": "Aurora MySQL 3.07 -> 3.08", "risk": "LOW"},
        ],
        "next_maintenance_window": "Sunday 02:00–04:00 UTC",
        "change_freeze_active": False,
    }


TOOL_HANDLERS = {
    "get_service_dependencies": lambda args: get_service_dependencies(args["resource_id"]),
    "get_cost_impact": lambda args: get_cost_impact(args["resource_id"], args["downtime_minutes"]),
    "get_compliance_status": lambda args: get_compliance_status(args["resource_id"]),
    "get_maintenance_context": lambda args: get_maintenance_context(args["resource_id"]),
}


def handle_jsonrpc(request):
    method = request.get("method")
    req_id = request.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "aurora-devops-mcp-server", "version": "1.0.0"},
            },
        }

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}}

    if method == "tools/call":
        tool_name = request["params"]["name"]
        args = request["params"].get("arguments", {})
        handler = TOOL_HANDLERS.get(tool_name)
        if not handler:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"},
            }
        result = handler(args)
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"content": [{"type": "text", "text": json.dumps(result)}]},
        }

    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Unknown method: {method}"}}


def lambda_handler(event, context):
    headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, Mcp-Session-Id, x-api-key",
    }

    if event.get("httpMethod") == "OPTIONS":
        return {"statusCode": 204, "headers": headers, "body": ""}

    body = json.loads(event.get("body", "{}"))
    session_id = (event.get("headers") or {}).get("mcp-session-id") or str(uuid.uuid4())

    response = handle_jsonrpc(body)

    headers["Content-Type"] = "application/json"
    headers["Mcp-Session-Id"] = session_id

    return {"statusCode": 200, "headers": headers, "body": json.dumps(response)}
