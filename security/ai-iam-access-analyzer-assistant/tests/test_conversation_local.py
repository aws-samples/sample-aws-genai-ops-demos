#!/usr/bin/env python3
"""Local test harness for the full Bedrock Converse conversation loop.

Runs the agent locally (no Lambda, no API Gateway) — directly invokes
tool functions in-process instead of through Lambda.invoke().

Usage:
    cd ai-iam-access-analyzer-assistant
    python -m tests.test_conversation_local

    # Interactive mode:
    python -m tests.test_conversation_local --interactive

    # Single message:
    python -m tests.test_conversation_local --message "What are my critical IAM findings?"
"""

import argparse
import json
import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import boto3

# Import tool handlers directly
from tools.list_findings import handler as list_findings_handler
from tools.get_finding_details import handler as get_finding_details_handler
from tools.generate_policy import handler as generate_policy_handler
from tools.check_dependencies import handler as check_dependencies_handler
from tools.validate_policy import handler as validate_policy_handler

# Import agent config (system prompt + tool definitions)
from agent import SYSTEM_PROMPT, TOOL_CONFIG

# Local tool dispatch — bypasses Lambda.invoke()
LOCAL_TOOLS = {
    "list_findings": list_findings_handler,
    "get_finding_details": get_finding_details_handler,
    "generate_policy": generate_policy_handler,
    "check_dependencies": check_dependencies_handler,
    "validate_policy": validate_policy_handler,
}

bedrock_client = boto3.client("bedrock-runtime")

MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-5-20250929-v1:0")


def invoke_tool_local(tool_name: str, tool_input: dict) -> dict:
    """Invoke a tool handler directly (no Lambda)."""
    handler = LOCAL_TOOLS.get(tool_name)
    if not handler:
        return {"error": f"Unknown tool: {tool_name}"}

    print(f"\n    [TOOL CALL] {tool_name}")
    print(f"    Input: {json.dumps(tool_input, indent=2)[:200]}...")

    result = handler(tool_input)

    # Show compact result
    result_str = json.dumps(result, default=str)
    if len(result_str) > 500:
        print(f"    Result: {result_str[:500]}...")
    else:
        print(f"    Result: {result_str}")

    return result


def converse_local(messages: list) -> str:
    """Run one conversation turn with Bedrock, handling tool loops locally."""
    response = bedrock_client.converse(
        modelId=MODEL_ID,
        system=[{"text": SYSTEM_PROMPT}],
        messages=messages,
        toolConfig=TOOL_CONFIG,
    )

    # Tool use loop
    loop_count = 0
    max_loops = 10

    while response["stopReason"] == "tool_use" and loop_count < max_loops:
        loop_count += 1
        assistant_message = response["output"]["message"]
        messages.append(assistant_message)

        # Execute tool calls locally
        tool_results = []
        for block in assistant_message["content"]:
            if "toolUse" in block:
                tool_use = block["toolUse"]
                result = invoke_tool_local(tool_use["name"], tool_use["input"])
                tool_results.append({
                    "toolResult": {
                        "toolUseId": tool_use["toolUseId"],
                        "content": [{"json": result}],
                    }
                })

        messages.append({"role": "user", "content": tool_results})

        response = bedrock_client.converse(
            modelId=MODEL_ID,
            system=[{"text": SYSTEM_PROMPT}],
            messages=messages,
            toolConfig=TOOL_CONFIG,
        )

    # Extract final text
    final_text = ""
    for block in response["output"]["message"]["content"]:
        if "text" in block:
            final_text += block["text"]

    # Show usage
    usage = response.get("usage", {})
    print(f"\n    [USAGE] Input: {usage.get('inputTokens', '?')} tokens, Output: {usage.get('outputTokens', '?')} tokens")

    return final_text


def run_interactive():
    """Interactive conversation mode."""
    print("\n  Interactive mode — type 'quit' to exit, 'reset' to clear history")
    print("  " + "-" * 50)

    messages = []

    while True:
        try:
            user_input = input("\n  You: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n\n  Goodbye!")
            break

        if not user_input:
            continue
        if user_input.lower() == "quit":
            break
        if user_input.lower() == "reset":
            messages = []
            print("  [History cleared]")
            continue

        messages.append({"role": "user", "content": [{"text": user_input}]})

        print("\n  Assistant: ", end="")
        try:
            response_text = converse_local(messages)
            print(f"\n\n  {response_text}")
            messages.append({"role": "assistant", "content": [{"text": response_text}]})
        except Exception as e:
            print(f"\n  [ERROR] {e}")
            # Remove the failed user message to keep history clean
            messages.pop()


def run_single_message(message: str):
    """Run a single message through the conversation loop."""
    print(f"\n  User: {message}")
    messages = [{"role": "user", "content": [{"text": message}]}]

    print("\n  Processing...")
    response_text = converse_local(messages)
    print(f"\n  Assistant:\n  {response_text}")


def run_preset_tests():
    """Run a set of preset test messages."""
    test_messages = [
        "What are my active IAM findings? Show me the critical and high severity ones.",
        "How many findings do I have by resource type?",
    ]

    print("\n  Running preset test messages...\n")

    for i, msg in enumerate(test_messages, 1):
        print(f"\n{'='*60}")
        print(f"  Test {i}/{len(test_messages)}: {msg}")
        print("=" * 60)

        messages = [{"role": "user", "content": [{"text": msg}]}]
        try:
            response = converse_local(messages)
            print(f"\n  Response:\n  {response[:1000]}")
            if len(response) > 1000:
                print(f"  ... ({len(response)} chars total)")
        except Exception as e:
            print(f"\n  FAILED: {e}")

    print(f"\n{'='*60}")
    print("  Preset tests complete.")


def main():
    parser = argparse.ArgumentParser(
        description="Local conversation test harness for IAM Analyzer Assistant"
    )
    parser.add_argument("--interactive", "-i", action="store_true", help="Interactive chat mode")
    parser.add_argument("--message", "-m", help="Single message to process")
    args = parser.parse_args()

    print("=" * 60)
    print(" AI IAM Access Analyzer Assistant — Local Conversation Test")
    print("=" * 60)
    print(f"\n  Model: {MODEL_ID}")
    print(f"  Tools: {', '.join(LOCAL_TOOLS.keys())}")

    # Verify credentials
    sts = boto3.client("sts")
    try:
        identity = sts.get_caller_identity()
        print(f"  Account: {identity['Account']}")
        print(f"  Region: {boto3.session.Session().region_name}")
    except Exception as e:
        print(f"\n  ERROR: Cannot connect to AWS: {e}")
        sys.exit(1)

    # Verify Bedrock access
    try:
        bedrock_client.converse(
            modelId=MODEL_ID,
            messages=[{"role": "user", "content": [{"text": "Hi"}]}],
        )
        print(f"  Bedrock: Connected")
    except Exception as e:
        print(f"\n  ERROR: Cannot access Bedrock model {MODEL_ID}: {e}")
        print(f"\n  Try: BEDROCK_MODEL_ID=\"us.anthropic.claude-sonnet-4-20250514-v1:0\" python3 -m tests.test_conversation_local --interactive")
        sys.exit(1)

    if args.interactive:
        run_interactive()
    elif args.message:
        run_single_message(args.message)
    else:
        run_preset_tests()


if __name__ == "__main__":
    main()
