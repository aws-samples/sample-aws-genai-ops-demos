"""Example demonstrating Nova optimization opportunity detection."""

import boto3
import json

bedrock = boto3.client('bedrock-runtime', region_name='us-east-1')

# Example 1: Nova model with large prompt (optimization opportunity)
SYSTEM_PROMPT = """You are an expert AI assistant specializing in data analysis and insights generation.
Your role is to analyze complex datasets and provide actionable recommendations based on patterns,
trends, and anomalies you identify. Always structure your analysis with clear sections including
executive summary, detailed findings, methodology, and recommendations. Use data-driven reasoning
and cite specific metrics when making claims. Consider multiple perspectives and potential biases
in the data. Provide confidence levels for your conclusions and suggest areas for further investigation."""

def analyze_with_nova_micro(data):
    """
    This pattern triggers Nova optimization opportunity detection.
    
    Scanner will suggest: AWS Nova Prompt Optimizer can test variations
    to reduce this 450+ token prompt by 20-40% while maintaining quality.
    """
    body = json.dumps({
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Analyze this data: {data}"}
        ],
        "inferenceConfig": {
            "max_new_tokens": 2000,
            "temperature": 0.7
        }
    })
    
    response = bedrock.invoke_model(
        modelId="amazon.nova-micro-v1:0",
        body=body
    )
    
    return json.loads(response['body'].read())


def analyze_with_nova_lite(data):
    """
    Same large prompt with Nova Lite.
    
    Nova Prompt Optimizer can:
    - Test shorter variations
    - Find optimal prompt length
    - Maintain output quality
    - Reduce token costs by 20-40%
    """
    body = json.dumps({
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Analyze: {data}"}
        ],
        "inferenceConfig": {
            "max_new_tokens": 2000
        }
    })
    
    response = bedrock.invoke_model(
        modelId="amazon.nova-lite-v1:0",
        body=body
    )
    
    return json.loads(response['body'].read())


# Example 2: Optimized prompt (no suggestion)
OPTIMIZED_PROMPT = "Analyze data and provide insights with confidence levels."

def analyze_optimized(data):
    """
    Short, optimized prompt - no Nova optimization suggestion.
    
    This prompt is already concise and won't trigger the detector.
    """
    body = json.dumps({
        "messages": [
            {"role": "system", "content": OPTIMIZED_PROMPT},
            {"role": "user", "content": data}
        ]
    })
    
    response = bedrock.invoke_model(
        modelId="amazon.nova-micro-v1:0",
        body=body
    )
    
    return json.loads(response['body'].read())


# How to use Nova Prompt Optimizer (when suggested by scanner):
"""
1. Install: pip install nova-prompt-optimizer

2. Prepare test dataset:
   - Create inputs.json with test cases
   - Define expected outputs or metrics

3. Run optimization:
   from amzn_nova_prompt_optimizer import NovaPromptOptimizer
   
   optimizer = NovaPromptOptimizer(
       model_id="amazon.nova-micro-v1:0",
       region="us-east-1"
   )
   
   optimized_prompt = optimizer.optimize(
       original_prompt=SYSTEM_PROMPT,
       test_dataset="inputs.json",
       metric="accuracy"
   )

4. Replace original prompt with optimized version

Result: 20-40% token reduction while maintaining quality
"""

if __name__ == "__main__":
    # Scanner will detect optimization opportunity in functions 1 & 2
    # but not in function 3 (already optimized)
    sample_data = {"sales": [100, 200, 150], "region": "US"}
    
    result1 = analyze_with_nova_micro(sample_data)
    result2 = analyze_with_nova_lite(sample_data)
    result3 = analyze_optimized(sample_data)
