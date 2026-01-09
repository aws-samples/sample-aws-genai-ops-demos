"""Example demonstrating Bedrock Prompt Routing detection."""

import boto3
import json

bedrock = boto3.client('bedrock-runtime', region_name='us-east-1')

# Example 1: Using Prompt Router (DETECTED - Positive feedback)
ROUTER_ARN = "arn:aws:bedrock:us-east-1:517675598740:prompt-router/z0e0g1c7y7za"

def process_with_routing(prompt):
    """
    Using prompt router ARN - Scanner will detect this!
    
    Scanner provides positive feedback:
    - Confirms routing is enabled
    - Suggests monitoring via CloudWatch
    - Recommends best practices for optimization
    """
    body = json.dumps({
        "messages": [
            {"role": "user", "content": prompt}
        ],
        "inferenceConfig": {
            "max_new_tokens": 2000
        }
    })
    
    # Router automatically selects optimal model
    response = bedrock.invoke_model(
        modelId=ROUTER_ARN,  # Using router instead of specific model
        body=body
    )
    
    return json.loads(response['body'].read())


# Example 2: Mixed complexity WITHOUT routing (OPPORTUNITY DETECTED)
def simple_task_no_routing(text):
    """
    Simple prompt using expensive model directly.
    
    Scanner will detect: Mixed complexity opportunity
    - This simple task could use Haiku via routing
    - Currently paying for Sonnet unnecessarily
    """
    prompt = "Summarize the following text briefly and list the key points in a simple, straightforward format. Keep it concise."
    
    body = json.dumps({
        "messages": [{"role": "user", "content": prompt}]
    })
    
    # Using expensive model directly
    response = bedrock.invoke_model(
        modelId="anthropic.claude-3-5-sonnet-20241022-v2:0",
        body=body
    )
    
    return json.loads(response['body'].read())


def complex_task_no_routing(data):
    """
    Complex prompt using expensive model directly.
    
    Scanner will detect: Mixed complexity opportunity
    - This complex task needs Sonnet
    - But simple_task_no_routing() could use Haiku
    - Routing would optimize automatically
    """
    prompt = "Analyze the following data comprehensively and in great detail. Evaluate multiple perspectives carefully, compare different approaches systematically, and provide detailed reasoning for all recommendations."
    
    body = json.dumps({
        "messages": [{"role": "user", "content": prompt}]
    })
    
    # Using expensive model directly
    response = bedrock.invoke_model(
        modelId="anthropic.claude-3-5-sonnet-20241022-v2:0",
        body=body
    )
    
    return json.loads(response['body'].read())


# Scanner Output Examples:

# For Example 1 (with routing):
"""
{
  "type": "prompt_routing_detected",
  "router_arn": "arn:aws:bedrock:us-east-1:517675598740:prompt-router/z0e0g1c7y7za",
  "cost_consideration": "Prompt Routing is enabled. This automatically optimizes cost by routing simple prompts to cheaper models.",
  "best_practices": [
    "Monitor routing decisions via CloudWatch metrics",
    "Review which prompts route to which models",
    "Adjust routing criteria if needed"
  ]
}
"""

# For Example 2 (without routing):
"""
{
  "type": "prompt_routing_opportunity",
  "current_model": "claude-3-5-sonnet",
  "complexity_variation": {
    "min": 1,
    "max": 4,
    "range": 3
  },
  "cost_consideration": "Using claude-3-5-sonnet for all requests, but prompts vary in complexity. Prompt Routing could automatically use cheaper models for simpler prompts.",
  "potential_savings": "50%+ for simple prompts routed to cheaper models"
}
"""

# How Prompt Routing Works:
"""
1. Create router in AWS Console or via API
2. Configure routing criteria (quality vs cost trade-off)
3. Use router ARN instead of model ID
4. Router analyzes each prompt and selects optimal model:
   - Simple prompts → Haiku (cheap)
   - Complex prompts → Sonnet (powerful)
5. Monitor via CloudWatch metrics

Benefits:
- Automatic cost optimization
- No code changes per request
- Maintains quality for complex tasks
- Reduces cost for simple tasks
- Future-proof as new models are added
"""

if __name__ == "__main__":
    # Example 1: With routing (optimized)
    result1 = process_with_routing("Summarize this text")
    
    # Example 2: Without routing (opportunity for optimization)
    result2 = simple_task_no_routing("Some text")
    result3 = complex_task_no_routing({"data": "values"})
