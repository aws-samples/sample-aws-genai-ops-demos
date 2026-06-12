"""Helper for calling Bedrock to analyze code with AI.

Includes timeout, retry, and auth-failure shortcut to avoid silently slow scans.
"""

import json
import logging
import sys
import time
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

# Ensure logs go to stderr, not stdout (stdout is used for MCP JSON-RPC)
if not logger.handlers:
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(logging.Formatter("%(levelname)s [%(name)s] %(message)s"))
    logger.addHandler(_handler)
    logger.setLevel(logging.WARNING)

# Module-level flag: once we detect auth failure, skip all subsequent AI calls
_ai_disabled = False
_ai_disable_reason = ""

# Configuration
BEDROCK_TIMEOUT_SECONDS = 10
BEDROCK_MAX_RETRIES = 2
BEDROCK_RETRY_BASE_DELAY = 1.0  # seconds


def is_ai_available() -> bool:
    """Check if AI-powered analysis is available (credentials work, boto3 installed)."""
    return not _ai_disabled


def analyze_code_for_prompts(file_content: str, file_path: str) -> List[Dict[str, Any]]:
    """Use Bedrock AI to identify prompts in code.

    Args:
        file_content: The content of the file to analyze
        file_path: Path to the file (for context)

    Returns:
        List of prompts found with their locations
    """
    global _ai_disabled, _ai_disable_reason

    # Fast path: if AI was already disabled due to auth/config failure, skip
    if _ai_disabled:
        logger.debug("AI analysis skipped (%s): %s", _ai_disable_reason, file_path)
        return []

    # Guard boto3 import
    try:
        import boto3
        from botocore.config import Config
        from botocore.exceptions import (
            NoCredentialsError,
            ClientError,
            EndpointConnectionError,
        )
    except ImportError:
        _ai_disabled = True
        _ai_disable_reason = "boto3 not installed"
        logger.info("boto3 not installed — AI prompt detection disabled. Install with: pip install 'awslabs-genai-cost-optim-mcp-server[ai]'")
        return []

    # Configure client with timeout
    config = Config(
        connect_timeout=BEDROCK_TIMEOUT_SECONDS,
        read_timeout=BEDROCK_TIMEOUT_SECONDS,
        retries={"max_attempts": 0},  # We handle retries ourselves
    )

    try:
        bedrock = boto3.client("bedrock-runtime", region_name="us-east-1", config=config)
    except NoCredentialsError:
        _ai_disabled = True
        _ai_disable_reason = "no AWS credentials"
        logger.info("No AWS credentials found — AI prompt detection disabled for this session")
        return []
    except Exception as e:
        _ai_disabled = True
        _ai_disable_reason = f"client init failed: {e}"
        logger.warning("Failed to create Bedrock client: %s", e)
        return []

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

    # Retry loop with exponential backoff
    last_error = None
    for attempt in range(BEDROCK_MAX_RETRIES + 1):
        try:
            response = bedrock.converse(
                modelId=model_id,
                messages=[{"role": "user", "content": [{"text": prompt}]}],
                inferenceConfig={"maxTokens": 4000, "temperature": 0},
            )

            # Extract response
            response_text = response["output"]["message"]["content"][0]["text"].strip()

            # Clean up response (remove markdown code blocks if present)
            if "```json" in response_text or "```" in response_text:
                start = response_text.find("[")
                end = response_text.rfind("]") + 1
                if start != -1 and end > start:
                    response_text = response_text[start:end]

            # Parse JSON
            prompts = json.loads(response_text)
            return prompts if isinstance(prompts, list) else []

        except (NoCredentialsError, EndpointConnectionError) as e:
            # Permanent failures — disable AI for the rest of the session
            _ai_disabled = True
            _ai_disable_reason = f"auth/connection error: {type(e).__name__}"
            logger.info("Bedrock unavailable (%s) — disabling AI for this session", e)
            return []

        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code in ("ExpiredTokenException", "UnrecognizedClientException", "AccessDeniedException"):
                # Auth failures — disable permanently for this session
                _ai_disabled = True
                _ai_disable_reason = f"auth error: {error_code}"
                logger.info("Bedrock auth failed (%s) — disabling AI for this session", error_code)
                return []
            elif error_code == "ThrottlingException" and attempt < BEDROCK_MAX_RETRIES:
                # Throttling — retry with backoff
                delay = BEDROCK_RETRY_BASE_DELAY * (2 ** attempt)
                logger.debug("Throttled on attempt %d, retrying in %.1fs", attempt + 1, delay)
                time.sleep(delay)
                last_error = e
                continue
            else:
                last_error = e
                break

        except json.JSONDecodeError as e:
            logger.debug("JSON parse error for %s: %s", file_path, e)
            return []

        except Exception as e:
            last_error = e
            break

    if last_error:
        logger.debug("Bedrock call failed for %s after %d attempts: %s", file_path, BEDROCK_MAX_RETRIES + 1, last_error)
    return []
