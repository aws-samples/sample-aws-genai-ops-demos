#!/usr/bin/env python3
"""
Test the deployed AgentCore runtime directly to verify streaming is working.
"""

import boto3
import json
import asyncio
from datetime import datetime

# Configuration
AGENT_RUNTIME_ARN = "arn:aws:bedrock-agentcore:us-east-1:517675598740:runtime/password_reset_agent-CEgBZw30fn"
REGION = "us-east-1"

async def test_deployed_agent():
    """Test the deployed agent directly via AgentCore runtime"""
    
    print("=== Testing Deployed Agent ===\n")
    
    # Create AgentCore client
    client = boto3.client('bedrock-agentcore', region_name=REGION)
    
    # Test message
    message = "I forgot my password"
    session_id = f"test-session-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    
    print(f"Session ID: {session_id}")
    print(f"User: {message}")
    print("Agent response:")
    print("-" * 50)
    
    try:
        # Invoke the agent with streaming
        response = client.invoke_agent_runtime(
            agentRuntimeArn=AGENT_RUNTIME_ARN,
            sessionId=session_id,
            inputText=message
        )
        
        # Process streaming response
        response_text = ""
        chunk_count = 0
        
        for event in response['completion']:
            chunk_count += 1
            
            if 'chunk' in event:
                chunk = event['chunk']
                if 'bytes' in chunk:
                    # Decode the chunk
                    chunk_data = json.loads(chunk['bytes'].decode('utf-8'))
                    
                    # Extract text if available
                    if isinstance(chunk_data, dict) and 'data' in chunk_data:
                        text = chunk_data['data']
                        if isinstance(text, str) and text.strip():
                            response_text += text
                            print(text, end='', flush=True)
                    elif isinstance(chunk_data, str) and chunk_data.strip():
                        response_text += chunk_data
                        print(chunk_data, end='', flush=True)
        
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