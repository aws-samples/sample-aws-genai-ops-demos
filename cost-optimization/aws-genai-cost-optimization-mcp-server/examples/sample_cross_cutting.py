"""Example demonstrating cross-cutting cost pattern: Streaming in AgentCore Runtime."""

from bedrock_agentcore import BedrockAgentCoreApp
import boto3
import json

app = BedrockAgentCoreApp()
bedrock = boto3.client('bedrock-runtime', region_name='us-east-1')


# Example 1: COST CONCERN - Streaming in AgentCore
@app.entrypoint
async def streaming_chat_agent(payload):
    """
    This pattern triggers cross-cutting cost detection.
    
    Streaming responses extend AgentCore compute time:
    - Bedrock token cost: Same as synchronous
    - AgentCore compute: 6x longer billing (30s vs 5s)
    
    Question: Does this use case need real-time streaming?
    """
    user_message = payload.get("prompt", "Hello")
    
    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 2000,
        "messages": [{"role": "user", "content": user_message}]
    })
    
    # Streaming response - extends AgentCore compute time
    response = bedrock.invoke_model_with_response_stream(
        modelId="anthropic.claude-3-sonnet-20240229-v1:0",
        body=body
    )
    
    # Yielding chunks keeps AgentCore active
    for event in response['body']:
        chunk = json.loads(event['chunk']['bytes'])
        if chunk['type'] == 'content_block_delta':
            yield chunk['delta']['text']


# Example 2: COST OPTIMIZED - Synchronous in AgentCore
@app.entrypoint
def sync_api_agent(payload):
    """
    Cost-optimized pattern for AgentCore.
    
    Synchronous responses minimize AgentCore compute time:
    - Bedrock token cost: Same as streaming
    - AgentCore compute: Minimal (5s vs 30s)
    
    Use when: API endpoints, batch processing, no human waiting
    """
    user_message = payload.get("prompt", "Hello")
    
    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 2000,
        "messages": [{"role": "user", "content": user_message}]
    })
    
    # Synchronous response - minimizes AgentCore compute time
    response = bedrock.invoke_model(
        modelId="anthropic.claude-3-sonnet-20240229-v1:0",
        body=body
    )
    
    result = json.loads(response['body'].read())
    return {"result": result['content'][0]['text']}


# Example 3: HYBRID APPROACH - Conditional streaming
@app.entrypoint
async def smart_agent(payload):
    """
    Smart pattern: Stream only when necessary.
    
    Uses synchronous for short responses, streaming for long ones.
    Balances UX and cost.
    """
    user_message = payload.get("prompt", "Hello")
    expected_length = payload.get("expected_length", "short")  # short, medium, long
    
    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 2000,
        "messages": [{"role": "user", "content": user_message}]
    })
    
    # Stream only for long responses where UX matters
    if expected_length == "long":
        response = bedrock.invoke_model_with_response_stream(
            modelId="anthropic.claude-3-sonnet-20240229-v1:0",
            body=body
        )
        
        for event in response['body']:
            chunk = json.loads(event['chunk']['bytes'])
            if chunk['type'] == 'content_block_delta':
                yield chunk['delta']['text']
    else:
        # Use synchronous for short/medium responses
        response = bedrock.invoke_model(
            modelId="anthropic.claude-3-sonnet-20240229-v1:0",
            body=body
        )
        
        result = json.loads(response['body'].read())
        return {"result": result['content'][0]['text']}


if __name__ == "__main__":
    app.run()
