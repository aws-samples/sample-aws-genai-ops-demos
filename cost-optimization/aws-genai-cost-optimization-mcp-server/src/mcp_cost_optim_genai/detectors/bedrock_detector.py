"""Detector for Amazon Bedrock usage patterns."""

import re
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any

from .base import BaseDetector


class BedrockDetector(BaseDetector):
    """Detects Amazon Bedrock API usage in code."""

    # Generic Bedrock model ID pattern - detects ANY Bedrock model
    # Matches: [region.]provider.model-name-with-hyphens[-version]
    # Examples:
    #   - anthropic.claude-3-7-sonnet-20250219-v1:0
    #   - us.anthropic.claude-sonnet-4-20250514-v1:0
    #   - global.anthropic.claude-sonnet-4-20250514-v1:0
    #   - amazon.nova-pro-v1:0
    #   - meta.llama3-70b-instruct-v1:0
    # 
    # Key requirement: model name must contain at least one hyphen (e.g., "claude-3", "nova-pro", "llama3-70b")
    # This prevents false positives like "meta.env", "amazon.com", "meta.client"
    BEDROCK_MODEL_ID_PATTERN = r'\b((?:global\.|us\.|eu\.|apac\.)?(?:anthropic|amazon|meta|cohere|mistral|ai21|stability|deepseek|openai|qwen|twelvelabs)\.[a-z0-9]+(?:-[a-z0-9]+)+(?:-v\d+:\d+)?(?::\d+k)?(?::mm)?)\b'

    # API call patterns
    # Includes both direct boto3 calls and LangChain wrappers (widely used in industry)
    INVOKE_PATTERNS = {
        # Direct boto3 Bedrock Runtime API calls
        "invoke_model": r"invoke_model\s*\(",
        "invoke_model_with_response_stream": r"invoke_model_with_response_stream\s*\(",
        "converse": r"converse\s*\(",
        "converse_stream": r"converse_stream\s*\(",
        # OpenAI Chat Completions API (Bedrock-compatible)
        "chat_completions_create": r"chat\.completions\.create\s*\(",
        # LangChain Bedrock wrappers (very common in real-world projects)
        "ChatBedrockConverse": r"ChatBedrockConverse\s*\(",
        "ChatBedrock": r"ChatBedrock\s*\(",
        "BedrockLLM": r"BedrockLLM\s*\(",
        "Bedrock": r"Bedrock\s*\(",  # Legacy LangChain class
    }

    def can_analyze(self, file_path: Path) -> bool:
        """Check if file is Python or TypeScript/JavaScript."""
        return file_path.suffix in [".py", ".ts", ".js", ".tsx", ".jsx"]
    
    def _parse_model_id(self, model_id: str) -> Dict[str, Any]:
        """Parse a Bedrock model ID into structured components.
        
        Args:
            model_id: Full model ID (e.g., "us.anthropic.claude-3-7-sonnet-20250219-v1:0")
            
        Returns:
            Dictionary with parsed components:
            - provider: anthropic, amazon, meta, etc.
            - family: claude, nova, llama, etc.
            - version: extracted version number (e.g., "3.7", "4.5")
            - tier: sonnet, haiku, opus, pro, lite, etc. (if applicable)
            - region_prefix: us, eu, apac (if present)
            - full_model_id: original model ID
        """
        model_lower = model_id.lower()
        parsed = {
            "full_model_id": model_id,
            "provider": None,
            "family": None,
            "version": None,
            "tier": None,
            "region_prefix": None
        }
        
        # Extract region prefix (global, us, eu, apac)
        region_match = re.match(r'^(global|us|eu|apac)\.', model_lower)
        if region_match:
            parsed["region_prefix"] = region_match.group(1)
            model_lower = model_lower[len(region_match.group(0)):]  # Remove prefix
        
        # Extract provider (first part before first dot)
        provider_match = re.match(r'^([a-z0-9]+)\.', model_lower)
        if provider_match:
            parsed["provider"] = provider_match.group(1)
        
        # Extract family and version based on provider
        if "anthropic" in model_lower:
            parsed["family"] = "claude"
            
            # Extract tier (opus, sonnet, haiku)
            if "opus" in model_lower:
                parsed["tier"] = "opus"
            elif "sonnet" in model_lower:
                parsed["tier"] = "sonnet"
            elif "haiku" in model_lower:
                parsed["tier"] = "haiku"
            
            # Extract version (e.g., "3.7", "4.5", "4")
            # Patterns: claude-3-7, claude-4-5, claude-sonnet-4, etc.
            version_patterns = [
                r'claude-(\d+)-(\d+)-(?:sonnet|haiku|opus)',  # claude-3-7-sonnet
                r'(?:sonnet|haiku|opus)-(\d+)-(\d+)-\d{8}',   # sonnet-4-5-20250929 (with date)
                r'(?:sonnet|haiku|opus)-(\d+)-\d{8}',         # sonnet-4-20250514 (with date, single version)
                r'claude-(\d+)-(\d+)',                        # claude-3-7 (fallback)
                r'claude-(\d+\.\d+)',                         # claude-3.7
            ]
            
            for pattern in version_patterns:
                match = re.search(pattern, model_lower)
                if match:
                    if len(match.groups()) == 2:
                        parsed["version"] = f"{match.group(1)}.{match.group(2)}"
                    else:
                        parsed["version"] = match.group(1)
                    break
                    
        elif "amazon" in model_lower:
            # Determine family (nova, titan)
            if "nova" in model_lower:
                parsed["family"] = "nova"
                
                # Extract tier
                if "premier" in model_lower:
                    parsed["tier"] = "premier"
                elif "pro" in model_lower:
                    parsed["tier"] = "pro"
                elif "lite" in model_lower:
                    parsed["tier"] = "lite"
                elif "micro" in model_lower:
                    parsed["tier"] = "micro"
                elif "canvas" in model_lower:
                    parsed["tier"] = "canvas"
                elif "reel" in model_lower:
                    parsed["tier"] = "reel"
                elif "sonic" in model_lower:
                    parsed["tier"] = "sonic"
                
                # Extract version
                version_match = re.search(r'-v(\d+):(\d+)', model_lower)
                if version_match:
                    parsed["version"] = f"{version_match.group(1)}.{version_match.group(2)}"
                    
            elif "titan" in model_lower:
                parsed["family"] = "titan"
                
                # Extract type
                if "embed" in model_lower:
                    parsed["tier"] = "embed"
                elif "text" in model_lower:
                    parsed["tier"] = "text"
                elif "image" in model_lower:
                    parsed["tier"] = "image"
                
                # Extract version
                version_match = re.search(r'-v(\d+)', model_lower)
                if version_match:
                    parsed["version"] = version_match.group(1)
                    
        elif "meta" in model_lower:
            parsed["family"] = "llama"
            
            # Extract version (llama3, llama3-1, llama4, etc.)
            version_match = re.search(r'llama(\d+)(?:-(\d+))?', model_lower)
            if version_match:
                if version_match.group(2):
                    parsed["version"] = f"{version_match.group(1)}.{version_match.group(2)}"
                else:
                    parsed["version"] = version_match.group(1)
            
            # Extract size as tier
            size_match = re.search(r'(\d+)b', model_lower)
            if size_match:
                parsed["tier"] = f"{size_match.group(1)}b"
                
        elif "mistral" in model_lower or "cohere" in model_lower or "ai21" in model_lower:
            # Generic extraction for other providers
            parsed["family"] = parsed["provider"]
            
            # Try to extract version
            version_match = re.search(r'-v(\d+)(?::(\d+))?', model_lower)
            if version_match:
                if version_match.group(2):
                    parsed["version"] = f"{version_match.group(1)}.{version_match.group(2)}"
                else:
                    parsed["version"] = version_match.group(1)
        
        return parsed

    def analyze(self, content: str, file_path: str) -> List[Dict[str, Any]]:
        """Analyze content for Bedrock usage."""
        findings = []

        # Check for Bedrock client initialization
        if self._has_bedrock_client(content):
            findings.append({
                "type": "bedrock_client_detected",
                "file": file_path,
                "service": "bedrock",
                "description": "Amazon Bedrock client detected in this file"
            })

        # Detect Strands BedrockModel configuration
        strands_findings = self._detect_strands_bedrock_model(content, file_path)
        findings.extend(strands_findings)

        # Detect model usage
        model_findings = self._detect_models(content, file_path)
        findings.extend(model_findings)

        # Detect API call patterns
        api_findings = self._detect_api_calls(content, file_path)
        findings.extend(api_findings)

        # Detect token usage patterns
        token_findings = self._detect_token_patterns(content, file_path)
        findings.extend(token_findings)

        # Detect Nova explicit caching opportunities
        nova_caching_findings = self._detect_nova_explicit_caching_opportunity(content, file_path)
        findings.extend(nova_caching_findings)

        # Detect caching with cross-region inference anti-pattern
        cross_region_findings = self._detect_caching_cross_region_antipattern(content, file_path)
        findings.extend(cross_region_findings)
        
        # Detect dynamic variables in system prompts
        system_prompt_findings = self._detect_dynamic_system_prompts(content, file_path)
        findings.extend(system_prompt_findings)

        # Detect prompt routing usage and opportunities
        routing_findings = self._detect_prompt_routing(content, file_path, findings)
        findings.extend(routing_findings)

        # Detect service tier configuration
        service_tier_findings = self._detect_service_tier(content, file_path)
        findings.extend(service_tier_findings)

        # Add clickable file links to all findings
        from ..utils.file_links import create_file_link
        for finding in findings:
            if 'line' in finding and finding.get('file'):
                finding['file_link'] = create_file_link(finding['file'], finding['line'])

        return findings

    def _has_bedrock_client(self, content: str) -> bool:
        """Check if Bedrock client is initialized."""
        patterns = [
            r"boto3\.client\(['\"]bedrock-runtime['\"]",
            r"from\s+.*bedrock.*\s+import",
            r"BedrockRuntime",
            r"@aws-sdk/client-bedrock",
            r"from\s+strands\.models\s+import\s+BedrockModel",
            r"BedrockModel\s*\(",
            # OpenAI SDK with Bedrock endpoint
            r"bedrock-runtime\.[a-z0-9-]+\.amazonaws\.com/openai",
        ]
        return any(re.search(pattern, content, re.IGNORECASE) for pattern in patterns)

    def _detect_strands_bedrock_model(self, content: str, file_path: str) -> List[Dict[str, Any]]:
        """Detect AWS Strands BedrockModel configuration and analyze settings."""
        findings = []
        
        pattern = r'BedrockModel\s*\(((?:[^()]*|\([^()]*\))*)\)'
        matches = re.finditer(pattern, content, re.DOTALL)
        
        for match in matches:
            line_num = content[:match.start()].count('\n') + 1
            params_str = match.group(1)
            
            model_id = None
            streaming = None
            
            model_id_match = re.search(r'model_id\s*=\s*["\']([^"\']+)["\']', params_str)
            if model_id_match:
                model_id = model_id_match.group(1)
            
            streaming_match = re.search(r'streaming\s*=\s*(True|False)', params_str, re.IGNORECASE)
            if streaming_match:
                streaming = streaming_match.group(1).lower() == 'true'
            
            if model_id:
                model_tier = self._analyze_model_tier(model_id)
                
                finding = {
                    "type": "strands_bedrock_model_config",
                    "file": file_path,
                    "line": line_num,
                    "model_id": model_id,
                    "model_tier": model_tier["tier"],
                    "service": "bedrock"
                }
                
                if streaming is not None:
                    finding["streaming"] = streaming
                    finding["streaming_assessment"] = self._assess_streaming(streaming, file_path, content)
                
                finding["model_family"] = model_tier["model_family"]
                finding["tier_name"] = model_tier["tier_name"]
                finding["cost_consideration"] = self._get_tier_cost_consideration(model_tier["tier"], model_tier["model_family"], model_tier["tier_name"])
                finding["optimization"] = self._get_tier_optimization_guidance(model_tier["tier"], model_tier["model_family"], model_tier["tier_name"])
                
                findings.append(finding)
        
        return findings

    def _analyze_model_tier(self, model_id: str) -> Dict[str, Any]:
        """Analyze model tier and provide tier-based cost optimization context."""
        model_lower = model_id.lower()
        
        if "claude" in model_lower:
            # Extract version number for better analysis
            version_match = re.search(r'claude-(\d+(?:\.\d+)?)', model_lower)
            version = float(version_match.group(1)) if version_match else 3.0
            
            if "opus" in model_lower or "4-" in model_lower or "4." in model_lower or version >= 4.0:
                tier = "ultra-premium"
                tier_name = f"Opus/Claude {version}" if version >= 4.0 else "Opus/Claude 4"
            elif "sonnet" in model_lower:
                tier = "premium"
                # Include version in tier name for better context
                if version >= 3.7:
                    tier_name = f"Sonnet {version}"
                elif version >= 3.5:
                    tier_name = "Sonnet 3.5"
                else:
                    tier_name = "Sonnet"
            elif "haiku" in model_lower:
                tier = "cost-effective"
                # Include version in tier name
                if version >= 3.5:
                    tier_name = f"Haiku {version}"
                else:
                    tier_name = "Haiku"
            else:
                tier = "unknown"
                tier_name = "Unknown Claude tier"
            
            return {
                "tier": tier,
                "model_family": "anthropic-claude",
                "tier_name": tier_name,
                "version": version
            }
        
        elif "nova" in model_lower:
            if "premier" in model_lower:
                tier = "ultra-premium"
                tier_name = "Premier"
            elif "pro" in model_lower:
                tier = "premium"
                tier_name = "Pro"
            elif "lite" in model_lower:
                tier = "cost-effective"
                tier_name = "Lite"
            elif "micro" in model_lower:
                tier = "ultra-cost-effective"
                tier_name = "Micro"
            else:
                tier = "unknown"
                tier_name = "Unknown Nova tier"
            
            return {
                "tier": tier,
                "model_family": "amazon-nova",
                "tier_name": tier_name
            }
        
        elif "llama" in model_lower:
            if "70b" in model_lower or "405b" in model_lower:
                tier = "premium"
                tier_name = "Large (70B/405B)"
            else:
                tier = "cost-effective"
                tier_name = "Small (8B)"
            
            return {
                "tier": tier,
                "model_family": "meta-llama",
                "tier_name": tier_name
            }
        
        return {
            "tier": "unknown",
            "model_family": "unknown",
            "tier_name": "Unknown"
        }

    def _get_tier_cost_consideration(self, tier: str, family: str, tier_name: str) -> str:
        """Get cost consideration message based on tier."""
        if tier == "ultra-premium":
            return f"{family} {tier_name} tier detected. Use AWS MCP Server to check for newer models or assess if use case requires this tier."
        elif tier == "premium":
            return f"{family} {tier_name} tier detected. Use AWS MCP Server to check for newer models or assess if use case fits this tier."
        elif tier == "cost-effective":
            return f"{family} {tier_name} tier detected. Use AWS MCP Server to check for newer models or other optimizations."
        elif tier == "ultra-cost-effective":
            return f"{family} {tier_name} tier detected. Use AWS MCP Server to check for newer models or other optimizations."
        else:
            return f"{family} {tier_name} tier classification unknown. Use AWS MCP Server to verify current models and pricing."
    
    def _get_tier_optimization_guidance(self, tier: str, family: str, tier_name: str) -> Dict[str, Any]:
        """Get optimization guidance based on tier.
        
        NOTE: This provides general guidance only. Actual model recommendations
        require calling AWS MCP Server to get current model catalog.
        """
        return {
            "technique": "Model Tier Assessment",
            "current_tier": tier_name,
            "current_family": family,
            "use_case_tier_fit": {
                "ultra_premium": "Extremely complex reasoning, research-level analysis, maximum accuracy required",
                "premium": "Complex reasoning, analysis, creative writing, general-purpose production use",
                "cost_effective": "Structured data extraction, classification, simple reasoning, simple Q&A",
                "ultra_cost_effective": "Very simple tasks, high-volume low-complexity workloads"
            },
            "tier_assessment": f"Current tier: {tier_name} ({tier})",
            "next_steps": "Use AWS MCP Server to check for newer models in this tier or alternative tiers that might be more cost-effective"
        }

    def _assess_streaming(self, streaming: bool, file_path: str, content: str = "") -> Dict[str, Any]:
        """Assess if streaming configuration is appropriate for the context."""
        is_lambda = (
            "lambda" in file_path.lower() or 
            "handler" in file_path.lower() or
            "lambda_handler" in content or
            "def lambda_handler" in content
        )
        
        if streaming and is_lambda:
            return {
                "status": "⚠️ Streaming enabled in Lambda context",
                "issue": "Streaming extends Lambda execution time, increasing costs",
                "recommendation": "Disable streaming for batch/Lambda processing",
                "optimization": "Set streaming=False for faster synchronous responses",
                "potential_savings": "10-20% reduction in Lambda execution costs"
            }
        elif streaming:
            return {
                "status": "✅ Streaming enabled",
                "note": "Appropriate for real-time UI/API responses",
                "consideration": "Streaming is good for user experience but extends compute time"
            }
        else:
            return {
                "status": "✅ Synchronous mode",
                "note": "Faster response, lower compute costs",
                "appropriate_for": "Batch processing, Lambda functions, background jobs"
            }

    def _detect_models(self, content: str, file_path: str) -> List[Dict[str, Any]]:
        """Detect Bedrock model IDs using generic pattern matching."""
        findings = []
        
        # Use generic pattern to find ANY Bedrock model ID
        matches = re.finditer(self.BEDROCK_MODEL_ID_PATTERN, content, re.IGNORECASE)
        
        for match in matches:
            line_num = content[:match.start()].count('\n') + 1
            model_id = match.group(1)
            
            # Check if this is likely a false positive (validation message, comment, docstring)
            if self._is_likely_false_positive(content, match.start(), match.end()):
                continue
            
            # Parse the model ID to extract structured information
            parsed = self._parse_model_id(model_id)
            
            # Determine if this is a cross-region inference profile
            region_prefix = parsed.get("region_prefix")
            is_cross_region = region_prefix is not None
            cross_region_type = None
            
            if region_prefix == "global":
                cross_region_type = "global"
            elif region_prefix in ["us", "eu", "apac"]:
                cross_region_type = "geography-specific"
            
            finding = {
                "type": "bedrock_model_usage",
                "file": file_path,
                "line": line_num,
                "model_id": model_id,
                "service": "bedrock",
                "parsed": parsed,
                "is_cross_region": is_cross_region,
                "cross_region_type": cross_region_type,
                "description": f"Using {parsed['family']} {parsed['tier']} (model_id: {model_id})",
                "enrichment_required": {
                    "priority": "HIGH",
                    "why": "Must verify if this is the latest available model and compare pricing",
                    "mcp_server_required": "AWS MCP Server (mcp-proxy-for-aws)",
                    "step_1_get_available_models": {
                        "command": "aws bedrock list-foundation-models --region us-east-1",
                        "purpose": "Get REAL list of available Bedrock models",
                        "filter_results_by": {
                            "providerName": parsed['provider'].title(),  # e.g., "Anthropic"
                            "modelName_pattern": f"Contains '{parsed['tier']}'" if parsed['tier'] else "All models",
                            "modelLifecycle_status": "ACTIVE"
                        },
                        "example_filter": f"Filter for providerName='Anthropic' and modelName contains 'Sonnet' to find all Sonnet models"
                    },
                    "step_2_compare_models": {
                        "action": "Compare detected model_id against API results",
                        "check": [
                            "Is detected model in the list?",
                            "What is its modelLifecycle.status? (ACTIVE, LEGACY, etc.)",
                            "Are there newer models with later dates in modelId?",
                            "Example: claude-3-7-sonnet-20250219 vs claude-sonnet-4-20250514"
                        ]
                    },
                    "step_3_pricing_comparison": {
                        "condition": "Only if newer ACTIVE models exist",
                        "mcp_server_primary": "AWS MCP Server (mcp-proxy-for-aws)",
                        "action": "Try to get pricing for both models",
                        "known_limitation": "Pricing API often lags behind model releases by weeks/months",
                        "pricing_console_link": "https://aws.amazon.com/bedrock/pricing/",
                        "fallback_strategy": {
                            "if_pricing_unavailable": [
                                "1. State clearly: 'Pricing data not yet available in AWS Pricing API'",
                                "2. Provide pricing_console_link for manual verification",
                                "3. Search AWS MCP Server for 'Bedrock [model] pricing'",
                                "4. Provide tier-based guidance (same tier usually has similar pricing)"
                            ],
                            "tier_based_guidance": {
                                "same_tier": "Models in same tier (e.g., Sonnet → Sonnet) typically have similar pricing",
                                "newer_models": "Newer models often have better performance at same or lower cost",
                                "recommendation": "Check AWS Console for current pricing before making decision"
                            }
                        },
                        "example": "If Claude 3.7 Sonnet pricing unavailable, note that Sonnet-tier models typically cost $3/1M input tokens, but verify on AWS Console"
                    },
                    "critical_rules": [
                        "❌ NEVER recommend model changes without completing step 1",
                        "❌ NEVER assume a model is 'latest' without checking API",
                        "❌ NEVER recommend downgrading to older models",
                        "❌ NEVER make up pricing numbers - if unavailable, say so",
                        "✅ ALWAYS use actual modelName from API (e.g., 'Claude Sonnet 4', not 'Claude 4 (May 2025)')",
                        "✅ If pricing unavailable, provide tier-based guidance and AWS Console link",
                        "✅ If model is already the newest ACTIVE model, focus on OTHER optimizations (caching, streaming, etc.)"
                    ]
                }
            }
            
            # Add cross-region warning if applicable
            if is_cross_region:
                finding["cross_region_warning"] = {
                    "type": cross_region_type,
                    "message": f"Using {cross_region_type} cross-region inference profile. Be cautious with prompt caching (can increase costs).",
                    "recommendation": "Consider using single-region model ID if prompt caching is enabled"
                }
            
            findings.append(finding)

        return findings

    def _detect_api_calls(self, content: str, file_path: str) -> List[Dict[str, Any]]:
        """Detect Bedrock API call patterns."""
        findings = []

        for call_type, pattern in self.INVOKE_PATTERNS.items():
            matches = re.finditer(pattern, content)
            for match in matches:
                line_num = content[:match.start()].count('\n') + 1
                is_streaming = "stream" in call_type.lower()
                
                finding = {
                    "type": "bedrock_api_call",
                    "file": file_path,
                    "line": line_num,
                    "call_type": call_type,
                    "pattern": "streaming" if is_streaming else "synchronous",
                    "service": "bedrock"
                }
                
                # Add extra context for OpenAI Chat Completions API
                if call_type == "chat_completions_create":
                    finding["api_style"] = "openai_compatible"
                    finding["description"] = "OpenAI Chat Completions API call (Bedrock-compatible)"
                    
                    # Check if stream parameter is used
                    call_context = content[match.start():min(match.start() + 500, len(content))]
                    if re.search(r'stream\s*=\s*True', call_context, re.IGNORECASE):
                        finding["pattern"] = "streaming"
                    
                    # Check if base_url points to Bedrock
                    if self._is_bedrock_openai_client(content):
                        finding["bedrock_confirmed"] = True
                        finding["note"] = "Using OpenAI SDK with Bedrock Runtime endpoint"
                    else:
                        finding["bedrock_confirmed"] = False
                        finding["note"] = "OpenAI SDK detected - verify if using Bedrock endpoint"
                
                findings.append(finding)

        return findings
    
    def _is_bedrock_openai_client(self, content: str) -> bool:
        """Check if OpenAI client is configured to use Bedrock Runtime endpoint."""
        bedrock_endpoint_patterns = [
            r'base_url\s*=\s*["\'].*bedrock-runtime.*["\']',
            r'bedrock-runtime\.[a-z0-9-]+\.amazonaws\.com/openai',
            r'BEDROCK.*endpoint',
        ]
        return any(re.search(pattern, content, re.IGNORECASE) for pattern in bedrock_endpoint_patterns)

    def _detect_token_patterns(self, content: str, file_path: str) -> List[Dict[str, Any]]:
        """Detect potential token usage patterns."""
        findings = []

        max_tokens_pattern = r"max_tokens['\"]?\s*[:=]\s*(\d+)"
        matches = re.finditer(max_tokens_pattern, content, re.IGNORECASE)
        
        for match in matches:
            line_num = content[:match.start()].count('\n') + 1
            token_count = int(match.group(1))
            
            findings.append({
                "type": "token_configuration",
                "file": file_path,
                "line": line_num,
                "max_tokens": token_count,
                "service": "bedrock",
                "note": "High token limit" if token_count > 4000 else "Token limit configured"
            })

        return findings

    def _find_prompts(self, content: str, file_path: str) -> List[Dict[str, Any]]:
        """Find actual LLM prompts in code using AI analysis.
        
        Uses Bedrock AI to intelligently identify prompts in Python, TypeScript, JavaScript.
        Falls back to regex if AI analysis fails or AWS credentials not available.
        """
        # Try AI-powered detection first
        try:
            from ..utils.bedrock_helper import analyze_code_for_prompts
            ai_prompts = analyze_code_for_prompts(content, file_path)
            
            if ai_prompts:
                # Convert AI results to our format
                prompts = []
                for p in ai_prompts:
                    prompts.append({
                        "text": p.get("prompt_preview", ""),
                        "length": p.get("estimated_tokens", 0) * 4,  # rough estimate
                        "line": p.get("line", 0),
                        "start": 0,
                        "end": 0
                    })
                return prompts
        except Exception as e:
            # Fall back to regex if AI fails
            print(f"AI prompt detection failed, using regex fallback: {e}")
        
        # Regex fallback (simple patterns)
        prompts = []
        
        # Python: system_prompt=f"""..."""
        python_patterns = [
            r'(system_prompt|prompt|instruction|message)\s*=\s*f?"""(.*?)"""',
        ]
        
        # TypeScript/JavaScript: const comparisonPrompt = `...` or instruction: `...`
        typescript_patterns = [
            r'(?:const|let|var)\s+(\w*(?:prompt|instruction|message|system)\w*)\s*[=:]\s*`([^`]{200,})`',
            r'(instruction|prompt|message|system)\s*:\s*`([^`]{200,})`',  # Object property
        ]
        
        # Instruction keywords
        instruction_keywords = [
            r'\byou are\b', r'\bact as\b', r'\banalyze\b', r'\bevaluate\b',
        ]
        
        all_patterns = python_patterns + typescript_patterns
        
        for pattern in all_patterns:
            for match in re.finditer(pattern, content, re.DOTALL | re.IGNORECASE):
                line_num = content[:match.start()].count('\n') + 1
                
                if len(match.groups()) >= 2:
                    text = match.group(2)
                else:
                    text = match.group(1)
                
                has_instruction = any(re.search(kw, text, re.IGNORECASE) for kw in instruction_keywords)
                
                if has_instruction and len(text) >= 200:
                    prompts.append({
                        "text": text,
                        "length": len(text),
                        "line": line_num,
                        "start": match.start(),
                        "end": match.end()
                    })
        
        return prompts

    def _detect_nova_explicit_caching_opportunity(self, content: str, file_path: str) -> List[Dict[str, Any]]:
        """Detect Nova models with large prompts that lack explicit caching.
        
        NOTE: 150 token threshold is a heuristic for initial filtering.
        Actual Nova caching minimum is 1,000 tokens (see AWS docs).
        Enrichment layer should verify token count meets model-specific minimum.
        """
        findings = []
        
        nova_pattern = r'(us\.)?amazon\.nova-(micro|lite|pro|premier)'
        nova_matches = list(re.finditer(nova_pattern, content, re.IGNORECASE))
        
        if not nova_matches:
            return findings
        
        nova_model = nova_matches[0].group(0)
        nova_line = content[:nova_matches[0].start()].count('\n') + 1
        
        has_cache_point = bool(re.search(r'cachePoint', content))
        has_cache_control = bool(re.search(r'cache_control', content))
        
        if has_cache_point or has_cache_control:
            findings.append({
                'type': 'nova_explicit_caching_enabled',
                'file': file_path,
                'line': nova_line,
                'model_id': nova_model,
                'service': 'bedrock',
                'description': f'✅ Explicit prompt caching enabled for {nova_model}',
                'cost_consideration': 'Great! You\'re using explicit caching to get 90% discount on cached tokens.',
                'best_practices': [
                    'Monitor cache hit rate in CloudWatch (CacheReadInputTokens)',
                    'Ensure static content is ≥1,000 tokens (Nova minimum)',
                    'Keep cache TTL in mind (5 minutes)',
                    'Batch similar requests to maximize cache hits'
                ]
            })
            return findings
        
        large_prompts = self._find_prompts(content, file_path)
        
        if not large_prompts:
            return findings
        
        for prompt_info in large_prompts:
            estimated_tokens = prompt_info['length'] // 4
            
            # NOTE: 150 is a heuristic threshold for initial filtering.
            # Actual Nova minimum is 1,000 tokens. Enrichment layer verifies this.
            if estimated_tokens < 150:
                continue
            
            monthly_requests = 1000
            cost_without_caching = (monthly_requests * estimated_tokens * 0.00006) / 1000
            cost_with_caching = (estimated_tokens * 0.00009 / 1000) + \
                               ((monthly_requests - 1) * estimated_tokens * 0.000006 / 1000)
            monthly_savings = cost_without_caching - cost_with_caching
            
            finding = {
                'type': 'nova_explicit_caching_opportunity',
                'file': file_path,
                'line': prompt_info['line'],
                'model_id': nova_model,
                'estimated_static_tokens': estimated_tokens,
                'service': 'bedrock',
                'description': f'Nova model with large prompt (~{estimated_tokens} tokens) without explicit caching',
                'cost_consideration': f'This prompt has ~{estimated_tokens} tokens that are repeated for each request. Without explicit caching, you\'re paying full price ($0.00006 per 1K tokens). With explicit caching, you\'d pay 90% less ($0.000006 per 1K tokens) after the first request.',
                'potential_savings': f'${monthly_savings:.2f}/month (90% reduction)',
                'documentation': 'https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-caching.html',
                'enrichment_required': {
                    'priority': 'HIGH',
                    'why': 'Must verify if estimated tokens meet Nova minimum for prompt caching',
                    'action': 'Check if estimated_static_tokens >= 1,000 (Nova minimum)',
                    'nova_caching_requirements': {
                        'minimum_tokens': '1,000 tokens',
                        'maximum_tokens': '20,000 tokens',
                        'cache_ttl': '5 minutes',
                        'documentation': 'https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-caching.html'
                    },
                    'validation': f'Estimated {estimated_tokens} tokens - {"✅ Meets minimum" if estimated_tokens >= 1000 else "❌ Below 1,000 token minimum - caching will NOT work"}',
                    'recommendation': 'If below minimum, do NOT suggest prompt caching for this prompt'
                }
            }
            
            findings.append(finding)
        
        return findings

    def _detect_caching_cross_region_antipattern(self, content: str, file_path: str) -> List[Dict[str, Any]]:
        """Detect prompt caching used with cross-region inference profiles.
        
        Uses the generic model detection to find cross-region model IDs,
        then checks if caching is enabled. Distinguishes between static and dynamic prompts.
        
        Static prompts + cross-region + caching = OK (same cache across regions)
        Dynamic prompts + cross-region + caching = RISK (different cache per request)
        """
        findings = []
        
        # Check if caching is enabled in this file
        # Support both snake_case (Python) and camelCase (TypeScript/JavaScript)
        has_cache_point = bool(re.search(r'cachePoint', content))
        has_cache_control = bool(re.search(r'cache(?:_control|Control)', content))  # Matches cache_control or cacheControl
        
        if not (has_cache_point or has_cache_control):
            return findings  # No caching, no anti-pattern
        
        # Analyze if prompts are static or dynamic
        prompt_analysis = self._analyze_prompt_staticness(content)
        
        # Use generic model detection to find ALL models with region prefixes
        model_findings = self._detect_models(content, file_path)
        
        for model_finding in model_findings:
            parsed = model_finding.get("parsed", {})
            region_prefix = parsed.get("region_prefix")
            
            if not region_prefix:
                continue  # No region prefix = single-region model, no issue
            
            model_id = model_finding["model_id"]
            line_num = model_finding["line"]
            
            if region_prefix == "global":
                # Determine severity based on prompt staticness
                if prompt_analysis['is_static']:
                    # Static prompts are OK with cross-region caching
                    severity = 'info'
                    description = f'ℹ️ INFO: Prompt caching with global inference profile ({model_id}) - Static prompts detected'
                    problem = 'Global inference profiles route to multiple regions, but since your prompts appear to be static (no dynamic variables), the same cache content will be used across regions. This is generally OK.'
                    recommendation = f'Static prompts + cross-region + caching is acceptable. Monitor cache hit rates to ensure effectiveness.'
                else:
                    # Dynamic prompts are HIGH RISK
                    severity = 'high'
                    description = f'⚠️ HIGH RISK: Prompt caching with global inference profile ({model_id}) - Dynamic prompts detected'
                    problem = 'Global inference profiles route requests to ANY commercial AWS region. Each region maintains separate caches. With dynamic prompts (containing variables), different requests may create different cache entries in multiple regions (10-20+), potentially INCREASING costs by 50%+ instead of reducing them.'
                    recommendation = f'Use single-region model ID (e.g., {parsed["provider"]}.{parsed["family"]}-...) instead of global profile, or disable caching for dynamic prompts'
                
                findings.append({
                    'type': 'caching_cross_region_antipattern',
                    'severity': severity,
                    'file': file_path,
                    'line': line_num,
                    'model_id': model_id,
                    'profile_type': 'global',
                    'region_prefix': region_prefix,
                    'service': 'bedrock',
                    'parsed': parsed,
                    'prompt_analysis': prompt_analysis,
                    'description': description,
                    'issue': 'Prompt caching with global cross-region inference profile',
                    'problem': problem,
                    'recommendation': recommendation,
                    'documentation': 'https://docs.aws.amazon.com/bedrock/latest/userguide/cross-region-inference.html'
                })
            elif region_prefix in ["us", "eu", "apac"]:
                # Determine severity based on prompt staticness
                if prompt_analysis['is_static']:
                    # Static prompts are OK with cross-region caching
                    severity = 'info'
                    description = f'ℹ️ INFO: Prompt caching with {region_prefix.upper()} geo-specific inference profile ({model_id}) - Static prompts detected'
                    problem = f'{region_prefix.upper()} profiles route to 3-5 regions, but since your prompts appear to be static (no dynamic variables), the same cache content will be used across regions. This is generally OK.'
                    recommendation = f'Static prompts + cross-region + caching is acceptable. Monitor cache hit rates to ensure effectiveness.'
                else:
                    # Dynamic prompts are MEDIUM RISK
                    severity = 'medium'
                    description = f'⚠️ MEDIUM RISK: Prompt caching with {region_prefix.upper()} geo-specific inference profile ({model_id}) - Dynamic prompts detected'
                    problem = f'{region_prefix.upper()} profiles route to 3-5 regions. With dynamic prompts (containing variables), cache writes occur in multiple regions with lower hit rates.'
                    recommendation = f'Use single-region model ID (e.g., {parsed["provider"]}.{parsed["family"]}-...) for consistent caching, or ensure very high traffic (>1000 req/hour)'
                
                findings.append({
                    'type': 'caching_cross_region_antipattern',
                    'severity': severity,
                    'file': file_path,
                    'line': line_num,
                    'model_id': model_id,
                    'profile_type': 'geography-specific',
                    'region_prefix': region_prefix,
                    'service': 'bedrock',
                    'parsed': parsed,
                    'prompt_analysis': prompt_analysis,
                    'description': description,
                    'issue': f'Prompt caching with {region_prefix.upper()} geography-specific cross-region inference profile',
                    'problem': problem,
                    'recommendation': recommendation,
                    'documentation': 'https://docs.aws.amazon.com/bedrock/latest/userguide/cross-region-inference.html'
                })
        
        return findings

    def _detect_dynamic_system_prompts(self, content: str, file_path: str) -> List[Dict[str, Any]]:
        """Detect dynamic variables in system prompts that should be moved to user messages.
        
        Dynamic variables in system prompts prevent effective prompt caching because
        the system prompt changes on every request.
        """
        findings = []
        
        # Analyze system prompt staticness
        analysis = self._analyze_system_prompt_staticness(content)
        
        # Only report if we found system prompts and they have dynamic variables
        if analysis.get('system_prompts_found', 0) > 0 and not analysis['is_static']:
            dynamic_vars = analysis.get('dynamic_variables', [])
            
            if dynamic_vars:
                # Find the line number of the system_prompt
                line_num = None
                for i, line in enumerate(content.split('\n'), 1):
                    if 'system_prompt' in line and '=' in line:
                        line_num = i
                        break
                
                finding = {
                    'type': 'dynamic_system_prompt',
                    'severity': 'high',
                    'file': file_path,
                    'line': line_num,
                    'service': 'bedrock',
                    'dynamic_variables': dynamic_vars,
                    'system_prompts_found': analysis['system_prompts_found'],
                    'description': f'🚨 HIGH PRIORITY: System prompt contains dynamic variables: {", ".join(dynamic_vars)}',
                    'issue': 'Dynamic variables in system_prompt prevent effective prompt caching',
                    'problem': f'The system_prompt contains {len(dynamic_vars)} dynamic variable(s) that change per request. This prevents prompt caching from working effectively, as each unique system prompt creates a new cache entry.',
                    'impact': 'Cannot use prompt caching (90% cost savings lost). With cross-region models, this also causes cache fragmentation.',
                    'recommendation': 'Move dynamic variables to user messages instead of system_prompt',
                    'fix_example': self._generate_system_prompt_fix_example(dynamic_vars),
                    'estimated_savings': 'Up to 90% on system prompt tokens (~500-1000 tokens) if caching is enabled after fix'
                }
                
                findings.append(finding)
        
        return findings
    
    def _generate_system_prompt_fix_example(self, dynamic_vars: List[str]) -> Dict[str, str]:
        """Generate before/after example for fixing dynamic system prompts."""
        # Create example variable names
        var_examples = ', '.join(dynamic_vars[:3])  # Show up to 3 variables
        
        return {
            'before': f'''# ❌ BAD: Dynamic variables in system_prompt
system_prompt=f"""You are an assistant.
Process this data: {{{dynamic_vars[0]}}}
"""

query = "Do the task"
response = agent(query)''',
            'after': f'''# ✅ GOOD: Variables in user message
system_prompt="""You are an assistant.
Process the data provided in the user message.
"""

query = f"Do the task with this data: {{{dynamic_vars[0]}}}"
response = agent(query)''',
            'benefit': 'System prompt is now static and can be cached (90% discount on cached tokens)'
        }
    
    def _analyze_system_prompt_staticness(self, content: str) -> Dict[str, Any]:
        """Analyze if system prompts specifically are static or dynamic.
        
        This is more precise than file-level analysis - it extracts system_prompt
        from Agent() calls and analyzes only those prompts.
        
        Returns:
            Dict with:
            - is_static: bool
            - confidence: str (high/medium/low)
            - indicators: list of what was detected
            - system_prompts_found: int
            - dynamic_variables: list of variable names found in system prompts
        """
        indicators = []
        dynamic_variables = []
        system_prompts_found = 0
        
        # Extract system_prompt from Agent() calls
        # Support both triple quotes, single quotes, and Strands parentheses concatenation
        patterns = [
            r'system_prompt\s*=\s*(f""".*?""")',  # f"""..."""
            r'system_prompt\s*=\s*(f\'\'\'.*?\'\'\')',  # f'''...'''
            r'system_prompt\s*=\s*(f"[^"]*")',  # f"..."
            r'system_prompt\s*=\s*(f\'[^\']*\')',  # f'...'
        ]
        
        for pattern in patterns:
            agent_matches = re.finditer(pattern, content, re.DOTALL)
            
            for match in agent_matches:
                system_prompts_found += 1
                prompt_text = match.group(1)
                
                # Extract variables from f-string
                var_pattern = r'\{([^}]+)\}'
                variables = re.findall(var_pattern, prompt_text)
                
                # Filter to only valid Python variable names
                real_variables = []
                for var in variables:
                    var_clean = var.strip()
                    # Must be a valid Python identifier (letters, numbers, underscore, starts with letter/underscore)
                    if re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', var_clean):
                        real_variables.append(var_clean)
                
                if real_variables:
                    dynamic_variables.extend(real_variables)
                    indicators.append(f"f-string in system_prompt with variables: {', '.join(real_variables)}")
        
        # Also check for Strands-style parentheses concatenation with f-strings
        # Pattern: system_prompt=(\n    f"..." \n    "..." \n)
        paren_pattern = r'system_prompt\s*=\s*\(((?:[^()]*|\([^()]*\))*)\)'
        paren_matches = re.finditer(paren_pattern, content, re.DOTALL)
        
        for match in paren_matches:
            system_prompts_found += 1
            paren_content = match.group(1)
            
            # Find all f-strings within the parentheses
            fstring_pattern = r'f["\']([^"\']*)["\']'
            fstrings = re.findall(fstring_pattern, paren_content)
            
            for fstring in fstrings:
                # Extract variables from each f-string
                var_pattern = r'\{([^}]+)\}'
                variables = re.findall(var_pattern, fstring)
                
                # Filter to only valid Python variable names
                for var in variables:
                    var_clean = var.strip()
                    if re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', var_clean):
                        if var_clean not in dynamic_variables:
                            dynamic_variables.append(var_clean)
            
            if dynamic_variables and not indicators:
                indicators.append(f"f-string in system_prompt with variables: {', '.join(dynamic_variables)}")
        
        # Also check for .format() on system_prompt (non-f-string)
        format_pattern = r'system_prompt\s*=\s*["\'].*?["\']\.format\('
        if re.search(format_pattern, content, re.DOTALL):
            indicators.append("system_prompt uses .format()")
        
        # If no Agent() found, fall back to file-level analysis
        if system_prompts_found == 0:
            return self._analyze_prompt_staticness_file_level(content)
        
        # Determine if static or dynamic
        is_static = len(indicators) == 0
        
        # Determine confidence
        if len(indicators) == 0:
            confidence = "high"  # No dynamic indicators in system prompts
        elif len(dynamic_variables) > 0:
            confidence = "high"  # Found specific variables, definitely dynamic
        else:
            confidence = "medium"
        
        return {
            "is_static": is_static,
            "confidence": confidence,
            "indicators": indicators if indicators else ["No dynamic variables in system_prompt"],
            "system_prompts_found": system_prompts_found,
            "dynamic_variables": dynamic_variables,
            "note": "System prompts with dynamic variables should be refactored to pass data via user messages instead."
        }
    
    def _analyze_prompt_staticness_file_level(self, content: str) -> Dict[str, Any]:
        """Analyze file-level prompt staticness (fallback method).
        
        Used when no Agent() system_prompt is found.
        """
        indicators = []
        
        # Check for f-strings with variables
        fstring_vars = re.findall(r'f["\'].*?\{[^}]+\}.*?["\']', content, re.DOTALL)
        if fstring_vars:
            indicators.append(f"f-string variables ({len(fstring_vars)} found)")
        
        # Check for .format() calls
        format_calls = re.findall(r'\.format\(', content)
        if format_calls:
            indicators.append(f".format() calls ({len(format_calls)} found)")
        
        # Check for string concatenation with variables
        concat_patterns = re.findall(r'["\'].*?["\']\\s*\+\\s*[a-zA-Z_]', content)
        if concat_patterns:
            indicators.append(f"string concatenation ({len(concat_patterns)} found)")
        
        # Check for % formatting
        percent_format = re.findall(r'%\s*\(', content)
        if percent_format:
            indicators.append(f"% formatting ({len(percent_format)} found)")
        
        # Determine if static or dynamic
        is_static = len(indicators) == 0
        
        # Determine confidence
        if len(indicators) == 0:
            confidence = "high"  # No dynamic indicators found
        elif len(indicators) <= 2:
            confidence = "medium"  # Few dynamic indicators
        else:
            confidence = "high"  # Many dynamic indicators, definitely dynamic
        
        return {
            "is_static": is_static,
            "confidence": confidence,
            "indicators": indicators if indicators else ["No dynamic prompt indicators found"],
            "system_prompts_found": 0,
            "dynamic_variables": [],
            "note": "File-level analysis (no Agent system_prompt found). Static prompts are safe with cross-region caching."
        }
    
    def _analyze_prompt_staticness(self, content: str) -> Dict[str, Any]:
        """Analyze if prompts in the code are static or dynamic.
        
        Delegates to system prompt-specific analysis for better accuracy.
        """
        return self._analyze_system_prompt_staticness(content)
    
    def _detect_prompt_routing(self, content: str, file_path: str, existing_findings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Detect prompt routing usage and opportunities.
        
        Detects:
        1. Existing prompt routing (positive feedback)
        2. Opportunities for routing when:
           - Multiple models from same family are used
           - Mixed complexity prompts use same expensive model
           - Premium/ultra-premium tier models are used
        
        Args:
            content: File content
            file_path: Path to file
            existing_findings: Already detected findings (to analyze model usage)
        
        Returns:
            List of routing-related findings
        """
        findings = []
        
        # Check for existing prompt routing usage
        router_arn_pattern = r'arn:aws:bedrock:[a-z0-9\-]+:\d+:prompt-router/[a-z0-9]+'
        router_matches = list(re.finditer(router_arn_pattern, content, re.IGNORECASE))
        
        if router_matches:
            # Positive feedback: routing is already enabled
            for match in router_matches:
                line_num = content[:match.start()].count('\n') + 1
                router_arn = match.group(0)
                
                findings.append({
                    'type': 'prompt_routing_detected',
                    'file': file_path,
                    'line': line_num,
                    'router_arn': router_arn,
                    'service': 'bedrock',
                    'description': '✅ Prompt Routing is enabled',
                    'cost_consideration': 'Prompt Routing automatically optimizes cost by routing simple prompts to cheaper models and complex prompts to more capable models.',
                    'best_practices': [
                        'Monitor routing decisions via CloudWatch metrics (PromptRouterInvocations)',
                        'Review which prompts route to which models using CloudWatch Logs',
                        'Adjust routing criteria in AWS Console if needed',
                        'Track cost savings by comparing against single-model baseline'
                    ],
                    'monitoring': 'Use CloudWatch to monitor routing effectiveness and cost savings',
                    'documentation': 'https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-routing.html'
                })
            
            # If routing is already enabled, don't suggest opportunities
            return findings
        
        # Analyze model usage from existing findings
        model_findings = [f for f in existing_findings if f.get('type') == 'bedrock_model_usage']
        
        if not model_findings:
            return findings
        
        # Extract models and their tiers
        models_by_family = {}
        premium_models = []
        
        for finding in model_findings:
            parsed = finding.get('parsed', {})
            family = parsed.get('family')
            tier = self._analyze_model_tier(finding['model_id'])
            
            if family:
                if family not in models_by_family:
                    models_by_family[family] = []
                models_by_family[family].append({
                    'model_id': finding['model_id'],
                    'tier': tier['tier'],
                    'tier_name': tier['tier_name'],
                    'line': finding['line']
                })
            
            # Track premium/ultra-premium models
            if tier['tier'] in ['premium', 'ultra-premium']:
                premium_models.append({
                    'model_id': finding['model_id'],
                    'tier': tier['tier'],
                    'tier_name': tier['tier_name'],
                    'family': tier['model_family'],
                    'line': finding['line']
                })
        
        # Opportunity 1: Multiple DIFFERENT models from same family
        for family, models in models_by_family.items():
            # Get unique model IDs
            unique_model_ids = list(set(m['model_id'] for m in models))
            
            if len(unique_model_ids) > 1:
                # Multiple DIFFERENT models from same family - routing opportunity
                tier_names = [m['tier_name'] for m in models]
                model_ids = [m['model_id'] for m in models]
                
                findings.append({
                    'type': 'prompt_routing_opportunity',
                    'subtype': 'multiple_models_same_family',
                    'file': file_path,
                    'line': models[0]['line'],
                    'service': 'bedrock',
                    'model_family': family,
                    'models_detected': model_ids,
                    'tiers_detected': tier_names,
                    'description': f'Multiple {family} models detected: {", ".join(tier_names)}',
                    'issue': 'Manual model selection logic detected',
                    'cost_consideration': f'Using multiple {family} models suggests conditional logic for model selection. Prompt Routing can automate this and optimize costs by automatically selecting the best model for each request.',
                    'optimization': {
                        'technique': 'Bedrock Prompt Routing',
                        'benefit': 'Automatic model selection based on prompt complexity',
                        'potential_savings': '30-50% by routing simple prompts to cheaper models',
                        'how_it_works': [
                            'Create a prompt router in AWS Console',
                            'Configure quality vs cost trade-off',
                            'Replace model IDs with router ARN',
                            'Router automatically selects optimal model per request'
                        ]
                    },
                    'documentation': 'https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-routing.html',
                    'action': 'Consider replacing manual model selection with Bedrock Prompt Routing'
                })
        
        # Opportunity 2: Analyze prompt complexity variation
        complexity_findings = self._analyze_prompt_complexity_variation(content, file_path)
        if complexity_findings:
            findings.extend(complexity_findings)
        
        # Opportunity 3: Premium/ultra-premium tier usage
        if premium_models and not models_by_family:
            # Using premium models but no multiple models detected
            # Suggest routing if there's complexity variation
            for model_info in premium_models:
                # Check if there are multiple prompts with varying complexity
                large_strings = self._find_prompts(content, file_path)
                
                if len(large_strings) >= 2:
                    # Analyze complexity of prompts
                    complexities = []
                    for string_info in large_strings:
                        complexity = self._estimate_prompt_complexity(string_info['text'])
                        complexities.append(complexity)
                    
                    if complexities:
                        min_complexity = min(complexities)
                        max_complexity = max(complexities)
                        complexity_range = max_complexity - min_complexity
                        
                        # If significant complexity variation, suggest routing
                        if complexity_range >= 2:
                            findings.append({
                                'type': 'prompt_routing_opportunity',
                                'subtype': 'premium_tier_with_mixed_complexity',
                                'file': file_path,
                                'line': model_info['line'],
                                'service': 'bedrock',
                                'current_model': model_info['model_id'],
                                'current_tier': model_info['tier_name'],
                                'tier_level': model_info['tier'],
                                'complexity_variation': {
                                    'min': min_complexity,
                                    'max': max_complexity,
                                    'range': complexity_range
                                },
                                'description': f'Using {model_info["tier_name"]} ({model_info["tier"]}) for all requests, but prompts vary in complexity',
                                'issue': f'Paying {model_info["tier"]} pricing for all prompts, including simple ones',
                                'cost_consideration': f'Using {model_info["tier_name"]} for all requests, but detected prompts with complexity range {min_complexity}-{max_complexity}. Prompt Routing could automatically use cheaper models (Haiku/Lite/Micro) for simpler prompts while keeping {model_info["tier_name"]} for complex ones.',
                                'optimization': {
                                    'technique': 'Bedrock Prompt Routing',
                                    'current_cost': f'All prompts use {model_info["tier_name"]} pricing',
                                    'with_routing': 'Simple prompts → cheaper models, Complex prompts → current model',
                                    'potential_savings': '50%+ for simple prompts routed to cheaper models',
                                    'setup_steps': [
                                        'Create prompt router in AWS Bedrock Console',
                                        'Configure routing criteria (balance quality vs cost)',
                                        'Replace model ID with router ARN in code',
                                        'Monitor routing decisions via CloudWatch'
                                    ]
                                },
                                'documentation': 'https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-routing.html',
                                'action': 'Consider Bedrock Prompt Routing to automatically optimize model selection'
                            })
                            break  # Only report once per file
        
        return findings
    
    def _analyze_prompt_complexity_variation(self, content: str, file_path: str) -> List[Dict[str, Any]]:
        """Analyze if prompts in the file have varying complexity levels.
        
        Returns routing opportunity if significant variation is detected.
        """
        findings = []
        
        # Find all prompts
        large_strings = self._find_prompts(content, file_path)
        
        if len(large_strings) < 2:
            return findings  # Need at least 2 prompts to compare
        
        # Estimate complexity for each prompt
        complexities = []
        for string_info in large_strings:
            complexity = self._estimate_prompt_complexity(string_info['text'])
            complexities.append({
                'complexity': complexity,
                'line': string_info['line'],
                'length': string_info['length']
            })
        
        if not complexities:
            return findings
        
        # Calculate complexity range
        complexity_values = [c['complexity'] for c in complexities]
        min_complexity = min(complexity_values)
        max_complexity = max(complexity_values)
        complexity_range = max_complexity - min_complexity
        
        # If significant variation (range >= 2), suggest routing
        if complexity_range >= 2:
            # Find ALL models being used
            model_pattern = self.BEDROCK_MODEL_ID_PATTERN
            model_matches = list(re.finditer(model_pattern, content, re.IGNORECASE))
            
            if model_matches:
                # Use the first model found
                model_id = model_matches[0].group(1)
                tier_info = self._analyze_model_tier(model_id)
                
                # Only suggest if using premium or ultra-premium tier
                if tier_info['tier'] in ['premium', 'ultra-premium']:
                    # Extract a simplified model name for display
                    parsed = self._parse_model_id(model_id)
                    display_name = f"{parsed['family']}-{parsed['tier']}" if parsed['tier'] else parsed['family']
                    
                    findings.append({
                        'type': 'prompt_routing_opportunity',
                        'subtype': 'mixed_complexity_prompts',
                        'file': file_path,
                        'line': complexities[0]['line'],
                        'service': 'bedrock',
                        'current_model': model_id,
                        'current_model_display': display_name,
                        'current_tier': tier_info['tier_name'],
                        'complexity_variation': {
                            'min': min_complexity,
                            'max': max_complexity,
                            'range': complexity_range,
                            'prompt_count': len(complexities)
                        },
                        'description': f'Mixed complexity prompts detected (range: {min_complexity}-{max_complexity}) using {tier_info["tier_name"]}',
                        'cost_consideration': f'Using {tier_info["tier_name"]} for all {len(complexities)} prompts, but complexity varies significantly. Prompt Routing could save 50%+ by routing simple prompts to cheaper models.',
                        'potential_savings': '50%+ for simple prompts routed to cheaper models',
                        'optimization': {
                            'technique': 'Bedrock Prompt Routing',
                            'recommendation': 'Let Bedrock automatically route based on complexity',
                            'documentation': 'https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-routing.html'
                        }
                    })
        
        return findings
    
    def _estimate_prompt_complexity(self, prompt_text: str) -> int:
        """Estimate prompt complexity on a scale of 1-5.
        
        Complexity indicators:
        - 1: Simple (summarize, list, extract)
        - 2: Moderate (explain, describe)
        - 3: Complex (analyze, compare)
        - 4: Very complex (evaluate, reason, multi-step)
        - 5: Extremely complex (comprehensive analysis, research-level)
        
        Args:
            prompt_text: The prompt text to analyze
        
        Returns:
            Complexity score (1-5)
        """
        text_lower = prompt_text.lower()
        
        # Complexity indicators with weights
        complexity_indicators = {
            # Level 1: Simple
            'simple': (1, [r'\bsummarize\b', r'\blist\b', r'\bextract\b', r'\bbrief\b', r'\bconcise\b']),
            # Level 2: Moderate
            'moderate': (2, [r'\bexplain\b', r'\bdescribe\b', r'\boutline\b']),
            # Level 3: Complex
            'complex': (3, [r'\banalyze\b', r'\bcompare\b', r'\bassess\b', r'\bevaluate\b']),
            # Level 4: Very complex
            'very_complex': (4, [
                r'\bdetailed\b.*\banalysis\b',
                r'\bmultiple perspectives\b',
                r'\bcomprehensive\b',
                r'\bsystematically\b',
                r'\breasoning\b',
                r'\bthink step by step\b'
            ]),
            # Level 5: Extremely complex
            'extremely_complex': (5, [
                r'\bextremely detailed\b',
                r'\bresearch-level\b',
                r'\bin great detail\b',
                r'\bcarefully.*evaluate.*multiple\b',
                r'\bconsider edge cases\b'
            ])
        }
        
        # Count matches for each level
        level_scores = []
        
        for level_name, (score, patterns) in complexity_indicators.items():
            for pattern in patterns:
                if re.search(pattern, text_lower):
                    level_scores.append(score)
        
        if not level_scores:
            # Default to moderate if no indicators found
            return 2
        
        # Return the highest complexity level detected
        return max(level_scores)

    def _detect_service_tier(self, content: str, file_path: str) -> List[Dict[str, Any]]:
        """Detect Bedrock Service Tier configuration (Reserved, Priority, Standard, Flex).
        
        Service tiers optimize performance and cost:
        - Reserved: Pre-reserved capacity for mission-critical apps, 99.5% uptime, fixed monthly pricing
        - Priority: Fastest response times, price premium
        - Standard/Default: Consistent performance, standard pricing
        - Flex: Cost-effective processing, pricing discount
        
        Also detects MISSING service_tier (optimization opportunity).
        
        Documentation: https://docs.aws.amazon.com/bedrock/latest/userguide/service-tiers-inference.html
        """
        findings = []
        
        # Pattern to detect service_tier parameter
        # Matches: "service_tier": "priority", service_tier="flex", 'service_tier': 'default', etc.
        service_tier_patterns = [
            # Python/JSON style: "service_tier": "value" or 'service_tier': 'value'
            r'["\']service_tier["\']\s*:\s*["\'](\w+)["\']',
            # Python keyword arg: service_tier="value" or service_tier='value'
            r'service_tier\s*=\s*["\'](\w+)["\']',
            # TypeScript/JavaScript: serviceTier: "value"
            r'serviceTier\s*:\s*["\'](\w+)["\']',
        ]
        
        # Track lines where service_tier is explicitly set
        lines_with_service_tier = set()
        
        for pattern in service_tier_patterns:
            matches = re.finditer(pattern, content, re.IGNORECASE)
            for match in matches:
                line_num = content[:match.start()].count('\n') + 1
                lines_with_service_tier.add(line_num)
                tier_value = match.group(1).lower()
                
                # Determine tier type and provide factual context
                tier_info = self._get_service_tier_info(tier_value)
                
                finding = {
                    "type": "bedrock_service_tier",
                    "file": file_path,
                    "line": line_num,
                    "service_tier": tier_value,
                    "tier_category": tier_info["category"],
                    "service": "bedrock",
                    "description": tier_info["description"],
                    "pricing_model": tier_info["pricing_model"],
                    "typical_use_cases": tier_info["typical_use_cases"],
                    "documentation": "https://docs.aws.amazon.com/bedrock/latest/userguide/service-tiers-inference.html"
                }
                
                findings.append(finding)
        
        # Detect API calls WITHOUT service_tier (optimization opportunity)
        # Reuse the same INVOKE_PATTERNS defined at class level for consistency
        for api_name, pattern in self.INVOKE_PATTERNS.items():
            matches = re.finditer(pattern, content)
            for match in matches:
                line_num = content[:match.start()].count('\n') + 1
                
                # Check if this API call already has service_tier configured
                # Find the closing parenthesis for this specific call to limit search scope
                call_start = match.start()
                call_end = self._find_matching_paren(content, match.end() - 1)
                
                if call_end == -1:
                    # Couldn't find matching paren, use limited context
                    call_context = content[call_start:min(call_start + 300, len(content))]
                else:
                    # Use only the content within this API call
                    call_context = content[call_start:call_end + 1]
                
                has_service_tier = any(
                    re.search(p, call_context, re.IGNORECASE) 
                    for p in service_tier_patterns
                )
                
                if not has_service_tier:
                    # API call without service_tier - optimization opportunity
                    findings.append({
                        "type": "bedrock_service_tier_missing",
                        "file": file_path,
                        "line": line_num,
                        "api_call": api_name,
                        "service_tier": "default (implicit)",
                        "service": "bedrock",
                        "optimization_opportunity": True,
                        "description": f"{api_name} call without service_tier parameter",
                        "issue": "Using default (Standard) tier without considering cost optimization",
                        "recommendation": "Consider adding service_tier parameter based on workload requirements. Flex tier offers cost savings for non-latency-sensitive workloads (batch processing, content summarization, model evaluations).",
                        "cost_consideration": "Flex tier provides pricing discount compared to Standard tier for workloads that can tolerate slightly longer response times",
                        "next_steps": [
                            "Assess if workload is latency-sensitive (real-time chat, customer-facing) or can tolerate delays (batch, background)",
                            "For non-latency-sensitive: Add service_tier='flex' for cost savings",
                            "For latency-sensitive: Keep default or use service_tier='priority'",
                            "Use AWS MCP Server to compare Standard vs Flex pricing for your model"
                        ],
                        "documentation": "https://docs.aws.amazon.com/bedrock/latest/userguide/service-tiers-inference.html"
                    })
        
        return findings
    
    def _find_matching_paren(self, content: str, start_pos: int) -> int:
        """Find the matching closing parenthesis for an opening parenthesis.
        
        Args:
            content: The full content string
            start_pos: Position of the opening parenthesis
            
        Returns:
            Position of matching closing paren, or -1 if not found
        """
        if start_pos >= len(content) or content[start_pos] != '(':
            return -1
        
        depth = 1
        pos = start_pos + 1
        
        while pos < len(content) and depth > 0:
            if content[pos] == '(':
                depth += 1
            elif content[pos] == ')':
                depth -= 1
                if depth == 0:
                    return pos
            # Skip strings to avoid counting parens inside strings
            elif content[pos] in ['"', "'"]:
                quote = content[pos]
                pos += 1
                while pos < len(content):
                    if content[pos] == quote and content[pos-1] != '\\':
                        break
                    pos += 1
            pos += 1
        
        return -1
    
    def _get_service_tier_info(self, tier_value: str) -> Dict[str, Any]:
        """Get factual information about a specific service tier.
        
        Returns facts from AWS documentation, not recommendations.
        """
        # Facts from: https://docs.aws.amazon.com/bedrock/latest/userguide/service-tiers-inference.html
        tier_info = {
            "reserved": {
                "category": "ultra-premium",
                "description": "Reserved tier configured",
                "pricing_model": "Fixed price per 1K tokens-per-minute, billed monthly (1 or 3 month duration)",
                "typical_use_cases": "Mission-critical applications with zero downtime requirements, 99.5% uptime target",
                "capacity_model": "Pre-reserved prioritized compute capacity with automatic overflow to Standard tier",
                "access_requirement": "Contact AWS account team for access"
            },
            "priority": {
                "category": "premium",
                "description": "Priority tier configured",
                "pricing_model": "Price premium over standard on-demand pricing",
                "typical_use_cases": "Mission-critical applications, customer-facing chatbots, real-time translation"
            },
            "default": {
                "category": "standard",
                "description": "Standard tier configured (default)",
                "pricing_model": "Standard on-demand pricing",
                "typical_use_cases": "Content generation, text analysis, routine document processing"
            },
            "standard": {
                "category": "standard",
                "description": "Standard tier configured",
                "pricing_model": "Standard on-demand pricing",
                "typical_use_cases": "Content generation, text analysis, routine document processing"
            },
            "flex": {
                "category": "cost-optimized",
                "description": "Flex tier configured",
                "pricing_model": "Pricing discount compared to standard",
                "typical_use_cases": "Model evaluations, content summarization, agentic workflows, batch processing"
            }
        }
        
        return tier_info.get(tier_value, {
            "category": "unknown",
            "description": f"Service tier configured: {tier_value}",
            "pricing_model": "Unknown - verify tier value in AWS documentation",
            "typical_use_cases": "Unknown"
        })
    

