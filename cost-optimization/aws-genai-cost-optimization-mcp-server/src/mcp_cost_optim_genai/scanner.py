"""Project scanner for detecting AWS GenAI usage patterns."""

import json
import os
from pathlib import Path
from typing import List, Dict, Any, Set, Optional

from .detectors.bedrock_detector import BedrockDetector
from .detectors.agentcore_detector import AgentCoreDetector
from .detectors.prompt_engineering_detector import PromptEngineeringDetector
from .detectors.vsc_detector import VscDetector
from .scan_config import (
    find_scannable_files,
    estimate_scan_size,
    DEFAULT_SKIP_DIRS,
)
from .utils import add_file_links_to_findings
from .presentation_guidelines import PRESENTATION_GUIDELINES


class ProjectScanner:
    """Scans projects for AWS GenAI service usage."""

    def __init__(self):
        self.detectors = [
            BedrockDetector(),
            AgentCoreDetector(),
            PromptEngineeringDetector(),  # Comprehensive prompt optimization (AST + regex)
            VscDetector(),  # VSC format optimization for maximum token efficiency
        ]

    async def scan_project(
        self,
        project_path: str,
        skip_dirs: Optional[Set[str]] = None,
        max_files: Optional[int] = None,
        estimate_only: bool = False
    ) -> str:
        """Scan entire project directory with smart filtering.
        
        Args:
            project_path: Path to the project directory
            skip_dirs: Additional directories to skip (merged with defaults)
            max_files: Maximum number of files to scan (safety limit)
            estimate_only: If True, only return scan size estimate without scanning
            
        Returns:
            JSON string with scan results and findings
        """
        path = Path(project_path)
        if not path.exists():
            return json.dumps({"error": f"Path does not exist: {project_path}"})

        # Merge custom skip dirs with defaults
        effective_skip_dirs = DEFAULT_SKIP_DIRS.copy()
        if skip_dirs:
            effective_skip_dirs.update(skip_dirs)

        # Estimate scan size first
        scan_estimate = estimate_scan_size(path, effective_skip_dirs)
        
        # If estimate only, return early
        if estimate_only:
            return json.dumps({
                "status": "estimate",
                "project_path": project_path,
                "estimate": scan_estimate
            }, indent=2)
        
        # Warn if scan is large (>1000 files or >100MB)
        if scan_estimate["file_count"] > 1000 or scan_estimate["total_size_mb"] > 100:
            # Still proceed, but include warning in results
            scan_warning = {
                "warning": "Large scan detected",
                "file_count": scan_estimate["file_count"],
                "total_size_mb": scan_estimate["total_size_mb"],
                "estimated_time_seconds": scan_estimate["estimated_time_seconds"],
                "suggestion": "Consider using max_files parameter or adding more skip_dirs"
            }
        else:
            scan_warning = None

        # Find all scannable files efficiently
        scannable_files = find_scannable_files(path, effective_skip_dirs, max_files)
        
        findings = []
        files_scanned = 0
        
        # Scan each file
        for file_path in scannable_files:
            file_findings = await self._analyze_file_internal(file_path)
            findings.extend(file_findings)
            files_scanned += 1

        # Correlate findings for cross-cutting insights
        findings = self._correlate_findings(findings)

        result = {
            "status": "success",
            "project_path": project_path,
            "files_scanned": files_scanned,
            "total_findings": len(findings),
            "findings": findings,
            "scan_stats": {
                "estimated_files": scan_estimate["file_count"],
                "actual_files_scanned": files_scanned,
                "skipped_directories": scan_estimate["skipped_directories"]
            },
            "_presentation_instructions": {
                "CRITICAL_BEFORE_ANALYSIS": {
                    "priority": "HIGHEST",
                    "step_1": "For EVERY bedrock_model_usage finding, you MUST call AWS MCP Server FIRST before making any recommendations",
                    "required_mcp_server": "AWS MCP Server (mcp-proxy-for-aws)",
                    "required_command": "aws bedrock list-foundation-models --region us-east-1",
                    "purpose": "Get the REAL list of available Bedrock models to compare against detected models",
                    "why": "NEVER make assumptions about model versions, dates, or availability - always use actual AWS data",
                    "workflow": [
                        "1. Call: aws bedrock list-foundation-models --region us-east-1",
                        "2. Filter results by providerName (e.g., 'Anthropic') and modelName pattern (e.g., 'Sonnet')",
                        "3. Compare detected model_id against actual available models",
                        "4. Check modelLifecycle.status (ACTIVE, LEGACY, etc.)",
                        "5. If newer ACTIVE models exist, TRY AWS MCP Server (may not have latest models)",
                        "6. If pricing unavailable: Use tier-based guidance + AWS Console link",
                        "7. Make informed recommendations based on REAL data, not assumptions"
                    ],
                    "pricing_limitation": {
                        "issue": "AWS Pricing API lags behind model releases by weeks/months",
                        "example": "Claude 3.7, 4.0, 4.5 available in Bedrock but not in Pricing API",
                        "fallback": "When pricing unavailable, provide tier-based guidance and link to AWS Console",
                        "never_do": "Don't make up pricing numbers or estimates"
                    },
                    "example": "User has 'anthropic.claude-3-7-sonnet-20250219-v1:0' → Call API → Find 'anthropic.claude-sonnet-4-20250514-v1:0' and 'anthropic.claude-sonnet-4-5-20250929-v1:0' exist → Compare pricing → Recommend if appropriate"
                },
                "NEVER_DO": [
                    "❌ Say 'latest model' or 'current model' without checking AWS API first",
                    "❌ Make up model names, versions, or release dates",
                    "❌ Estimate pricing without calling AWS MCP Server",
                    "❌ Say things like 'Claude 4 (May 2025)' - use actual modelName from API response",
                    "❌ Recommend older models when user already has newer ones",
                    "❌ Contradict yourself (e.g., 'latest Sonnet' then 'newer Sonnet 4.5 exists')"
                ],
                "KNOWN_LIMITATION": {
                    "issue": "AWS Pricing API lags behind model releases",
                    "impact": "Pricing may not be available for newest Claude models (3.5+, 4.x)",
                    "what_works": "Nova models and Claude 3.0 models have pricing",
                    "what_doesnt": "Claude 3.5+, 3.7, and 4.x models may not have pricing yet",
                    "when_pricing_unavailable": [
                        "1. State clearly: 'Pricing data not yet available in AWS Pricing API'",
                        "2. Provide AWS Console link: https://aws.amazon.com/bedrock/pricing/",
                        "3. Focus on OTHER optimizations (caching, streaming, prompt engineering)",
                        "4. Do NOT make up or estimate pricing numbers"
                    ]
                },
                **PRESENTATION_GUIDELINES
            }
        }
        
        if scan_warning:
            result["warning"] = scan_warning
        
        # Add clickable file links to all findings
        result["findings"] = add_file_links_to_findings(result["findings"], project_path)
        
        return json.dumps(result, indent=2)

    async def analyze_file(self, file_path: str) -> str:
        """Analyze a single file."""
        path = Path(file_path)
        if not path.exists():
            return json.dumps({"error": f"File does not exist: {file_path}"})

        findings = await self._analyze_file_internal(path)
        
        # Correlate findings for cross-cutting insights
        findings = self._correlate_findings(findings)
        
        # Add clickable file links
        findings = add_file_links_to_findings(findings, str(path.parent))
        
        return json.dumps({
            "status": "success",
            "file": file_path,
            "total_findings": len(findings),
            "findings": findings
        }, indent=2)

    async def _analyze_file_internal(self, file_path: Path) -> List[Dict[str, Any]]:
        """Internal method to analyze a file with all detectors."""
        findings = []
        
        try:
            content = file_path.read_text(encoding="utf-8")
        except Exception as e:
            return [{"error": f"Could not read file: {e}", "file": str(file_path)}]

        for detector in self.detectors:
            if detector.can_analyze(file_path):
                detector_findings = detector.analyze(content, str(file_path))
                findings.extend(detector_findings)

        return findings

    def _correlate_findings(self, findings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Correlate findings to identify cross-cutting cost optimization opportunities.
        
        This method analyzes combinations of findings that span multiple services
        and adds insights about their combined cost impact.
        """
        # Group findings by file
        by_file = {}
        for finding in findings:
            file_path = finding.get("file", "unknown")
            by_file.setdefault(file_path, []).append(finding)
        
        additional_findings = []
        
        # Analyze each file's findings for cross-cutting patterns
        for file_path, file_findings in by_file.items():
            # Pattern 1: Bedrock Streaming + AgentCore Runtime
            has_agentcore = any(
                f.get("service") == "bedrock-agentcore" 
                for f in file_findings
            )
            has_bedrock_streaming = any(
                f.get("type") == "bedrock_api_call" and f.get("pattern") == "streaming"
                for f in file_findings
            )
            
            if has_agentcore and has_bedrock_streaming:
                additional_findings.append({
                    "type": "cross_service_cost_impact",
                    "file": file_path,
                    "services": ["bedrock", "bedrock-agentcore"],
                    "pattern": "streaming_in_agentcore_runtime",
                    "severity": "medium",
                    "cost_consideration": "Bedrock streaming responses in AgentCore Runtime extend compute billing time. While streaming improves user experience, it keeps the runtime active longer.",
                    "optimization_questions": [
                        "Does the user need to see responses in real-time?",
                        "Could responses be batched or returned synchronously?",
                        "Is the extended compute time worth the UX improvement?",
                        "For long responses, is streaming necessary, or would pagination work?"
                    ],
                    "context": "AgentCore Runtime charges based on compute time. Streaming responses take longer to complete than synchronous responses, extending the billing period."
                })
        
        return findings + additional_findings
