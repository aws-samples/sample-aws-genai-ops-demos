"""Helper for calling Bedrock to analyze code with AI."""

import json
import logging
import sys
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

# Ensure logs go to stderr, not stdout (stdout is used for MCP JSON-RPC)
if not logger.handlers:
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(logging.Formatter("%(levelname)s [%(name)s] %(message)s"))
    logger.addHandler(_handler)
    logger.setLevel(logging.WARNING)


def analyze_code_for_prompts(file_content: str, file_path: str) -> List[Dict[str, Any]]:
    """Use Bedrock AI to identify prompts in code.
    
    Args:
        file_content: The content of the file to analyze
        file_path: Path to the file (for context)
        
    Returns:
        List of prompts found with their locations
    """
    try:
        import boto3
    except ImportError:
        logger.debug("boto3 not installed, skipping AI-powered prompt detection")
        return []

    bedrock = boto3.client('bedrock-runtime', region_name='us-east-1')
    
    # Use Nova Micro (cheapest, fast enough for this task)
    model_id = "us.amazon.nova-micro-v1:0"
    
    prompt = f"""Analyze this code file and identify all LLM prompt strings.

File: {file_path}

Look for strings that are sent to LLMs (instructions, system prompts, user prompts).

For each prompt found, return JSON:
{{
  "line": <line_number>,
  "variable_name": "<name>",
  "prompt_preview": "<first 50 chars>",
  "estimated_tokens": <number>
}}

Return ONLY a JSON array, no other text.

Code:
```
{file_content}
```"""
    
    try:
        response = bedrock.converse(
            modelId=model_id,
            messages=[{
                "role": "user",
                "content": [{"text": prompt}]
            }],
            inferenceConfig={
                "maxTokens": 4000,
                "temperature": 0
            }
        )
        
        # Extract response
        response_text = response['output']['message']['content'][0]['text'].strip()
        
        # Clean up response (remove markdown code blocks if present)
        if '```json' in response_text or '```' in response_text:
            # Extract JSON from markdown code block
            start = response_text.find('[')
            end = response_text.rfind(']') + 1
            if start != -1 and end > start:
                response_text = response_text[start:end]
        
        # Parse JSON
        prompts = json.loads(response_text)
        return prompts if isinstance(prompts, list) else []
        
    except json.JSONDecodeError as e:
        logger.warning("JSON parse error for %s: %s", file_path, e)
        logger.debug("Response was: %s...", response_text[:200])
        return []
    except Exception as e:
        logger.warning("Error analyzing %s: %s", file_path, e)
        return []
