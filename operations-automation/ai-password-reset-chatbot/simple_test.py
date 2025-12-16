#!/usr/bin/env python3
"""
Simple test to debug the streaming issue locally without AgentCore runtime.
"""

import os
import sys
import asyncio
import json

# Set environment variables
os.environ['USER_POOL_ID'] = 'us-east-1_lnmMuSBJ4'
os.environ['USER_POOL_CLIENT_ID'] = '2k78tkf99c7l3i1ioc4kb1r517'
os.environ['AWS_REGION'] = 'us-east-1'

# Add agent directory to path
sys.path.append(os.path.join(os.path.dirname(__file__), 'agent'))

# Mock the BedrockAgentCoreApp for local testing
class MockBedrockAgentCoreApp:
    def __init__(self):
        pass
    
    def entrypoint(self, func):
        return func
    
    def run(self):
        pass

# Mock the strands Agent for local testing
class MockAgent:
    def __init__(self, model, tools, system_prompt):
        self.model = model
        self.tools = tools
        self.system_prompt = system_prompt
        self.messages = []
        
    def invoke(self, user_input):
        # Add messages to simulate conversation history
        self.messages.append({"role": "user", "content": user_input})
        
        # Simple mock response for testing
        if "forgot" in user_input.lower() or "password" in user_input.lower():
            response = "Hello! I can help you reset your password. To start, could you please provide me with the email address associated with your account?"
        elif "@" in user_input:
            response = f"Thank you for providing your email address. If an account exists for '{user_input}', a verification code has been sent to your email. Please check your inbox (and spam folder) for the code."
        elif user_input.isdigit():
            response = "Please create a new password for your account. Your new password must meet the following requirements:\n- Minimum 8 characters\n- At least one uppercase letter (A-Z)\n- At least one lowercase letter (a-z)\n- At least one number (0-9)"
        else:
            response = "I understand you're providing your new password. Let me process that for you..."
        
        self.messages.append({"role": "assistant", "content": response})
        return response

# Mock the BedrockModel
class MockBedrockModel:
    def __init__(self, model_id):
        self.model_id = model_id

# Patch the imports
import strands_agent
strands_agent.BedrockAgentCoreApp = MockBedrockAgentCoreApp
strands_agent.Agent = MockAgent
strands_agent.BedrockModel = MockBedrockModel

# Mock context
class MockContext:
    def __init__(self, session_id):
        self.session_id = session_id

async def test_streaming():
    """Test the streaming functionality locally"""
    print("=== Testing Streaming Functionality ===\n")
    
    # Import the agent function
    from strands_agent import agent_invocation
    
    session_id = "test-session-123"
    context = MockContext(session_id)
    
    test_messages = [
        "I forgot my password",
        "test@example.com",
        "123456",
        "MyNewPassword123!"
    ]
    
    for i, message in enumerate(test_messages, 1):
        print(f"{i}. User: {message}")
        
        try:
            payload = {"prompt": message}
            response = ""
            
            # Test the streaming
            async for chunk in agent_invocation(payload, context):
                print(f"   Chunk: {repr(chunk)}")
                response += str(chunk)
            
            print(f"   Full Response: {response}\n")
            
        except Exception as e:
            print(f"   ERROR: {e}\n")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_streaming())