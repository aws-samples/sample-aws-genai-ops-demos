#!/usr/bin/env python3
"""
Direct streaming simulation test - no deployment needed.
This will show us exactly what chunks are being generated.
"""

import asyncio
import sys
import os

# Add the agent directory to the path
sys.path.append(os.path.join(os.path.dirname(__file__), 'agent'))

# Set environment variables for testing
os.environ['USER_POOL_ID'] = 'us-east-1_lnmMuSBJ4'
os.environ['USER_POOL_CLIENT_ID'] = '2k78tkf99c7l3i1ioc4kb1r517'
os.environ['AWS_REGION'] = 'us-east-1'

# Import the agent components directly
from strands import Agent
from strands.models import BedrockModel
from strands_agent import initiate_password_reset, complete_password_reset, PASSWORD_RESET_SYSTEM_PROMPT

async def test_direct_streaming():
    """Test streaming directly with the agent, bypassing AgentCore"""
    
    print("=== Direct Agent Streaming Test ===\n")
    
    # Create the model and agent directly
    model_id = "global.amazon.nova-2-lite-v1:0"
    model = BedrockModel(model_id=model_id)
    
    agent = Agent(
        model=model,
        tools=[initiate_password_reset, complete_password_reset],
        system_prompt=PASSWORD_RESET_SYSTEM_PROMPT
    )
    
    # Test message
    message = "I forgot my password"
    print(f"User: {message}")
    print("Streaming chunks:")
    print("-" * 50)
    
    chunk_count = 0
    response_text = ""
    
    try:
        async for chunk in agent.stream_async(message):
            chunk_count += 1
            print(f"Chunk {chunk_count}:")
            print(f"  Type: {type(chunk)}")
            print(f"  Content: {repr(chunk)}")
            
            # Check if it has data key (dictionary)
            if isinstance(chunk, dict) and 'data' in chunk:
                print(f"  Has data: {repr(chunk['data'])}")
                if isinstance(chunk['data'], str) and chunk['data'].strip():
                    response_text += chunk['data']
                    print(f"  -> YIELDING: {repr(chunk['data'])}")
            elif isinstance(chunk, str) and chunk.strip():
                response_text += chunk
                print(f"  -> YIELDING: {repr(chunk)}")
            else:
                print(f"  -> IGNORING (not text)")
            
            print()
    
    except Exception as e:
        print(f"Error during streaming: {e}")
        import traceback
        traceback.print_exc()
    
    print("-" * 50)
    print(f"Total chunks: {chunk_count}")
    print(f"Final response text: {repr(response_text)}")
    print(f"Response length: {len(response_text)} characters")

if __name__ == "__main__":
    asyncio.run(test_direct_streaming())