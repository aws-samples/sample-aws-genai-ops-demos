"""
Unit tests for Kiro Power configuration files.

Tests mcp.json structure, POWER.md frontmatter and sections, and steering file completeness.
Validates Requirements 4.2, 4.3.
"""

import json
import os
from pathlib import Path
import pytest
import re
from typing import Dict, Any, List


class TestKiroPowerConfiguration:
    """Test suite for Kiro Power configuration validation."""
    
    @pytest.fixture
    def power_root(self) -> Path:
        """Get the root directory of the Kiro Power."""
        # Navigate from tests directory to power root
        return Path(__file__).parent.parent
    
    @pytest.fixture
    def mcp_json_path(self, power_root: Path) -> Path:
        """Get path to mcp.json file."""
        return power_root / "mcp.json"
    
    @pytest.fixture
    def power_md_path(self, power_root: Path) -> Path:
        """Get path to POWER.md file."""
        return power_root / "POWER.md"
    
    @pytest.fixture
    def steering_dir(self, power_root: Path) -> Path:
        """Get path to steering directory."""
        return power_root / "steering"
    
    @pytest.fixture
    def mcp_config(self, mcp_json_path: Path) -> Dict[str, Any]:
        """Load and parse mcp.json configuration."""
        assert mcp_json_path.exists(), f"mcp.json not found at {mcp_json_path}"
        
        with open(mcp_json_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    @pytest.fixture
    def power_md_content(self, power_md_path: Path) -> str:
        """Load POWER.md content."""
        assert power_md_path.exists(), f"POWER.md not found at {power_md_path}"
        
        with open(power_md_path, 'r', encoding='utf-8') as f:
            return f.read()

    def test_mcp_json_structure_and_required_fields(self, mcp_config: Dict[str, Any]):
        """Test mcp.json has correct structure and all required fields."""
        # Test top-level structure
        assert "mcpServers" in mcp_config, "mcp.json must contain 'mcpServers' key"
        assert isinstance(mcp_config["mcpServers"], dict), "mcpServers must be a dictionary"
        
        mcp_servers = mcp_config["mcpServers"]
        
        # Test required servers are present
        required_servers = ["aws-chaos-engineering", "aws-mcp"]
        for server_name in required_servers:
            assert server_name in mcp_servers, f"Required MCP server '{server_name}' not found in configuration"
        
        # Test aws-chaos-engineering server configuration
        aws_chaos_server = mcp_servers["aws-chaos-engineering"]
        self._validate_server_config(aws_chaos_server, "aws-chaos-engineering")
        
        # Test aws-chaos-engineering specific fields
        assert aws_chaos_server["command"] == "uvx", "aws-chaos-engineering server must use uvx command"
        assert aws_chaos_server["args"] == ["aws-chaos-engineering"], "aws-chaos-engineering server args must be ['aws-chaos-engineering']"
        assert aws_chaos_server["disabled"] is False, "aws-chaos-engineering server must not be disabled"
        
        # Test autoApprove contains required tools
        required_tools = ["get_valid_fis_actions", "validate_fis_template", "refresh_valid_fis_actions_cache"]
        auto_approve = aws_chaos_server.get("autoApprove", [])
        for tool in required_tools:
            assert tool in auto_approve, f"Required tool '{tool}' not in autoApprove list"
        
        # Test aws-mcp server configuration
        aws_mcp_server = mcp_servers["aws-mcp"]
        self._validate_server_config(aws_mcp_server, "aws-mcp")
        
        # Test aws-mcp specific fields
        assert aws_mcp_server["command"] == "uvx", "aws-mcp server must use uvx command"
        assert "mcp-proxy-for-aws@latest" in aws_mcp_server["args"], "aws-mcp server must use mcp-proxy-for-aws"
        assert aws_mcp_server["disabled"] is False, "aws-mcp server must not be disabled"
        assert "timeout" in aws_mcp_server, "aws-mcp server must have timeout configured"
        assert aws_mcp_server["timeout"] > 0, "aws-mcp server timeout must be positive"
    
    def _validate_server_config(self, server_config: Dict[str, Any], server_name: str):
        """Validate common server configuration fields."""
        required_fields = ["command", "args", "disabled"]
        for field in required_fields:
            assert field in server_config, f"Server '{server_name}' missing required field '{field}'"
        
        assert isinstance(server_config["args"], list), f"Server '{server_name}' args must be a list"
        assert len(server_config["args"]) > 0, f"Server '{server_name}' args cannot be empty"
        assert isinstance(server_config["disabled"], bool), f"Server '{server_name}' disabled must be boolean"
        
        if "autoApprove" in server_config:
            assert isinstance(server_config["autoApprove"], list), f"Server '{server_name}' autoApprove must be a list"

    def test_power_md_frontmatter_and_required_sections(self, power_md_content: str):
        """Test POWER.md has correct frontmatter and all required sections."""
        # Test frontmatter exists and is valid YAML
        frontmatter_match = re.match(r'^---\n(.*?)\n---\n', power_md_content, re.DOTALL)
        assert frontmatter_match, "POWER.md must start with YAML frontmatter between --- markers"
        
        frontmatter_content = frontmatter_match.group(1)
        
        # Parse frontmatter (simple parsing for required fields)
        frontmatter_lines = frontmatter_content.strip().split('\n')
        frontmatter_dict = {}
        
        for line in frontmatter_lines:
            if ':' in line:
                key, value = line.split(':', 1)
                key = key.strip()
                value = value.strip()
                
                # Handle quoted strings
                if value.startswith('"') and value.endswith('"'):
                    value = value[1:-1]
                # Handle arrays
                elif value.startswith('[') and value.endswith(']'):
                    # Simple array parsing for keywords
                    value = [item.strip().strip('"') for item in value[1:-1].split(',') if item.strip()]
                
                frontmatter_dict[key] = value
        
        # Test required frontmatter fields
        required_frontmatter_fields = ["name", "displayName", "description", "keywords", "author"]
        for field in required_frontmatter_fields:
            assert field in frontmatter_dict, f"POWER.md frontmatter missing required field '{field}'"
            assert frontmatter_dict[field], f"POWER.md frontmatter field '{field}' cannot be empty"
        
        # Test specific frontmatter values
        assert frontmatter_dict["name"] == "aws-chaos-engineering", "POWER.md name must be 'aws-chaos-engineering'"
        assert "AWS Chaos Engineering" in frontmatter_dict["displayName"], "POWER.md displayName must contain 'AWS Chaos Engineering'"
        assert isinstance(frontmatter_dict["keywords"], list), "POWER.md keywords must be a list"
        assert len(frontmatter_dict["keywords"]) > 0, "POWER.md keywords cannot be empty"
        
        # Test chaos engineering keywords are present
        keywords = [kw.lower() for kw in frontmatter_dict["keywords"]]
        required_keywords = ["chaos engineering", "fault injection", "fis"]
        for keyword in required_keywords:
            assert any(keyword in kw for kw in keywords), f"POWER.md keywords must include '{keyword}'"
        
        # Get content after frontmatter
        content_after_frontmatter = power_md_content[frontmatter_match.end():]
        
        # Test required sections are present
        required_sections = [
            "# AWS Chaos Engineering Kiro Power",
            "## Overview", 
            "## Available MCP Servers",
            "## Tool Usage Examples",
            "## Common Workflows",
            "## Best Practices",
            "## Configuration",
            "## Troubleshooting"
        ]
        
        for section in required_sections:
            assert section in content_after_frontmatter, f"POWER.md missing required section '{section}'"
        
        # Test MCP servers are documented
        assert "aws-chaos-engineering Server" in content_after_frontmatter, "POWER.md must document aws-chaos-engineering server"
        assert "aws-mcp Server" in content_after_frontmatter, "POWER.md must document aws-mcp server"
        
        # Test tools are documented
        required_tools = ["get_valid_fis_actions", "validate_fis_template", "refresh_valid_fis_actions_cache"]
        for tool in required_tools:
            assert tool in content_after_frontmatter, f"POWER.md must document tool '{tool}'"
        
        # Test configuration sections
        assert "Prerequisites" in content_after_frontmatter, "POWER.md must include Prerequisites section"
        assert "AWS CLI" in content_after_frontmatter, "POWER.md must mention AWS CLI setup"
        assert "uvx" in content_after_frontmatter, "POWER.md must mention uvx installation"

    def test_steering_file_completeness(self, steering_dir: Path):
        """Test steering files exist and contain required content."""
        # Test steering directory exists
        assert steering_dir.exists(), f"Steering directory not found at {steering_dir}"
        assert steering_dir.is_dir(), "Steering path must be a directory"
        
        # Test required steering files exist
        required_files = ["getting-started.md", "advanced-patterns.md"]
        for filename in required_files:
            file_path = steering_dir / filename
            assert file_path.exists(), f"Required steering file '{filename}' not found"
            assert file_path.is_file(), f"Steering path '{filename}' must be a file"
        
        # Test getting-started.md content
        getting_started_path = steering_dir / "getting-started.md"
        with open(getting_started_path, 'r', encoding='utf-8') as f:
            getting_started_content = f.read()
        
        self._validate_getting_started_content(getting_started_content)
        
        # Test advanced-patterns.md content
        advanced_patterns_path = steering_dir / "advanced-patterns.md"
        with open(advanced_patterns_path, 'r', encoding='utf-8') as f:
            advanced_patterns_content = f.read()
        
        self._validate_advanced_patterns_content(advanced_patterns_content)
    
    def _validate_getting_started_content(self, content: str):
        """Validate getting-started.md has required sections and content."""
        required_sections = [
            "# Getting Started with AWS Chaos Engineering",
            "## Prerequisites",
            "## Step 1: Power Activation", 
            "## Step 2: Describe Your Architecture",
            "## Step 3: Review Generated Template",
            "## Step 4: Safety Review",
            "## Step 5: Deploy and Execute",
            "## Safety Guidelines",
            "## Troubleshooting"
        ]
        
        for section in required_sections:
            assert section in content, f"getting-started.md missing required section '{section}'"
        
        # Test specific content requirements
        assert "chaos engineering" in content.lower(), "getting-started.md must mention chaos engineering"
        assert "AWS FIS" in content, "getting-started.md must mention AWS FIS"
        assert "Stop Conditions" in content, "getting-started.md must cover stop conditions"
        assert "Safety" in content, "getting-started.md must emphasize safety"
        
        # Test trigger keywords are documented
        trigger_keywords = ["chaos engineering", "fault injection", "resilience testing"]
        for keyword in trigger_keywords:
            assert keyword in content.lower(), f"getting-started.md must document trigger keyword '{keyword}'"
    
    def _validate_advanced_patterns_content(self, content: str):
        """Validate advanced-patterns.md has required sections and content."""
        required_sections = [
            "# Advanced Chaos Engineering Patterns",
            "## Multi-Service Failure Scenarios",
            "## Time-Based Experiment Patterns", 
            "## Application-Specific Patterns",
            "## Advanced Safety Patterns",
            "## Automation and CI/CD Integration",
            "## Observability and Analysis Patterns"
        ]
        
        for section in required_sections:
            assert section in content, f"advanced-patterns.md missing required section '{section}'"
        
        # Test advanced concepts are covered
        advanced_concepts = ["Cascading Failure", "Multi-AZ", "Microservices", "CI/CD", "Game Day"]
        content_lower = content.lower()
        for concept in advanced_concepts:
            concept_lower = concept.lower()
            assert concept_lower in content_lower, f"advanced-patterns.md must cover '{concept}'"
        
        # Test safety emphasis in advanced patterns
        assert "Safety" in content, "advanced-patterns.md must emphasize safety in advanced scenarios"
        assert "Stop Condition" in content, "advanced-patterns.md must cover stop conditions"

    def test_configuration_consistency(self, mcp_config: Dict[str, Any], power_md_content: str):
        """Test consistency between mcp.json and POWER.md documentation."""
        # Extract server names from mcp.json
        mcp_servers = mcp_config["mcpServers"]
        
        # Test that all configured servers are documented in POWER.md
        for server_name in mcp_servers.keys():
            # Convert server name to expected documentation format
            if server_name == "aws-chaos-engineering":
                expected_doc = "aws-chaos-engineering Server"
            elif server_name == "aws-mcp":
                expected_doc = "aws-mcp Server"
            else:
                expected_doc = f"{server_name} Server"
            
            assert expected_doc in power_md_content, f"Server '{server_name}' from mcp.json not documented in POWER.md"
        
        # Test that autoApprove tools are documented
        aws_chaos_server = mcp_servers.get("aws-chaos-engineering", {})
        auto_approve_tools = aws_chaos_server.get("autoApprove", [])
        
        for tool in auto_approve_tools:
            assert tool in power_md_content, f"Auto-approved tool '{tool}' not documented in POWER.md"

    def test_file_permissions_and_encoding(self, power_root: Path):
        """Test that configuration files have proper permissions and encoding."""
        config_files = [
            power_root / "mcp.json",
            power_root / "POWER.md",
            power_root / "steering" / "getting-started.md",
            power_root / "steering" / "advanced-patterns.md"
        ]
        
        for file_path in config_files:
            assert file_path.exists(), f"Configuration file {file_path} does not exist"
            
            # Test file is readable
            assert os.access(file_path, os.R_OK), f"Configuration file {file_path} is not readable"
            
            # Test file can be opened with UTF-8 encoding
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    assert len(content) > 0, f"Configuration file {file_path} is empty"
            except UnicodeDecodeError:
                pytest.fail(f"Configuration file {file_path} is not valid UTF-8")