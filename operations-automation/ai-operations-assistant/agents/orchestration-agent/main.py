"""
G.O.A.T. Orchestration Agent - Strands Agent SDK + @tool pattern
Uses LLM reasoning to classify intent, invoke sub-agents, and correlate results.
Build: 2026-04-03T18:00

This is the ONLY agent that uses Strands Agent SDK. It takes natural language input,
uses LLM reasoning to classify intent, decides which sub-agents to invoke via @tool
functions, correlates cross-domain results, and streams natural language responses.
Follows the password-reset chatbot pattern (async streaming).
"""
import os
import json
from datetime import datetime, timezone
import boto3
from strands import Agent, tool
from strands.models import BedrockModel
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from aws_utils import get_region

app = BedrockAgentCoreApp()
AWS_REGION = get_region()


def _invoke_sub_agent(agent_arn_env: str, action: str, params: dict = None) -> str:
    """Invoke a sub-agent AgentCore runtime via boto3 and return the response."""
    agent_arn = os.environ.get(agent_arn_env)
    if not agent_arn:
        return json.dumps({
            "success": False,
            "error": f"Sub-agent ARN not configured: {agent_arn_env}"
        })

    try:
        client = boto3.client("bedrock-agentcore", region_name=AWS_REGION)
        payload_bytes = json.dumps({"action": action, "params": params or {}}).encode("utf-8")
        response = client.invoke_agent_runtime(
            agentRuntimeArn=agent_arn,
            payload=payload_bytes,
        )
        # Read the streaming response
        response_body = response.get("response", None)
        if response_body:
            result = response_body.read().decode("utf-8")
            return result
        return json.dumps({"success": False, "error": "Empty response from sub-agent"})
    except Exception as e:
        return json.dumps({"success": False, "error": f"Sub-agent invocation failed: {str(e)}"})


@tool
def query_cost_data(action: str, params: dict = None) -> str:
    """Query AWS cost and usage data from Cost Explorer.
    USE THIS for: spending, budgets, forecasts, cost trends, "how much did I spend", cost optimization recommendations.
    DO NOT use this for Trusted Advisor checks or health events.

    Available actions:
    - get_cost_and_usage: Retrieve cost data for a time range. Params: startDate (optional, defaults to 30 days ago), endDate (optional, defaults to today), granularity (DAILY or MONTHLY)
    - get_cost_forecast: Get cost forecast. Params: startDate, endDate, granularity, metric
    - list_recommendations: Get cost optimization recommendations from Cost Optimization Hub. Params: category, maxResults

    You MUST always provide the action parameter. Example: action="get_cost_and_usage", params={"granularity": "MONTHLY"}
    """
    return _invoke_sub_agent("COST_AGENT_ARN", action, params)


@tool
def query_health_events(action: str, params: dict = None) -> str:
    """Query AWS Health Dashboard for service outages, incidents, and scheduled maintenance.
    USE THIS for: "any AWS issues", "service outages", "health events", "what happened on date X", scheduled changes, lifecycle events.
    DO NOT use this for Trusted Advisor optimization recommendations.

    Available actions:
    - describe_events: List health events. Params: region, service, event_type, startTime (ISO 8601), endTime (ISO 8601), maxResults
    - describe_affected_entities: Get affected resources. Params: event_arn
    - describe_event_details: Get event details. Params: event_arn

    You MUST always provide the action parameter. Example: action="describe_events", params={"startTime": "2026-03-01T00:00:00Z", "endTime": "2026-03-31T23:59:59Z"}
    """
    return _invoke_sub_agent("HEALTH_AGENT_ARN", action, params)


@tool
def query_support_cases(action: str, params: dict = None) -> str:
    """Query AWS Support cases including resolved/closed cases.
    USE THIS for: "support cases", "tickets", "case history", "did I open a case".
    DO NOT use this for health events or Trusted Advisor.

    Available actions:
    - describe_cases: List support cases (includes resolved by default). Params: maxResults
    - describe_communications: Get case communications. Params: caseId
    - search_cases: Search for cases by criteria. Params: serviceCode, severityCode, afterTime, beforeTime

    You MUST always provide the action parameter. Example: action="describe_cases", params={"maxResults": 10}
    """
    return _invoke_sub_agent("SUPPORT_AGENT_ARN", action, params)


@tool
def query_trusted_advisor(action: str, params: dict = None) -> str:
    """Query AWS Trusted Advisor for optimization recommendations and best practice checks.
    USE THIS for: "trusted advisor", "optimization", "best practices", "check trusted advisor", "recommendations", "underutilized resources", "security checks", "cost savings".
    DO NOT use this for health events or service outages.

    Available actions:
    - list_recommendations: Get actionable recommendations with warnings/errors. Params: pillar (cost_optimizing, security, performance, fault_tolerance, service_limits), maxResults
    - describe_checks: List all available checks. Params: pillar
    - describe_check_result: Get detailed results for a specific check. Params: checkId

    You MUST always provide the action parameter. Example: action="list_recommendations", params={}
    """
    return _invoke_sub_agent("TA_AGENT_ARN", action, params)


@tool
def query_cur_data(action: str, params: dict = None) -> str:
    """Query Cost and Usage Report (CUR) data via Athena for granular resource-level cost analysis.
    USE THIS for: "resource-level costs", "detailed usage", "CUR data", "usage patterns".
    DO NOT use this for high-level cost summaries (use query_cost_data instead).

    Available actions:
    - query_cur_data: Run a custom SQL query against CUR. Params: query, maxRows
    - get_resource_costs: Get costs for specific resources. Params: resourceId, service, startDate, endDate
    - analyze_usage_patterns: Analyze usage patterns by service. Params: service, startDate, endDate, granularity

    You MUST always provide the action parameter. Example: action="get_resource_costs", params={"startDate": "2026-01-01", "endDate": "2026-03-31"}
    """
    return _invoke_sub_agent("CUR_AGENT_ARN", action, params)


model = BedrockModel(model_id="amazon.nova-pro-v1:0")


def _build_system_prompt() -> str:
    """Build system prompt with current date for accurate time references."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    current_year = datetime.now(timezone.utc).strftime("%Y")

    return f"""You are an AWS operations analytics assistant (G.O.A.T.).
Today's date is {today}. The current year is {current_year}. ALWAYS use this when interpreting time references.

CRITICAL DATE RULES:
- "last month" = previous calendar month in {current_year}
- "in March" = March {current_year} (startTime: "{current_year}-03-01T00:00:00Z", endTime: "{current_year}-03-31T23:59:59Z")
- "past 3 months" = 3 months before {today}
- "recently" = last 30 days from {today}
- "on October 20th" = October 20, 2025 (startTime: "2025-10-20T00:00:00Z", endTime: "2025-10-21T00:00:00Z")
- ALWAYS pass startTime and endTime as ISO 8601 strings when the user mentions ANY time period
- NEVER omit date parameters when the user asks about a specific time range
- ALWAYS use the current year ({current_year}) unless the user explicitly mentions a different year

When calling tools:
- ALWAYS provide the action parameter (e.g., action="describe_events")
- ALWAYS convert relative time references ("last week", "in March", "recently") to precise ISO 8601 date strings
- For health events: use startTime and endTime params
- For cost data: use startDate and endDate params (format: YYYY-MM-DD)
- For "top cost drivers" or "cost breakdown": use groupBy=["SERVICE"] to get per-service costs. ALWAYS use metric="UNBLENDED_COST" (default, most reliable).
- For "this month" cost queries: use startDate as the 1st of current month, endDate as {today}
- Cost Explorer data has a 24-48 hour delay — if current month returns $0, explain this to the user and suggest querying the previous month instead
- For cost queries: make ONE call with the right parameters. Do NOT retry with different date ranges — the agent handles retries internally. If the call fails, report the error and suggest the user try again in 2-3 minutes.

When a question spans multiple domains, call multiple tools and correlate the results.
For example, if a user asks about a service outage's cost impact, query both Health
and Cost tools, then explain how they relate.

COMPLETE HEALTH CHECK RULES:
When the user asks for a "complete health check", "full health check", or "account health check", query ALL FIVE domains:
1. query_health_events — health events and service issues
2. query_support_cases — support case history
3. query_trusted_advisor — optimization recommendations
4. query_cost_data — current month cost summary with groupBy=["SERVICE"]
5. query_cur_data — detailed usage data (if CUR is configured)

CONVERSATIONAL CONTEXT RULES:
- For follow-up messages like "and last year", "what about March", "show me more", "break it down by service" — look at the PREVIOUS conversation to determine which domain was being discussed, and ONLY query that same domain with adjusted parameters.
- Example: if the previous message was about cost data and the user says "and last year", query ONLY cost data for the last year — do NOT add health, support, or trusted advisor.
- Example: if the previous message was about health events and the user says "what about October", query ONLY health events for October.
- ONLY query multiple domains when the user explicitly asks for a cross-domain analysis or a complete health check.

If a tool call fails or times out, include partial results from successful tools
and indicate which domains are unavailable.

RESPONSE FORMATTING RULES:
- NEVER show raw JSON to the user. ALWAYS summarize tool results in clear, natural language.
- If a tool returns JSON with a "formattedText" field, use that text as the basis for your response.
- If a tool returns JSON data, extract the key information and present it as a readable summary.
- Use bullet points for lists, tables for comparisons, and highlight key metrics.
- Keep responses concise — summarize large result sets instead of listing every item.
- If there are many results, show the top 5-10 most relevant and mention the total count."""


@app.entrypoint
async def agent_invocation(payload, context=None):
    """
    Main entry point for the Orchestration Agent.
    Receives JSON payload, creates Strands Agent, streams response.
    Async — yields response chunks for streaming.
    """
    if isinstance(payload, str):
        payload = json.loads(payload)

    user_input = None
    if isinstance(payload, dict):
        if "input" in payload and isinstance(payload["input"], dict):
            user_input = payload["input"].get("prompt")
        else:
            user_input = payload.get("prompt")

    if not user_input:
        raise ValueError(
            f"No prompt found in payload. Expected {{'prompt': '...'}}. Received: {payload}"
        )

    agent = Agent(
        model=model,
        tools=[query_cost_data, query_health_events, query_support_cases,
               query_trusted_advisor, query_cur_data],
        system_prompt=_build_system_prompt(),
    )

    async for chunk in agent.stream_async(user_input):
        if isinstance(chunk, dict) and "data" in chunk:
            if isinstance(chunk["data"], str) and chunk["data"].strip():
                yield chunk["data"]
        elif isinstance(chunk, str) and chunk.strip():
            yield chunk


if __name__ == "__main__":
    app.run()
