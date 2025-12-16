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

# Create the AgentCore app
app = BedrockAgentCoreApp()

# Get User Pool configuration from environment
USER_POOL_ID = os.environ.get('USER_POOL_ID', '')
USER_POOL_CLIENT_ID = os.environ.get('USER_POOL_CLIENT_ID', '')
AWS_REGION = os.environ.get('AWS_REGION', 'us-east-1')

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
PASSWORD_RESET_SYSTEM_PROMPT = """## Task Summary:
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
    global MEMORY_ID
    
    if MEMORY_ID is None:
        try:
            # Create memory for password reset sessions
            memory_response = await memory_client.create_memory(
                name="password-reset-sessions",
                description="Session memory for password reset conversations"
            )
            MEMORY_ID = memory_response['memoryId']
            print(f"Created AgentCore Memory: {MEMORY_ID}")
        except Exception as e:
            print(f"Failed to create AgentCore Memory: {e}")
            # Fall back to simple session management
            return None
    
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
    if not session_id:
        session_id = "default"
    
    print(f"Processing message for session: {session_id}")

    # Try to use AgentCore Memory, fall back to simple sessions if needed
    memory_id = await get_or_create_memory()
    session_agent = None
    
    if memory_id:
        # Use AgentCore Memory for session management
        try:
            print(f"Using AgentCore Memory for session management")
            
            # Configure AgentCore Memory
            memory_config = AgentCoreMemoryConfig(
                memory_id=memory_id,
                session_id=session_id,
                actor_id=session_id,  # Use session_id as actor_id for simplicity
                retrieval_config={
                    f"/conversations/{session_id}": RetrievalConfig(
                        top_k=10,
                        relevance_score=0.2
                    )
                }
            )
            
            # Create session manager
            session_manager = AgentCoreMemorySessionManager(
                agentcore_memory_config=memory_config,
                region_name=AWS_REGION
            )
            
            # Create agent with AgentCore Memory
            session_agent = Agent(
                model=model,
                tools=[initiate_password_reset, complete_password_reset],
                system_prompt=PASSWORD_RESET_SYSTEM_PROMPT,
                session_manager=session_manager
            )
            
            print(f"Agent created with AgentCore Memory - session: {session_id}")
            
        except Exception as e:
            print(f"AgentCore Memory failed, falling back to simple sessions: {e}")
            memory_id = None
    
    if not memory_id or session_agent is None:
        # Fall back to simple session management
        print(f"Using simple session management")
        
        if session_id not in sessions:
            sessions[session_id] = {
                "messages": [],
                "user_email": None,
                "step": "initial"
            }
        
        session_data = sessions[session_id]
        
        # Create agent without AgentCore Memory
        session_agent = Agent(
            model=model,
            tools=[initiate_password_reset, complete_password_reset],
            system_prompt=PASSWORD_RESET_SYSTEM_PROMPT
        )
        
        # Restore session messages if any
        if session_data["messages"]:
            session_agent.messages = session_data["messages"].copy()
        
        print(f"Agent created - {len(session_agent.messages)} existing messages from session")

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
        
        # Save session state (only for simple sessions - AgentCore Memory handles this automatically)
        if not memory_id and session_id in sessions:
            sessions[session_id]["messages"] = session_agent.messages.copy()
        
        print(f"Agent response completed: {len(response_content)} characters")
        print(f"Session management: {'AgentCore Memory' if memory_id else 'Simple sessions'}")
        
    except Exception as e:
        error_msg = f"Error invoking agent: {str(e)}"
        print(error_msg)
        yield error_msg


if __name__ == "__main__":
    app.run()
