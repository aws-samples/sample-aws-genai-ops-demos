#!/usr/bin/env python3
"""
Test the deployed AgentCore runtime directly to verify streaming is working.
"""

import boto3
import json
import asyncio
import uuid
from datetime import datetime

# Configuration
# Replace with your own deployed runtime ARN (account ID + runtime ID)
AGENT_RUNTIME_ARN = "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/password_reset_agent-XXXXXXXXXX"
REGION = "us-east-1"

async def test_deployed_agent():
    """Test the deployed agent directly via AgentCore runtime"""
    
    print("=== Testing Deployed Agent ===\n")
    
    # Create AgentCore client
    client = boto3.client('bedrock-agentcore', region_name=REGION)
    
    # Test message
    message = "I forgot my password"
    session_id = str(uuid.uuid4())
    
    print(f"Session ID: {session_id}")
    print(f"User: {message}")
    print("Agent response:")
    print("-" * 50)
    
    try:
        # Invoke the agent with streaming (correct API format)
        payload_data = json.dumps({"prompt": message}).encode('utf-8')
        response = client.invoke_agent_runtime(
            agentRuntimeArn=AGENT_RUNTIME_ARN,
            runtimeSessionId=session_id,
            payload=payload_data
        )
        
        # Process streaming response according to AgentCore documentation
        response_text = ""
        chunk_count = 0
        
        # Check if it's a streaming response
        if "text/event-stream" in response.get("contentType", ""):
            print("Processing streaming response...")
            # Handle streaming response
            for line in response["response"].iter_lines(chunk_size=10):
                if line:
                    chunk_count += 1
                    line = line.decode("utf-8")
                    if line.startswith("data: "):
                        line = line[6:]  # Remove "data: " prefix
                        if line.strip():
                            response_text += line
                            print(line, end='', flush=True)
        
        elif response.get("contentType") == "application/json":
            print("Processing JSON response...")
            # Handle standard JSON response
            content = []
            for chunk in response.get("response", []):
                content.append(chunk.decode('utf-8'))
            response_data = json.loads(''.join(content))
            response_text = str(response_data)
            print(response_text)
        
        else:
            print(f"Unknown content type: {response.get('contentType')}")
            print(f"Response keys: {list(response.keys())}")
            print(f"Raw response: {response}")
        
        print(f"\n{'-' * 50}")
        print(f"Total chunks: {chunk_count}")
        print(f"Response length: {len(response_text)} characters")
        print(f"Response preview: {repr(response_text[:100])}")
        
        if response_text.strip():
            print("✅ SUCCESS: Agent returned clean text response")
        else:
            print("❌ FAILURE: No text content received")
            
    except Exception as e:
        print(f"❌ ERROR: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_deployed_agent())