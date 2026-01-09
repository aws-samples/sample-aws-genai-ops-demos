"""
Example: Bedrock Service Tier configurations and optimization opportunities.

Service tiers allow you to optimize cost vs performance:
- Reserved: Pre-reserved capacity, 99.5% uptime, fixed monthly pricing
- Priority: Fastest response, price premium
- Standard/Default: Consistent performance, standard pricing  
- Flex: Cost-effective, pricing discount

Documentation: https://docs.aws.amazon.com/bedrock/latest/userguide/service-tiers-inference.html
"""

import boto3
import json

bedrock = boto3.client('bedrock-runtime', region_name='us-east-1')

# ❌ MISSING service_tier - optimization opportunity!
# This uses default (Standard) tier without considering cost optimization
response1 = bedrock.invoke_model(
    modelId="amazon.nova-lite-v1:0",
    body=json.dumps({
        "messages": [{"role": "user", "content": "Summarize this document"}]
    })
)

# ✅ GOOD: Using Flex tier for batch processing (cost savings)
response2 = bedrock.invoke_model(
    modelId="anthropic.claude-3-haiku-20240307-v1:0",
    body=json.dumps({
        "messages": [{"role": "user", "content": "Process batch data"}],
        "service_tier": "flex"
    })
)

# ✅ GOOD: Using Priority tier for customer-facing chatbot (low latency)
response3 = bedrock.converse(
    modelId="anthropic.claude-3-sonnet-20240229-v1:0",
    messages=[{"role": "user", "content": "Help customer with urgent issue"}],
    service_tier="priority"
)

# ❌ MISSING service_tier - another optimization opportunity!
# Background job that could use Flex tier for cost savings
response4 = bedrock.converse_stream(
    modelId="amazon.nova-pro-v1:0",
    messages=[{"role": "user", "content": "Generate weekly report"}]
)

# OpenAI SDK Examples (Bedrock-compatible)
from openai import OpenAI

client = OpenAI(
    base_url="https://bedrock-runtime.us-west-2.amazonaws.com/openai/v1",
    api_key="$AWS_BEARER_TOKEN_BEDROCK"
)

# ❌ MISSING service_tier - OpenAI SDK also supports this parameter!
completion1 = client.chat.completions.create(
    model="openai.gpt-oss-20b-1:0",
    messages=[{"role": "user", "content": "Process batch data"}]
)

# ✅ GOOD: Using service_tier with OpenAI SDK
completion2 = client.chat.completions.create(
    model="openai.gpt-oss-20b-1:0",
    messages=[{"role": "user", "content": "Urgent customer request"}],
    service_tier="priority"
)
