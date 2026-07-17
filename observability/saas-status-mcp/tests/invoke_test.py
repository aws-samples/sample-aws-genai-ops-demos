"""Test: invoke the deployed MCP server via bedrock-agentcore SDK.

Based on the agentcore-samples invoke pattern:
https://github.com/awslabs/agentcore-samples/blob/main/01-features/02-host-your-agent/01-runtime/02-hosting-tools/01-mcp-server-basics/invoke.py
"""

import json
import boto3

REGION = "eu-west-3"
RUNTIME_ARN = "arn:aws:bedrock-agentcore:eu-west-3:517675598740:runtime/saas_status_mcp-yOYcLm8Tlg"

client = boto3.client("bedrock-agentcore", region_name=REGION)


def send_mcp_rpc(method: str, params: dict, rpc_id: int = 1) -> dict:
    """Send an MCP JSON-RPC message to the deployed server."""
    rpc_message = {
        "jsonrpc": "2.0",
        "method": method,
        "id": rpc_id,
        "params": params,
    }

    response = client.invoke_agent_runtime(
        agentRuntimeArn=RUNTIME_ARN,
        payload=json.dumps(rpc_message).encode("utf-8"),
        contentType="application/json",
        accept="application/json, text/event-stream",
    )

    body = response["response"].read().decode("utf-8")
    # streamable-http may return an SSE frame ("data: {...}")
    if body.startswith("data:"):
        body = body.split("data:", 1)[1].strip()
    return json.loads(body)


def main():
    print(f"MCP Server: {RUNTIME_ARN}\n")

    # 1. Initialize
    print("--- Initialize")
    result = send_mcp_rpc(
        "initialize",
        {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "1.0.0"},
        },
        rpc_id=1,
    )
    print(f"    Server: {json.dumps(result.get('result', {}).get('serverInfo', {}))}\n")

    # 2. List tools
    print("--- tools/list")
    result = send_mcp_rpc("tools/list", {}, rpc_id=2)
    tools = result.get("result", {}).get("tools", [])
    for t in tools:
        print(f"    * {t['name']}: {t.get('description', '')[:70]}")
    print()

    # 3. list_providers
    print("--- tools/call: list_providers")
    result = send_mcp_rpc("tools/call", {"name": "list_providers", "arguments": {}}, rpc_id=3)
    print(f"    {json.dumps(result.get('result', {}))[:600]}\n")

    # 4. get_service_status(mongodb)
    print("--- tools/call: get_service_status(mongodb)")
    result = send_mcp_rpc(
        "tools/call",
        {"name": "get_service_status", "arguments": {"provider": "mongodb"}},
        rpc_id=4,
    )
    print(f"    {json.dumps(result.get('result', {}))[:600]}\n")

    # 5. check_all_dependencies
    print("--- tools/call: check_all_dependencies([snowflake, datadog, mongodb])")
    result = send_mcp_rpc(
        "tools/call",
        {"name": "check_all_dependencies", "arguments": {"providers": ["snowflake", "datadog", "mongodb"]}},
        rpc_id=5,
    )
    print(f"    {json.dumps(result.get('result', {}))[:800]}\n")


if __name__ == "__main__":
    main()
