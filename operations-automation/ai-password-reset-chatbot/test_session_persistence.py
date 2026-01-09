#!/usr/bin/env python3
"""
Test session persistence with the deployed agent using the SAME session ID
"""

import boto3
import json
import uuid

# Configuration
AGENT_RUNTIME_ARN = "arn:aws:bedrock-agentcore:us-east-1:517675598740:runtime/password_reset_agent-CEgBZw30fn"
REGION = "us-east-1"

def test_session_persistence():
    """Test if the same session ID maintains context"""
    
    print("=== Testing Session Persistence ===\n")
    
    client = boto3.client('bedrock-agentcore', region_name=REGION)
    
    # Use the SAME session ID for all messages
    session_id = str(uuid.uuid4())
    print(f"Using session ID: {session_id}\n")
    
    messages = [
        "I forgot my password for user test@example.com",
        "I got the verification code: 123456. My new password is NewPass123!"
    ]
    
    for i, message in enumerate(messages, 1):
        print(f"=== Message {i} ===")
        print(f"User: {message}")
        print("Agent:")
        
        payload = json.dumps({"prompt": message}).encode('utf-8')
        response = client.invoke_agent_runtime(
            agentRuntimeArn=AGENT_RUNTIME_ARN,
            runtimeSessionId=session_id,  # SAME session ID
            payload=payload
        )
        
        response_text = ""
        if "text/event-stream" in response.get("contentType", ""):
            for line in response["response"].iter_lines(chunk_size=10):
                if line:
                    line = line.decode("utf-8")
                    if line.startswith("data: "):
                        line = line[6:]
                        if line.strip():
                            response_text += line
                            print(line, end='', flush=True)
        
        print(f"\n--- Response {i} length: {len(response_text)} chars ---\n")
        
        # Check if agent remembers the email from message 1 in message 2
        if i == 2:
            if "test@example.com" in response_text:
                print("✅ SUCCESS: Agent remembered email from previous message")
            else:
                print("❌ FAILURE: Agent forgot email from previous message")
                print(f"Response preview: {repr(response_text[:200])}")

if __name__ == "__main__":
    test_session_persistence()