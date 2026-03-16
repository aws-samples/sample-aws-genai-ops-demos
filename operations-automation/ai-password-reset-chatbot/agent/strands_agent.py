"""
Password Reset Chatbot Agent

A conversational agent that guides users through Cognito's native password reset flow.
GenAI handles the conversational UX while Cognito enforces all security-critical operations.

Security Principles:
- Agent NEVER generates, validates, or stores passwords
- Agent NEVER validates verification codes
- All security operations delegated to Cognito
"""

import os
import json
import boto3
from datetime import datetime
from strands import Agent, tool
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands.models import BedrockModel
from bedrock_agentcore.memory import MemoryClient
from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig, RetrievalConfig
from bedrock_agentcore.memory.integrations.strands.session_manager import AgentCoreMemorySessionManager
from botocore.exceptions import ClientError
from aws_utils import get_region

# Create the AgentCore app
app = BedrockAgentCoreApp()

# Get User Pool configuration from environment
USER_POOL_ID = os.environ.get('USER_POOL_ID', '')
USER_POOL_CLIENT_ID = os.environ.get('USER_POOL_CLIENT_ID', '')
AWS_REGION = get_region()

# Initialize Cognito client
cognito_client = boto3.client('cognito-idp', region_name=AWS_REGION)

memory_client = MemoryClient(region_name=AWS_REGION)
MEMORY_ID = None

# Simple in-memory session storage as fallback
sessions = {}


@tool
def initiate_password_reset(username: str) -> str:
    """
    Initiate password reset for a user. Sends a verification code to their 
    registered email address.
    
    Args:
        username: The user's email address or username
        
    Returns:
        A message indicating the result of the operation
    """
    if not USER_POOL_CLIENT_ID:
        return "Error: Password reset service is not configured. Please contact support."
    
    try:
        cognito_client.forgot_password(
            ClientId=USER_POOL_CLIENT_ID,
            Username=username.strip()
        )
        # Security: Don't reveal if user exists - always return same message
        return f"If an account exists for '{username}', a verification code has been sent. Please check your email inbox (and spam folder)."
    
    except ClientError as e:
        error_code = e.response['Error']['Code']
        
        if error_code == 'UserNotFoundException':
            # Security: Don't reveal that user doesn't exist
            return f"If an account exists for '{username}', a verification code has been sent. Please check your email inbox (and spam folder)."
        
        elif error_code == 'LimitExceededException':
            return "Too many password reset attempts. Please wait a few minutes before trying again."
        
        elif error_code == 'InvalidParameterException':
            return "The email address format appears to be invalid. Please provide a valid email address."
        
        elif error_code == 'NotAuthorizedException':
            return "Password reset is not available for this account. Please contact support."
        
        else:
            # Log the error but don't expose details to user
            print(f"Cognito error: {error_code} - {e.response['Error']['Message']}")
            return "An error occurred while processing your request. Please try again later."
    
    except Exception as e:
        print(f"Unexpected error in initiate_password_reset: {str(e)}")
        return "An unexpected error occurred. Please try again later."


@tool
def complete_password_reset(username: str, verification_code: str, new_password: str) -> str:
    """
    Complete the password reset process with the verification code and new password.
    
    Args:
        username: The user's email address or username
        verification_code: The 6-digit code sent to the user's email
        new_password: The new password (must meet policy: 8+ chars, uppercase, lowercase, digit)
        
    Returns:
        A message indicating the result of the operation
    """
    if not USER_POOL_CLIENT_ID:
        return "Error: Password reset service is not configured. Please contact support."
    
    try:
        cognito_client.confirm_forgot_password(
            ClientId=USER_POOL_CLIENT_ID,
            Username=username.strip(),
            ConfirmationCode=verification_code.strip(),
            Password=new_password
        )
        return "Your password has been successfully reset! You can now sign in with your new password."
    
    except ClientError as e:
        error_code = e.response['Error']['Code']
        
        if error_code == 'CodeMismatchException':
            return "The verification code you entered is incorrect. Please check the code and try again."
        
        elif error_code == 'ExpiredCodeException':
            return "Your verification code has expired. Would you like me to send a new code?"
        
        elif error_code == 'InvalidPasswordException':
            return ("Your password doesn't meet the requirements. Please ensure it has:\n"
                   "- At least 8 characters\n"
                   "- At least one uppercase letter (A-Z)\n"
                   "- At least one lowercase letter (a-z)\n"
                   "- At least one number (0-9)")
        
        elif error_code == 'LimitExceededException':
            return "Too many attempts. Please wait a few minutes before trying again."
        
        elif error_code == 'UserNotFoundException':
            return "Unable to complete password reset. Please start the process again."
        
        else:
            print(f"Cognito error: {error_code} - {e.response['Error']['Message']}")
            return "An error occurred while resetting your password. Please try again."
    
    except Exception as e:
        print(f"Unexpected error in complete_password_reset: {str(e)}")
        return "An unexpected error occurred. Please try again later."


# System prompt for password reset assistant - optimized for Nova 2 Lite
SYSTEM_PROMPT_TEMPLATE = """## Task Summary:
You are a password reset assistant. Your ONLY job is to help users reset their password through a secure, guided process using the provided tools.

## Available Tools:
- initiate_password_reset: Sends verification code to user's email
- complete_password_reset: Completes reset with verification code and new password
- All security operations are handled by AWS Cognito - you NEVER handle passwords or codes directly

## Security Rules:
- NEVER generate, suggest, validate, or store passwords
- NEVER validate verification codes yourself - always use the complete_password_reset tool
- NEVER reveal whether a user account exists or not
- NEVER store, log, or repeat back passwords or verification codes
- Always use the provided tools for all password reset operations

## Response Style:
- Keep responses concise and conversational
- Use clear, step-by-step guidance
- When explaining password requirements, use bullet points
- For errors, provide specific next steps
- Be friendly, patient, and helpful - users are often frustrated when locked out
- If users ask about non-password-reset topics, politely redirect them to contact support

## Password Requirements (explain to users):
- Minimum 8 characters
- At least one uppercase letter (A-Z)
- At least one lowercase letter (a-z)
- At least one number (0-9)

## Error Handling:
- Wrong code: Ask to double-check and try again
- Expired code: Offer to send a new code
- Password doesn't meet requirements: Explain what's missing
- Rate limited: Ask to wait a few minutes before trying again"""

# Initialize the model - using Amazon Nova 2 Lite global inference profile for lightweight conversational tasks
model_id = "global.amazon.nova-2-lite-v1:0"  # Using global inference profile instead of direct model ID
model = BedrockModel(model_id=model_id)

# Global agent instance - will be configured per session
agent = None

async def get_or_create_memory():
    """Get or create AgentCore Memory for session management"""
    import asyncio
    global MEMORY_ID
    
    print(f"DEBUG: get_or_create_memory called, current MEMORY_ID: {MEMORY_ID}")
    
    if MEMORY_ID is None:
        print("DEBUG: Looking for existing AgentCore Memory...")
        try:
            # First, try to list existing memories to see if we already have one
            memories = memory_client.list_memories()
            print(f"DEBUG: Found {len(memories)} existing memories")
            print(f"DEBUG: Memories response type: {type(memories)}")
            print(f"DEBUG: Memories content: {memories}")
            
            # Look for our memory by ID prefix (name field may be empty in list response)
            existing_memory = None
            for memory in memories:
                memory_id = memory.get('id', '')
                memory_status = memory.get('status', '')
                print(f"DEBUG: Checking memory - id: '{memory_id}', status: '{memory_status}'")
                
                # Check if this is our memory by ID prefix (regardless of status)
                if memory_id.startswith('password_reset_sessions'):
                    existing_memory = memory
                    print(f"DEBUG: Found existing memory: {memory_id} with status: {memory_status}")
                    break
            
            if existing_memory:
                memory_id = existing_memory.get('id')
                memory_status = existing_memory.get('status')
                
                # If memory is CREATING, wait for it to become ACTIVE
                if memory_status == 'CREATING':
                    print(f"DEBUG: Memory {memory_id} is CREATING, waiting for it to become ACTIVE...")
                    max_wait_seconds = 30
                    wait_interval = 2
                    elapsed = 0
                    
                    while elapsed < max_wait_seconds:
                        await asyncio.sleep(wait_interval)
                        elapsed += wait_interval
                        
                        # Check status again
                        memories = memory_client.list_memories()
                        for memory in memories:
                            if memory.get('id') == memory_id:
                                memory_status = memory.get('status')
                                print(f"DEBUG: Memory status after {elapsed}s: {memory_status}")
                                
                                if memory_status == 'ACTIVE':
                                    MEMORY_ID = memory_id
                                    print(f"DEBUG: Memory is now ACTIVE: {MEMORY_ID}")
                                    return MEMORY_ID
                                elif memory_status == 'FAILED':
                                    raise Exception(f"Memory creation failed: {memory_id}")
                                break
                    
                    # If we get here, timeout waiting for ACTIVE status
                    raise Exception(f"Timeout waiting for memory {memory_id} to become ACTIVE after {max_wait_seconds}s")
                
                elif memory_status == 'ACTIVE':
                    MEMORY_ID = memory_id
                    print(f"DEBUG: Found existing ACTIVE AgentCore Memory: {MEMORY_ID}")
                    return MEMORY_ID
                
                else:
                    raise Exception(f"Memory {memory_id} has unexpected status: {memory_status}")
            
            else:
                # No existing memory found, create new one
                print("DEBUG: Creating new AgentCore Memory...")
                memory_response = memory_client.create_memory(
                    name="password_reset_sessions",
                    description="Session memory for password reset conversations"
                )
                memory_id = memory_response.get('id')
                memory_status = memory_response.get('status')
                print(f"DEBUG: Successfully created AgentCore Memory: {memory_id} with status: {memory_status}")
                print(f"DEBUG: Full memory response: {memory_response}")
                
                # Wait for it to become ACTIVE
                if memory_status == 'CREATING':
                    print(f"DEBUG: Waiting for newly created memory to become ACTIVE...")
                    max_wait_seconds = 30
                    wait_interval = 2
                    elapsed = 0
                    
                    while elapsed < max_wait_seconds:
                        await asyncio.sleep(wait_interval)
                        elapsed += wait_interval
                        
                        # Check status
                        memories = memory_client.list_memories()
                        for memory in memories:
                            if memory.get('id') == memory_id:
                                memory_status = memory.get('status')
                                print(f"DEBUG: Memory status after {elapsed}s: {memory_status}")
                                
                                if memory_status == 'ACTIVE':
                                    MEMORY_ID = memory_id
                                    print(f"DEBUG: Memory is now ACTIVE: {MEMORY_ID}")
                                    return MEMORY_ID
                                elif memory_status == 'FAILED':
                                    raise Exception(f"Memory creation failed: {memory_id}")
                                break
                    
                    # If we get here, timeout waiting for ACTIVE status
                    raise Exception(f"Timeout waiting for memory {memory_id} to become ACTIVE after {max_wait_seconds}s")
                
                elif memory_status == 'ACTIVE':
                    MEMORY_ID = memory_id
                    return MEMORY_ID
                
                else:
                    raise Exception(f"Newly created memory has unexpected status: {memory_status}")
                
        except Exception as e:
            print(f"ERROR: Failed to get/create AgentCore Memory: {e}")
            print(f"ERROR: Exception type: {type(e)}")
            import traceback
            print(f"ERROR: Full traceback: {traceback.format_exc()}")
            raise e  # Don't fall back - must work
    else:
        print(f"DEBUG: Using existing AgentCore Memory: {MEMORY_ID}")
    
    return MEMORY_ID


@app.entrypoint
async def agent_invocation(payload, context=None):
    """
    Agent invocation with AgentCore Memory integration and fixed streaming.
    """
    global agent
    
    if isinstance(payload, str):
        payload = json.loads(payload)

    user_input = None
    if isinstance(payload, dict):
        if "input" in payload and isinstance(payload["input"], dict):
            user_input = payload["input"].get("prompt")
        else:
            user_input = payload.get("prompt")

    if not user_input:
        raise ValueError(f"No prompt found in payload. Expected {{'prompt': '...'}}. Received: {payload}")

    # Get session ID from AgentCore context
    session_id = getattr(context, 'session_id', None) if context else None
    runtime_session_id = getattr(context, 'runtime_session_id', None) if context else None
    
    print(f"DEBUG SESSION: context={context}")
    print(f"DEBUG SESSION: session_id from context={session_id}")
    print(f"DEBUG SESSION: runtime_session_id from context={runtime_session_id}")
    print(f"DEBUG SESSION: context attributes={dir(context) if context else 'None'}")
    
    # Try to get the session ID that was passed from frontend
    if runtime_session_id:
        session_id = runtime_session_id
    elif session_id:
        session_id = session_id
    else:
        session_id = "default"
    
    print(f"DEBUG SESSION: Final session_id being used: {session_id}")
    print(f"Processing message for session: {session_id}")

    # Get AgentCore Memory - MUST work, no fallback
    print(f"DEBUG: Starting agent invocation for session: {session_id}")
    memory_id = await get_or_create_memory()
    print(f"DEBUG: Got memory_id: {memory_id}")
    
    # Configure AgentCore Memory
    print(f"DEBUG: Configuring AgentCore Memory...")
    memory_config = AgentCoreMemoryConfig(
        memory_id=memory_id,
        session_id=session_id,
        actor_id=session_id,  # Use session_id as actor_id for simplicity
        retrieval_config={
            f"/preferences/{session_id}": RetrievalConfig(
                top_k=10,
                relevance_score=0.2
            )
        }
    )
    print(f"DEBUG: Memory config created: {memory_config}")
    
    # Create session manager
    print(f"DEBUG: Creating AgentCore Memory session manager...")
    session_manager = AgentCoreMemorySessionManager(
        agentcore_memory_config=memory_config,  # Fixed: correct parameter name
        region_name=AWS_REGION
    )
    print(f"DEBUG: Session manager created: {session_manager}")
    
    # Create agent with AgentCore Memory
    print(f"DEBUG: Creating agent with AgentCore Memory...")
    session_agent = Agent(
        model=model,
        tools=[initiate_password_reset, complete_password_reset],
        system_prompt=SYSTEM_PROMPT_TEMPLATE,
        session_manager=session_manager
    )
    
    print(f"DEBUG: Agent created successfully with AgentCore Memory - session: {session_id}")
    print(f"DEBUG: Agent has {len(session_agent.messages)} existing messages")

    print(f"Invoking agent with: {user_input}")
    
    try:
        # Use streaming approach with clean text-only output (FIXED)
        response_content = ""
        async for chunk in session_agent.stream_async(user_input):
            # Only process chunks that contain actual text content
            if isinstance(chunk, dict) and 'data' in chunk:
                if isinstance(chunk['data'], str) and chunk['data'].strip():
                    # This is actual text content - yield it cleanly
                    response_content += chunk['data']
                    yield chunk['data']
            elif isinstance(chunk, str) and chunk.strip():
                # Direct string content that's not empty
                response_content += chunk
                yield chunk
            # Completely ignore all other chunk types (debug data, metadata, etc.)
        
        print(f"DEBUG: Agent response completed: {len(response_content)} characters")
        print(f"DEBUG: Final agent has {len(session_agent.messages)} messages in memory")
        print(f"DEBUG: Session management: AgentCore Memory (memory_id: {memory_id})")
        
    except Exception as e:
        error_msg = f"Error invoking agent: {str(e)}"
        print(error_msg)
        yield error_msg


if __name__ == "__main__":
    app.run()
