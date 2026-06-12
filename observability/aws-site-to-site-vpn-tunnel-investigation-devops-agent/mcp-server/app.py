import json
import uuid


TOOLS = [
    {
        "name": "get_service_dependencies",
        "description": "Returns services and stakeholders depending on the specified VPN resource.",
        "inputSchema": {
            "type": "object",
            "properties": {"resource_id": {"type": "string", "description": "The VPN resource identifier"}},
            "required": ["resource_id"],
        },
    },
    {
        "name": "get_cost_impact",
        "description": "Calculates financial impact of VPN downtime including revenue loss and SLA breach status.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "resource_id": {"type": "string", "description": "The VPN resource identifier"},
                "downtime_minutes": {"type": "number", "description": "Duration of downtime in minutes"},
            },
            "required": ["resource_id", "downtime_minutes"],
        },
    },
    {
        "name": "get_compliance_status",
        "description": "Returns compliance framework status and incident reporting requirements for the VPN resource.",
        "inputSchema": {
            "type": "object",
            "properties": {"resource_id": {"type": "string", "description": "The VPN resource identifier"}},
            "required": ["resource_id"],
        },
    },
]


def get_service_dependencies(resource_id):
    return {
        "resource_id": resource_id,
        "dependent_services": [
            {"name": "payment-gateway", "criticality": "CRITICAL", "type": "API", "throughput": "520 txn/min"},
            {"name": "order-api", "criticality": "CRITICAL", "type": "API", "throughput": "230 txn/min"},
            {"name": "inventory-sync", "criticality": "HIGH", "type": "batch", "sync_interval": "30s"},
        ],
        "on_call_team": "Platform Engineering",
        "escalation_contact": "VP of Engineering",
        "total_end_users_affected": "~12,000 active sessions",
    }


def get_cost_impact(resource_id, downtime_minutes):
    revenue_per_minute = 4200
    sla_threshold = 30
    sla_penalty = 50000
    return {
        "resource_id": resource_id,
        "downtime_minutes": downtime_minutes,
        "revenue_per_minute_usd": revenue_per_minute,
        "avg_transactions_per_min": 847,
        "estimated_revenue_loss_usd": revenue_per_minute * downtime_minutes,
        "sla_penalty_threshold_min": sla_threshold,
        "sla_penalty_usd": sla_penalty,
        "sla_breach": downtime_minutes >= sla_threshold,
        "annual_vpn_availability_sla": "99.95%",
    }


def get_compliance_status(resource_id):
    return {
        "resource_id": resource_id,
        "frameworks": [
            {"name": "PCI-DSS", "status": "active", "mandatory_reporting_threshold_min": 15},
            {"name": "SOC 2 Type II", "status": "active", "mandatory_reporting_threshold_min": 60},
        ],
        "data_classification": "Confidential — contains payment and PII data",
        "incident_response_policy": "IR-2024-007",
    }


TOOL_HANDLERS = {
    "get_service_dependencies": lambda args: get_service_dependencies(args["resource_id"]),
    "get_cost_impact": lambda args: get_cost_impact(args["resource_id"], args["downtime_minutes"]),
    "get_compliance_status": lambda args: get_compliance_status(args["resource_id"]),
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
                "serverInfo": {"name": "aws-vpn-devops-mcp-server", "version": "1.0.0"},
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
