#!/usr/bin/env python3
"""
Test script to debug streaming response issues.
This will help us see exactly what chunks are being generated.
"""

import json
import asyncio
import sys
import os

# Add the agent directory to the path
sys.path.append(os.path.join(os.path.dirname(__file__), 'agent'))

# Mock the AgentCore dependencies for local testing
class MockRequestContext:
    """Mock RequestContext for testing session management"""
    def __init__(self, session_id):
        self.session_id = session_id

# Set environment variables for testing
os.environ['USER_POOL_ID'] = 'us-east-1_lnmMuSBJ4'
os.environ['USER_POOL_CLIENT_ID'] = '2k78tkf99c7l3i1ioc4kb1r517'
os.environ['AWS_REGION'] = 'us-east-1'

# Import after setting environment
from strands_agent import agent_invocation

async def test_streaming_debug():
    """Test streaming response to see what chunks are generated"""
    
    print("=== Testing Streaming Response Debug ===\n")
    
    session_id = "debug-session-456"
    context = MockRequestContext(session_id)
    
    # Simple test message
    message = "I forgot my password"
    payload = {"prompt": message}
    
    print(f"User: {message}")
    print("Raw streaming chunks:")
    print("-" * 50)
    
    chunk_count = 0
    text_chunks = []
    debug_chunks = []
    
    try:
        async for chunk in agent_invocation(payload, context):
            chunk_count += 1
            print(f"Chunk {chunk_count}: {repr(chunk)}")
            
            # Categorize chunks
            if isinstance(chunk, str) and chunk.strip():
                text_chunks.append(chunk)
            else:
                debug_chunks.append(chunk)
    
    except Exception as e:
        print(f"Error during streaming: {e}")
    
    print("-" * 50)
    print(f"Total chunks: {chunk_count}")
    print(f"Text chunks: {len(text_chunks)}")
    print(f"Debug chunks: {len(debug_chunks)}")
    
    if text_chunks:
        print("\nCombined text response:")
        print("".join(text_chunks))
    
    if debug_chunks:
        print("\nDebug chunks that should be filtered:")
        for chunk in debug_chunks:
            print(f"  {repr(chunk)}")

async def test_simple_conversation():
    """Test a simple conversation flow"""
    
    print("\n=== Testing Simple Conversation ===\n")
    
    session_id = "simple-session-789"
    context = MockRequestContext(session_id)
    
    messages = [
        "I forgot my password",
        "my email is test@example.com"
    ]
    
    for i, message in enumerate(messages, 1):
        print(f"{i}. User: {message}")
        
        payload = {"prompt": message}
        response = ""
        
        try:
            async for chunk in agent_invocation(payload, context):
                if isinstance(chunk, str) and chunk.strip():
                    response += chunk
            
            print(f"   Agent: {response}\n")
            
        except Exception as e:
            print(f"   Error: {e}\n")

if __name__ == "__main__":
    asyncio.run(test_streaming_debug())
    asyncio.run(test_simple_conversation())