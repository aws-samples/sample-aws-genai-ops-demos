#!/usr/bin/env python3
"""
Test script to verify session-based conversation state management.
This demonstrates how AgentCore session IDs maintain conversation context.
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

async def test_session_flow():
    """Test the password reset flow using session-based state management"""
    
    print("=== Testing AgentCore Session-Based Password Reset ===\n")
    
    session_id = "test-session-123"
    context = MockRequestContext(session_id)
    
    # Simulate the problematic conversation flow
    test_messages = [
        "I forgot my password",
        "bllecoq@amazon.com",
        "Here you go: 529016", 
        "Here is my new password i would like to use: 2GG8mu7o!",
        "again? you have it dude"  # This should NOT cause the agent to ask for email again
    ]
    
    for i, message in enumerate(test_messages, 1):
        print(f"{i}. User: {message}")
        
        payload = {"prompt": message}
        response = ""
        
        async for chunk in agent_invocation(payload, context):
            response += chunk
        
        print(f"   Agent: {response[:100]}{'...' if len(response) > 100 else ''}\n")
        
        # Check if agent is asking for email after it should already know it
        if i > 2 and "email" in response.lower() and "provide" in response.lower():
            print("❌ ISSUE: Agent is asking for email again after it was already provided!")
        elif i == 5:  # Last message - should acknowledge frustration and use stored email
            if "bllecoq@amazon.com" in response or "email" not in response.lower():
                print("✅ SUCCESS: Agent remembered the email and didn't ask again!")
            else:
                print("❌ ISSUE: Agent still asking for email on final message!")
    
    print("=== Test Complete ===")

if __name__ == "__main__":
    asyncio.run(test_session_flow())