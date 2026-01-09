"""Sample AgentCore code for testing the scanner."""

import asyncio
from bedrock_agentcore import BedrockAgentCoreApp
from bedrock_agentcore.runtime.context import RequestContext
from strands import Agent

# Initialize AgentCore app
app = BedrockAgentCoreApp(debug=True)

# Initialize Strands agent
agent = Agent(
    model="anthropic.claude-3-5-sonnet-20241022-v1:0",
    tools=[],
)


@app.entrypoint
async def streaming_agent(payload, context: RequestContext):
    """Main agent entrypoint with streaming support."""
    user_message = payload.get("prompt", "Hello")
    session_id = context.session_id

    # Stream responses as they're generated
    stream = agent.stream_async(user_message)
    async for event in stream:
        if "data" in event:
            yield event["data"]
        elif "message" in event:
            yield event["message"]


@app.async_task
async def background_data_processing():
    """Background task for data processing."""
    # Simulate long-running task
    await asyncio.sleep(30)
    # Process data here
    return {"status": "completed"}


@app.entrypoint
def sync_agent(payload, context: RequestContext):
    """Synchronous agent for simple queries."""
    prompt = payload.get("prompt", "")
    session_id = context.session_id

    # Simple response
    result = agent(prompt)
    return {"result": result.message, "session_id": session_id}


@app.ping
def custom_health_check():
    """Custom health check logic."""
    # Check if system is busy
    if processing_data():
        return "HealthyBusy"
    return "Healthy"


def processing_data():
    """Check if background processing is active."""
    # Implementation here
    return False


if __name__ == "__main__":
    app.run()
