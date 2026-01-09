"""Detector for Amazon Bedrock AgentCore usage patterns."""

import re
from pathlib import Path
from typing import List, Dict, Any

from .base import BaseDetector


class AgentCoreDetector(BaseDetector):
    """Detects Amazon Bedrock AgentCore usage in code."""

    # AgentCore app initialization patterns
    APP_PATTERNS = [
        r"BedrockAgentCoreApp\s*\(",
        r"from\s+bedrock_agentcore\s+import",
        r"import\s+bedrock_agentcore",
    ]

    # Runtime API call patterns (Python boto3, TypeScript/JavaScript SDK)
    RUNTIME_API_PATTERNS = {
        "create_agent_runtime": r"create_agent_runtime\s*\(",
        "update_agent_runtime": r"update_agent_runtime\s*\(",
        "CreateAgentRuntime": r"CreateAgentRuntime\s*\(",  # TypeScript/JavaScript
        "UpdateAgentRuntime": r"UpdateAgentRuntime\s*\(",  # TypeScript/JavaScript
    }

    # Decorator patterns
    DECORATOR_PATTERNS = {
        "entrypoint": r"@app\.entrypoint",
        "async_task": r"@app\.async_task",
        "ping": r"@app\.ping",
    }

    # Session management patterns
    SESSION_PATTERNS = [
        r"RequestContext",
        r"context\.session_id",
        r"--session-id",
    ]

    # Deployment patterns
    DEPLOYMENT_PATTERNS = {
        "direct_deploy": r"agentcore\s+launch(?!\s+--local)",
        "local_dev": r"agentcore\s+launch\s+--local",
        "hybrid_build": r"agentcore\s+launch\s+--local-build",
    }

    # Authentication patterns
    AUTH_PATTERNS = {
        "jwt": r"customJWTAuthorizer",
        "iam": r"IAM\s+SigV4",
        "authorizer_config": r"--authorizer-config",
    }

    # Lifecycle configuration patterns (supports Python, TypeScript, CDK)
    LIFECYCLE_PATTERNS = {
        # Python style: 'idleRuntimeSessionTimeout': 300 or "idleRuntimeSessionTimeout": 300
        # TypeScript/CDK: IdleRuntimeSessionTimeout: 300
        "idle_timeout": r"['\"]?[Ii]dle[Rr]untime[Ss]ession[Tt]imeout['\"]?\s*[:=]\s*(\d+)",
        "max_lifetime": r"['\"]?[Mm]ax[Ll]ifetime['\"]?\s*[:=]\s*(\d+)",
        # Matches: lifecycleConfiguration, LifecycleConfiguration, 'LifecycleConfiguration'
        "lifecycle_config": r"['\"]?[Ll]ifecycle[Cc]onfiguration['\"]?",
    }

    # CDK Runtime patterns
    CDK_RUNTIME_PATTERNS = {
        "cfn_runtime": r"new\s+bedrockagentcore\.CfnRuntime\s*\(",
        "l2_runtime": r"new\s+bedrockagentcore\.Runtime\s*\(",
    }

    # Session termination patterns (proactive cost optimization)
    STOP_SESSION_PATTERNS = [
        r"StopRuntimeSession",                              # AWS SDK v3 (TypeScript/JavaScript), Java SDK
        r"stop_runtime_session",                            # boto3 (Python), bedrock-agentcore-sdk-python
        r"stopRuntimeSession",                              # AWS SDK v2 (JavaScript), camelCase variants
        r"stop_session",                                    # bedrock-agentcore-sdk-python potential shorthand
        r"bedrock-agentcore-runtime:StopRuntimeSession",    # IAM policy actions
    ]

    def can_analyze(self, file_path: Path) -> bool:
        """Check if file is Python, TypeScript/JavaScript, or configuration file."""
        return file_path.suffix in [
            ".py",
            ".ts",
            ".tsx",
            ".js",
            ".jsx",
            ".sh",
            ".bash",
            ".yml",
            ".yaml",
        ]

    def analyze(self, content: str, file_path: str) -> List[Dict[str, Any]]:
        """Analyze content for AgentCore usage."""
        findings = []

        # Check for AgentCore app initialization
        app_line = self._has_agentcore_app(content)
        if app_line:
            findings.append(
                {
                    "type": "agentcore_app_detected",
                    "file": file_path,
                    "line": app_line,
                    "service": "bedrock-agentcore",
                    "description": "Amazon Bedrock AgentCore application detected",
                    "cost_consideration": "AgentCore Runtime charges based on compute time and memory allocation",
                }
            )

        # Detect decorator usage
        decorator_findings = self._detect_decorators(content, file_path)
        findings.extend(decorator_findings)

        # Detect session management
        session_findings = self._detect_session_usage(content, file_path)
        findings.extend(session_findings)

        # Detect deployment patterns
        deployment_findings = self._detect_deployment_patterns(content, file_path)
        findings.extend(deployment_findings)

        # Detect authentication patterns
        auth_findings = self._detect_auth_patterns(content, file_path)
        findings.extend(auth_findings)

        # Detect streaming patterns
        streaming_findings = self._detect_streaming(content, file_path)
        findings.extend(streaming_findings)

        # Detect async processing
        async_findings = self._detect_async_processing(content, file_path)
        findings.extend(async_findings)

        # Detect lifecycle configuration (both present and absent)
        lifecycle_findings = self._detect_lifecycle_config(content, file_path)
        findings.extend(lifecycle_findings)

        # Detect CDK Runtime without lifecycle config
        cdk_findings = self._detect_cdk_runtime_config(content, file_path)
        findings.extend(cdk_findings)
        
        # Detect Python API calls without lifecycle config
        python_api_findings = self._detect_runtime_api_lifecycle(content, file_path)
        findings.extend(python_api_findings)

        # Detect proactive session termination
        stop_session_findings = self._detect_stop_session(content, file_path)
        findings.extend(stop_session_findings)

        return findings

    def _has_agentcore_app(self, content: str) -> int:
        """Check if AgentCore app is initialized and return line number.
        
        Returns:
            Line number if found, 0 if not found
        """
        for pattern in self.APP_PATTERNS:
            match = re.search(pattern, content, re.IGNORECASE)
            if match:
                return content[: match.start()].count("\n") + 1
        return 0

    def _detect_decorators(self, content: str, file_path: str) -> List[Dict[str, Any]]:
        """Detect AgentCore decorator usage."""
        findings = []

        for decorator_type, pattern in self.DECORATOR_PATTERNS.items():
            matches = re.finditer(pattern, content)
            for match in matches:
                line_num = content[: match.start()].count("\n") + 1

                cost_note = ""
                if decorator_type == "entrypoint":
                    cost_note = "Main agent logic - compute time charged per invocation"
                elif decorator_type == "async_task":
                    cost_note = "Background task - extends compute time, agent stays in HealthyBusy state"
                elif decorator_type == "ping":
                    cost_note = "Health check endpoint - minimal cost impact"

                findings.append(
                    {
                        "type": "agentcore_decorator",
                        "file": file_path,
                        "line": line_num,
                        "decorator_type": decorator_type,
                        "service": "bedrock-agentcore",
                        "cost_consideration": cost_note,
                    }
                )

        return findings

    def _detect_session_usage(self, content: str, file_path: str) -> List[Dict[str, Any]]:
        """Detect session management usage."""
        findings = []

        for pattern in self.SESSION_PATTERNS:
            matches = re.finditer(pattern, content)
            for match in matches:
                line_num = content[: match.start()].count("\n") + 1

                findings.append(
                    {
                        "type": "agentcore_session_management",
                        "file": file_path,
                        "line": line_num,
                        "service": "bedrock-agentcore",
                        "description": "Session management detected",
                        "cost_consideration": "Sessions timeout after 15 minutes of inactivity. Consider session cleanup for cost optimization.",
                    }
                )
                break  # Only report once per file

        return findings

    def _detect_deployment_patterns(
        self, content: str, file_path: str
    ) -> List[Dict[str, Any]]:
        """Detect deployment patterns."""
        findings = []

        for deploy_type, pattern in self.DEPLOYMENT_PATTERNS.items():
            matches = re.finditer(pattern, content)
            for match in matches:
                line_num = content[: match.start()].count("\n") + 1

                cost_note = ""
                if deploy_type == "direct_deploy":
                    cost_note = "Direct code deploy - recommended for production, uses managed runtime"
                elif deploy_type == "local_dev":
                    cost_note = "Local development - no cloud costs during development"
                elif deploy_type == "hybrid_build":
                    cost_note = "Hybrid build - local container build, cloud deployment"

                findings.append(
                    {
                        "type": "agentcore_deployment",
                        "file": file_path,
                        "line": line_num,
                        "deployment_type": deploy_type,
                        "service": "bedrock-agentcore",
                        "cost_consideration": cost_note,
                    }
                )

        return findings

    def _detect_auth_patterns(self, content: str, file_path: str) -> List[Dict[str, Any]]:
        """Detect authentication patterns."""
        findings = []

        for auth_type, pattern in self.AUTH_PATTERNS.items():
            if re.search(pattern, content, re.IGNORECASE):
                findings.append(
                    {
                        "type": "agentcore_authentication",
                        "file": file_path,
                        "auth_type": auth_type,
                        "service": "bedrock-agentcore",
                        "description": f"Authentication pattern detected: {auth_type}",
                        "cost_consideration": "Authentication adds minimal overhead but ensures secure access",
                    }
                )
                break  # Only report once per file

        return findings

    def _detect_streaming(self, content: str, file_path: str) -> List[Dict[str, Any]]:
        """Detect streaming response patterns."""
        findings = []

        streaming_patterns = [
            r"async\s+def\s+\w+.*yield",
            r"app\.stream_async",
            r"for\s+event\s+in\s+stream",
        ]

        for pattern in streaming_patterns:
            matches = re.finditer(pattern, content, re.DOTALL)
            for match in matches:
                line_num = content[: match.start()].count("\n") + 1

                findings.append(
                    {
                        "type": "agentcore_streaming",
                        "file": file_path,
                        "line": line_num,
                        "service": "bedrock-agentcore",
                        "description": "Streaming response pattern detected",
                        "cost_consideration": "Streaming responses improve UX but may extend compute time. Consider chunking strategy.",
                    }
                )
                break  # Only report once per file

        return findings

    def _detect_async_processing(self, content: str, file_path: str) -> List[Dict[str, Any]]:
        """Detect async/background processing patterns."""
        findings = []

        async_patterns = [
            r"asyncio\.create_task",
            r"app\.add_async_task",
            r"threading\.Thread",
            r"HealthyBusy",
        ]

        for pattern in async_patterns:
            matches = re.finditer(pattern, content)
            for match in matches:
                line_num = content[: match.start()].count("\n") + 1

                findings.append(
                    {
                        "type": "agentcore_async_processing",
                        "file": file_path,
                        "line": line_num,
                        "service": "bedrock-agentcore",
                        "description": "Async/background processing detected",
                        "cost_consideration": "Background tasks keep agent in HealthyBusy state, extending compute time. Monitor task duration.",
                    }
                )
                break  # Only report once per file

        return findings

    def _detect_lifecycle_config(self, content: str, file_path: str) -> List[Dict[str, Any]]:
        """Detect lifecycle configuration settings."""
        findings = []

        # Check if lifecycle configuration is present
        if not re.search(self.LIFECYCLE_PATTERNS["lifecycle_config"], content):
            return findings

        # Default values from AWS documentation
        DEFAULT_IDLE_TIMEOUT = 900  # 15 minutes
        DEFAULT_MAX_LIFETIME = 28800  # 8 hours

        # Extract all idle timeout configurations
        for idle_match in re.finditer(self.LIFECYCLE_PATTERNS["idle_timeout"], content):
            idle_timeout = int(idle_match.group(1))
            line_num = content[: idle_match.start()].count("\n") + 1

            # Analyze the value
            cost_note = self._analyze_idle_timeout(idle_timeout, DEFAULT_IDLE_TIMEOUT)

            findings.append(
                {
                    "type": "agentcore_lifecycle_idle_timeout",
                    "file": file_path,
                    "line": line_num,
                    "service": "bedrock-agentcore",
                    "configured_value": idle_timeout,
                    "default_value": DEFAULT_IDLE_TIMEOUT,
                    "unit": "seconds",
                    "cost_consideration": cost_note,
                }
            )

        # Extract all max lifetime configurations
        for max_match in re.finditer(self.LIFECYCLE_PATTERNS["max_lifetime"], content):
            max_lifetime = int(max_match.group(1))
            line_num = content[: max_match.start()].count("\n") + 1

            # Analyze the value
            cost_note = self._analyze_max_lifetime(max_lifetime, DEFAULT_MAX_LIFETIME)

            findings.append(
                {
                    "type": "agentcore_lifecycle_max_lifetime",
                    "file": file_path,
                    "line": line_num,
                    "service": "bedrock-agentcore",
                    "configured_value": max_lifetime,
                    "default_value": DEFAULT_MAX_LIFETIME,
                    "unit": "seconds",
                    "cost_consideration": cost_note,
                }
            )

        return findings

    def _analyze_idle_timeout(self, configured: int, default: int) -> str:
        """Analyze idle timeout configuration for cost implications."""
        if configured > default:
            minutes = configured / 60
            return f"COST ALERT: Idle timeout ({configured}s / {minutes:.1f}min) is HIGHER than default ({default}s). Instances stay alive longer when idle, increasing costs. Consider reducing if workload allows."
        elif configured < default:
            minutes = configured / 60
            return f"Cost optimized: Idle timeout ({configured}s / {minutes:.1f}min) is lower than default ({default}s). Instances terminate faster when idle, reducing costs."
        else:
            return f"Using default idle timeout ({default}s / 15min). Consider reducing for cost savings if workload allows."

    def _analyze_max_lifetime(self, configured: int, default: int) -> str:
        """Analyze max lifetime configuration for cost implications."""
        if configured > default:
            hours = configured / 3600
            return f"COST ALERT: Max lifetime ({configured}s / {hours:.1f}h) is HIGHER than default ({default}s / 8h). Instances can run longer, increasing costs. Ensure this is necessary for your workload."
        elif configured < default:
            hours = configured / 3600
            return f"Cost optimized: Max lifetime ({configured}s / {hours:.1f}h) is lower than default ({default}s / 8h). Instances terminate sooner, reducing costs."
        else:
            return f"Using default max lifetime ({default}s / 8h). Consider reducing for cost savings if workload completes faster."

    def _detect_cdk_runtime_config(self, content: str, file_path: str) -> List[Dict[str, Any]]:
        """Detect CDK Runtime creation and check for lifecycle configuration."""
        findings = []

        # Only check TypeScript/JavaScript CDK files
        if not file_path.endswith(('.ts', '.tsx', '.js', '.jsx')):
            return findings

        # Check if this is a CDK file with Runtime creation
        has_runtime = False
        runtime_line = 0
        
        for pattern_name, pattern in self.CDK_RUNTIME_PATTERNS.items():
            match = re.search(pattern, content)
            if match:
                has_runtime = True
                runtime_line = content[:match.start()].count('\n') + 1
                break

        if not has_runtime:
            return findings

        # Check if lifecycle configuration is present in the Runtime definition OR via addPropertyOverride
        # Look for lifecycleConfiguration within the Runtime block
        has_lifecycle_config_inline = re.search(
            r"lifecycleConfiguration\s*:\s*\{",
            content,
            re.IGNORECASE
        )
        
        # Check for CDK addPropertyOverride pattern (common in TypeScript/JavaScript)
        # Example: agentRuntime.addPropertyOverride('LifecycleConfiguration', {...})
        has_lifecycle_config_override = re.search(
            r"\.addPropertyOverride\s*\(\s*['\"]LifecycleConfiguration['\"]",
            content,
            re.IGNORECASE
        )

        # If neither inline config nor override exists, report using defaults
        if not has_lifecycle_config_inline and not has_lifecycle_config_override:
            # No lifecycle config found - using AWS defaults
            findings.append({
                "type": "agentcore_lifecycle_missing",
                "file": file_path,
                "line": runtime_line,
                "service": "bedrock-agentcore",
                "description": "AgentCore Runtime created without explicit lifecycleConfiguration",
                "issue": "Using AWS default lifecycle settings without explicit configuration",
                "defaults_being_used": {
                    "idleRuntimeSessionTimeout": "900 seconds (15 minutes)",
                    "maxLifetime": "28800 seconds (8 hours)"
                },
                "cost_consideration": "Defaults may not be optimal for your workload. Instances stay alive for 15 minutes after idle and up to 8 hours maximum. If your workload completes faster, you're paying for unused compute time.",
                "optimization_opportunity": {
                    "assess_workload": "Measure actual task completion time and idle periods",
                    "if_tasks_complete_quickly": "Reduce idleRuntimeSessionTimeout (e.g., 300s = 5min) to terminate faster",
                    "if_workload_is_short": "Reduce maxLifetime (e.g., 3600s = 1hr) if tasks never run that long",
                    "potential_savings": "Reducing idle timeout from 15min to 5min can save ~67% on idle time costs"
                },
                "next_steps": [
                    "Monitor actual runtime session durations in CloudWatch",
                    "Identify average task completion time",
                    "Set idleRuntimeSessionTimeout slightly above average idle time",
                    "Set maxLifetime based on longest expected task duration"
                ],
                "documentation": "https://docs.aws.amazon.com/bedrock-agentcore/latest/APIReference/API_CreateAgentRuntime.html"
            })

        return findings

    def _detect_runtime_api_lifecycle(self, content: str, file_path: str) -> List[Dict[str, Any]]:
        """Detect Python boto3 Runtime API calls and check for lifecycle configuration.
        
        Following DRY principle: Reuses self.RUNTIME_API_PATTERNS for consistency.
        Following Principle 11: Tests both presence AND absence of lifecycle config.
        """
        findings = []

        # Only check Python files
        if not file_path.endswith('.py'):
            return findings

        # Check for Runtime API calls using class-level patterns (DRY)
        for api_name, pattern in self.RUNTIME_API_PATTERNS.items():
            # Skip TypeScript/JavaScript patterns
            if api_name in ['CreateAgentRuntime', 'UpdateAgentRuntime']:
                continue
                
            matches = re.finditer(pattern, content)
            for match in matches:
                api_line = content[:match.start()].count('\n') + 1
                
                # Find the closing parenthesis for this API call
                call_start = match.start()
                call_end = self._find_matching_paren(content, match.end() - 1)
                
                if call_end == -1:
                    # Couldn't find matching paren, use limited context
                    call_context = content[call_start:min(call_start + 500, len(content))]
                else:
                    # Use only the content within this API call
                    call_context = content[call_start:call_end + 1]
                
                # Check if lifecycleConfiguration is present in this call
                has_lifecycle = re.search(
                    r'lifecycleConfiguration\s*=',
                    call_context,
                    re.IGNORECASE
                )
                
                if not has_lifecycle:
                    # API call without lifecycle config - using defaults
                    findings.append({
                        "type": "agentcore_lifecycle_missing",
                        "file": file_path,
                        "line": api_line,
                        "api_call": api_name,
                        "service": "bedrock-agentcore",
                        "description": f"{api_name} call without explicit lifecycleConfiguration",
                        "issue": "Using AWS default lifecycle settings without explicit configuration",
                        "defaults_being_used": {
                            "idleRuntimeSessionTimeout": "900 seconds (15 minutes)",
                            "maxLifetime": "28800 seconds (8 hours)"
                        },
                        "cost_consideration": "Defaults may not be optimal for your workload. Instances stay alive for 15 minutes after idle and up to 8 hours maximum. If your workload completes faster, you're paying for unused compute time.",
                        "optimization_opportunity": {
                            "assess_workload": "Measure actual task completion time and idle periods",
                            "if_tasks_complete_quickly": "Reduce idleRuntimeSessionTimeout (e.g., 300s = 5min) to terminate faster",
                            "if_workload_is_short": "Reduce maxLifetime (e.g., 3600s = 1hr) if tasks never run that long",
                            "potential_savings": "Reducing idle timeout from 15min to 5min can save ~67% on idle time costs"
                        },
                        "next_steps": [
                            "Monitor actual runtime session durations in CloudWatch",
                            "Identify average task completion time",
                            "Set idleRuntimeSessionTimeout slightly above average idle time",
                            "Set maxLifetime based on longest expected task duration"
                        ],
                        "documentation": "https://docs.aws.amazon.com/bedrock-agentcore/latest/APIReference/API_CreateAgentRuntime.html"
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

    def _detect_stop_session(self, content: str, file_path: str) -> List[Dict[str, Any]]:
        """Detect proactive session termination using StopRuntimeSession."""
        findings = []

        # Check if any pattern matches (only report once per file)
        for pattern in self.STOP_SESSION_PATTERNS:
            match = re.search(pattern, content, re.IGNORECASE)
            if match:
                line_num = content[: match.start()].count("\n") + 1

                findings.append(
                    {
                        "type": "agentcore_stop_session_detected",
                        "file": file_path,
                        "line": line_num,
                        "service": "bedrock-agentcore",
                        "description": "✅ EXCELLENT: Proactive session termination detected using StopRuntimeSession",
                        "cost_consideration": "Manually stopping sessions prevents idle timeout charges. This is a cost optimization best practice - sessions terminate immediately instead of waiting for idle timeout (default 15min).",
                        "api_reference": "https://docs.aws.amazon.com/bedrock-agentcore/latest/APIReference/API_StopRuntimeSession.html",
                        "benefit": "Eliminates idle time charges by terminating sessions immediately when work is complete",
                        "best_practice": "Call StopRuntimeSession after completing agent tasks to avoid paying for idle compute time"
                    }
                )
                break  # Only report once per file

        return findings
