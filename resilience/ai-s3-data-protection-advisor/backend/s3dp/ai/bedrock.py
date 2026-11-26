"""Amazon Bedrock Converse with one forced tool: the model must answer with the tool's JSON input."""
import json

from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError


def client(session):
    """bedrock-runtime client from the scan's boto3 session (same credentials and region)."""
    return session.client("bedrock-runtime",
                          config=Config(retries={"mode": "adaptive", "max_attempts": 4}, read_timeout=60))


def error_code(e):
    """Error code or type name for logs: messages can echo prompt text (bucket names), so they are never logged."""
    return getattr(e, "response", {}).get("Error", {}).get("Code") or type(e).__name__


def probe(bedrock, model):
    """One minimal Converse call. None when the model answers, else the error code (for example
    AccessDeniedException when model access is not granted)."""
    try:
        bedrock.converse(modelId=model, messages=[{"role": "user", "content": [{"text": "Reply with OK."}]}],
                         inferenceConfig={"maxTokens": 5, "temperature": 0})
        return None
    except ClientError as e:
        return e.response.get("Error", {}).get("Code", "Unknown")
    except BotoCoreError as e:
        return type(e).__name__


def call_tool(bedrock, model, system, user, tool_name, schema, max_tokens=4000):
    """Return the tool input dict the model produced."""
    r = bedrock.converse(
        modelId=model,
        system=[{"text": system}],
        messages=[{"role": "user", "content": [{"text": user}]}],
        inferenceConfig={"maxTokens": max_tokens, "temperature": 0},
        toolConfig={"tools": [{"toolSpec": {"name": tool_name, "description": "Record the result",
                                            "inputSchema": {"json": schema}}}],
                    "toolChoice": {"tool": {"name": tool_name}}},
    )
    for block in r["output"]["message"]["content"]:
        if "toolUse" in block and block["toolUse"]["name"] == tool_name:
            data = block["toolUse"]["input"]
            return json.loads(data) if isinstance(data, str) else data
    raise ValueError("model did not call the tool")
