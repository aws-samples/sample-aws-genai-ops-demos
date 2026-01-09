"""Sample code with Bedrock usage for testing the scanner."""

import boto3
import json

# Initialize Bedrock client
bedrock = boto3.client('bedrock-runtime', region_name='us-east-1')

def generate_text_sync(prompt: str) -> str:
    """Generate text using Claude Sonnet synchronously."""
    
    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 8000,
        "messages": [
            {
                "role": "user",
                "content": prompt
            }
        ]
    })
    
    response = bedrock.invoke_model(
        modelId="anthropic.claude-3-sonnet-20240229-v1:0",
        body=body
    )
    
    return json.loads(response['body'].read())


def generate_text_stream(prompt: str):
    """Generate text using Claude Haiku with streaming."""
    
    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 2000,
        "messages": [
            {
                "role": "user",
                "content": prompt
            }
        ]
    })
    
    response = bedrock.invoke_model_with_response_stream(
        modelId="anthropic.claude-3-5-haiku-20241022-v1:0",
        body=body
    )
    
    for event in response['body']:
        chunk = json.loads(event['chunk']['bytes'])
        if chunk['type'] == 'content_block_delta':
            yield chunk['delta']['text']


# Example with large static prompt (optimization opportunity)
SYSTEM_PROMPT = """You are a helpful AI assistant with extensive knowledge across many domains. 
Your role is to provide accurate, thoughtful, and comprehensive responses to user queries.
Always be respectful, clear, and concise in your communication. If you're unsure about something,
acknowledge the uncertainty rather than providing potentially incorrect information. Consider
multiple perspectives when appropriate and provide balanced viewpoints. Use examples to illustrate
complex concepts when helpful. Break down complicated topics into digestible parts."""

def chat_with_context(user_message: str) -> str:
    """Chat with system context."""
    
    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 4096,
        "system": SYSTEM_PROMPT,
        "messages": [
            {
                "role": "user",
                "content": user_message
            }
        ]
    })
    
    response = bedrock.invoke_model(
        modelId="anthropic.claude-3-opus-20240229-v1:0",
        body=body
    )
    
    return json.loads(response['body'].read())
