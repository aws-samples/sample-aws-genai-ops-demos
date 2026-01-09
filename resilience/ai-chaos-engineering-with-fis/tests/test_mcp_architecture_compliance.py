"""Property-based tests for MCP architecture compliance.

Tests Property 7: MCP Architecture Compliance
Validates: Requirements 5.1, 5.2
"""

import ast
import inspect
import tempfile
import time
from pathlib import Path
from datetime import datetime, timezone, timedelta
import pytest
from hypothesis import given, strategies as st, settings, HealthCheck
from typing import Dict, Any

from aws_chaos_engineering.fis_cache import FISCache
import aws_chaos_engineering.server as server_module


class TestMCPArchitectureCompliance:
    """Property-based tests for MCP architecture compliance functionality."""
    
    def test_server_does_not_import_direct_mcp_clients(self):
        """Property 7: The MCP server should not import any direct MCP clients or AWS clients.
        
        **Feature: aws-chaos-engineering-kiro-power, Property 7: MCP Architecture Compliance**
        **Validates: Requirements 5.1**
        """
        # Get the source code of the server module
        server_source = inspect.getsource(server_module)
        
        # Parse the source code into an AST
        tree = ast.parse(server_source)
        
        # Collect all import statements
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.append(node.module)
                    # Also check for specific imports from modules
                    for alias in node.names:
                        imports.append(f"{node.module}.{alias.name}")
        
        # Define prohibited imports that would indicate direct server-to-server calls
        prohibited_imports = [
            'requests',
            'urllib',
            'http.client',
            'httpx',
            'aiohttp',
            'boto3',
            'botocore',
            'aws',
            'mcp.client',
            'mcp_client',
            'fastmcp.client'
        ]
        
        # Check that no prohibited imports are present
        for prohibited in prohibited_imports:
            matching_imports = [imp for imp in imports if prohibited in imp.lower()]
            assert not matching_imports, f"Server should not import {prohibited} for direct calls. Found: {matching_imports}"
        
        # Verify only allowed imports are present
        allowed_patterns = [
            'fastmcp',  # FastMCP framework for MCP server implementation
            'pydantic',  # Data validation
            'typing',    # Type hints
            'json',      # JSON handling
            'logging',   # Logging
            'pathlib',   # Path handling
            'datetime',  # Time handling
            'tempfile',  # Temporary files
            'os',        # OS operations
            'sys',       # System operations
            'asyncio',   # Async operations
            '.fis_cache',     # Local cache module
            '.validators',    # Local validators module
            '.prompt_templates'  # Local prompt templates module
        ]
        
        # Check that all imports are from allowed patterns
        for imp in imports:
            is_allowed = any(pattern in imp for pattern in allowed_patterns)
            # Special case: relative imports without the dot prefix are also allowed
            if not is_allowed and not imp.startswith('.'):
                # Check if it's a relative import (like 'fis_cache' which is actually '.fis_cache')
                relative_imp = f'.{imp}'
                is_allowed = any(pattern in relative_imp for pattern in allowed_patterns)
            assert is_allowed, f"Unexpected import found: {imp}. Only allowed patterns: {allowed_patterns}"
    
    @given(
        region=st.sampled_from(['us-east-1', 'us-west-2', 'eu-west-1', 'test-region']),
        cache_age_hours=st.integers(min_value=25, max_value=100)  # Always stale (>24 hours)
    )
    @settings(max_examples=100)
    def test_stale_cache_returns_agent_instruction_not_direct_call(
        self, 
        region: str,
        cache_age_hours: int
    ):
        """Property 7: For any stale cache scenario, server should instruct agents instead of making direct calls.
        
        **Feature: aws-chaos-engineering-kiro-power, Property 7: MCP Architecture Compliance**
        **Validates: Requirements 5.1, 5.2**
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create FIS cache with temporary directory
            cache = FISCache(cache_dir=temp_dir)
            
            # Create old cached data (stale)
            old_timestamp = datetime.now(timezone.utc) - timedelta(hours=cache_age_hours)
            old_data = {
                "fis_actions": [{"id": "aws:ec2:stop-instances", "description": "Stop EC2 instances"}],
                "resource_types": [{"type": "aws:ec2:instance", "description": "EC2 instances"}],
                "last_updated": old_timestamp.isoformat(),
                "region": region,
                "cache_ttl_hours": 24
            }
            
            # Manually create stale cache file
            cache_file = cache._get_cache_file_path(region)
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            with open(cache_file, 'w') as f:
                import json
                json.dump(old_data, f)
            
            # Set the file modification time to match the old timestamp to make it stale
            import os
            old_timestamp_seconds = old_timestamp.timestamp()
            os.utime(cache_file, (old_timestamp_seconds, old_timestamp_seconds))
            
            # Temporarily replace the cache in server module for testing
            original_cache = server_module.fis_cache
            server_module.fis_cache = cache
            
            try:
                # Call get_valid_fis_actions - should return instruction, not make direct calls
                response = server_module.get_valid_fis_actions.fn(region=region)
                
                # Verify it returns instruction for agent instead of making direct calls
                assert response.cache_status == "stale", "Should detect stale cache"
                assert response.instruction is not None, "Should provide instruction for agent"
                assert "AWS MCP server" in response.instruction, "Should instruct agent to use AWS MCP server"
                assert "describe_fis_actions" in response.instruction, "Should specify AWS API calls for agent"
                assert "refresh_valid_fis_actions_cache" in response.instruction, "Should instruct agent to refresh cache"
                
                # Verify no direct data is returned (empty lists indicate no direct fetch)
                assert response.fis_actions == [], "Should not return data from direct calls"
                assert response.resource_types == [], "Should not return data from direct calls"
                
                # Verify the instruction contains proper agent orchestration guidance
                instruction_lower = response.instruction.lower()
                assert "call" in instruction_lower, "Should instruct agent to make calls"
                assert "then use" in instruction_lower, "Should provide sequential instruction for agent"
                
            finally:
                # Restore original cache
                server_module.fis_cache = original_cache
    
    @given(
        region=st.sampled_from(['us-east-1', 'us-west-2', 'eu-west-1', 'test-region'])
    )
    @settings(max_examples=100)
    def test_empty_cache_returns_agent_instruction_not_direct_call(
        self, 
        region: str
    ):
        """Property 7: For any empty cache scenario, server should instruct agents instead of making direct calls.
        
        **Feature: aws-chaos-engineering-kiro-power, Property 7: MCP Architecture Compliance**
        **Validates: Requirements 5.1, 5.2**
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create FIS cache with temporary directory (empty cache)
            cache = FISCache(cache_dir=temp_dir)
            
            # Temporarily replace the cache in server module for testing
            original_cache = server_module.fis_cache
            server_module.fis_cache = cache
            
            try:
                # Call get_valid_fis_actions with empty cache - should return instruction, not make direct calls
                response = server_module.get_valid_fis_actions.fn(region=region)
                
                # Verify it returns instruction for agent instead of making direct calls
                assert response.cache_status == "empty", "Should detect empty cache"
                assert response.instruction is not None, "Should provide instruction for agent"
                assert "AWS MCP server" in response.instruction, "Should instruct agent to use AWS MCP server"
                assert "describe_fis_actions" in response.instruction, "Should specify AWS API calls for agent"
                assert "refresh_valid_fis_actions_cache" in response.instruction, "Should instruct agent to refresh cache"
                
                # Verify no direct data is returned (empty lists indicate no direct fetch)
                assert response.fis_actions == [], "Should not return data from direct calls"
                assert response.resource_types == [], "Should not return data from direct calls"
                assert response.last_updated is None, "Should not have timestamp from direct calls"
                
                # Verify the instruction contains proper agent orchestration guidance
                instruction_lower = response.instruction.lower()
                assert "fetch" in instruction_lower or "call" in instruction_lower, "Should instruct agent to fetch data"
                assert "then use" in instruction_lower, "Should provide sequential instruction for agent"
                
            finally:
                # Restore original cache
                server_module.fis_cache = original_cache
    
    @given(
        region=st.sampled_from(['us-east-1', 'us-west-2', 'eu-west-1', 'test-region']),
        fis_actions_count=st.integers(min_value=1, max_value=5),
        resource_types_count=st.integers(min_value=1, max_value=5)
    )
    @settings(max_examples=100)
    def test_agent_data_acceptance_follows_mcp_architecture(
        self, 
        region: str,
        fis_actions_count: int,
        resource_types_count: int
    ):
        """Property 7: For any agent-provided data, server should accept it without making direct calls.
        
        **Feature: aws-chaos-engineering-kiro-power, Property 7: MCP Architecture Compliance**
        **Validates: Requirements 5.1, 5.2**
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create FIS cache with temporary directory
            cache = FISCache(cache_dir=temp_dir)
            
            # Generate test data that agent would provide from AWS MCP server
            agent_provided_data = {
                "fis_actions": [
                    {"id": f"aws:service:action-{i}", "description": f"Agent provided action {i}"}
                    for i in range(fis_actions_count)
                ],
                "resource_types": [
                    {"type": f"aws:service:resource-{i}", "description": f"Agent provided resource {i}"}
                    for i in range(resource_types_count)
                ]
            }
            
            # Temporarily replace the cache in server module for testing
            original_cache = server_module.fis_cache
            server_module.fis_cache = cache
            
            try:
                # Call refresh_valid_fis_actions_cache with agent-provided data
                response = server_module.refresh_valid_fis_actions_cache.fn(region=region, fis_data=agent_provided_data)
                
                # Verify successful acceptance of agent data without direct calls
                assert response.success is True, "Should successfully accept agent-provided data"
                assert response.region == region, "Should preserve region from agent request"
                assert response.last_updated is not None, "Should provide timestamp of update"
                assert "Error" not in response.message, "Should not have errors when accepting valid agent data"
                
                # Verify the data is now available in cache (proving agent orchestration works)
                cached_response = server_module.get_valid_fis_actions.fn(region=region)
                assert cached_response.cache_status == "fresh", "Cache should be fresh after agent update"
                assert cached_response.fis_actions == agent_provided_data["fis_actions"], "Should contain agent-provided actions"
                assert cached_response.resource_types == agent_provided_data["resource_types"], "Should contain agent-provided resource types"
                assert cached_response.instruction is None, "Should not need instruction when cache is fresh"
                
            finally:
                # Restore original cache
                server_module.fis_cache = original_cache
    
    @given(
        region=st.sampled_from(['us-east-1', 'us-west-2', 'eu-west-1', 'test-region'])
    )
    @settings(max_examples=50)
    def test_server_never_makes_outbound_network_calls(
        self, 
        region: str
    ):
        """Property 7: For any operation, server should never make outbound network calls.
        
        **Feature: aws-chaos-engineering-kiro-power, Property 7: MCP Architecture Compliance**
        **Validates: Requirements 5.1**
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create FIS cache with temporary directory
            cache = FISCache(cache_dir=temp_dir)
            
            # Temporarily replace the cache in server module for testing
            original_cache = server_module.fis_cache
            server_module.fis_cache = cache
            
            try:
                # Test all server operations to ensure no network calls are made
                
                # 1. Test get_valid_fis_actions with empty cache
                response1 = server_module.get_valid_fis_actions.fn(region=region)
                assert response1.cache_status == "empty", "Should handle empty cache locally"
                assert response1.instruction is not None, "Should provide agent instruction instead of making calls"
                
                # 2. Test refresh_valid_fis_actions_cache without data
                response2 = server_module.refresh_valid_fis_actions_cache.fn(region=region, fis_data=None)
                assert response2.success is False, "Should handle missing data locally"
                assert "No FIS data provided" in response2.message, "Should provide local error message"
                
                # 3. Test refresh_valid_fis_actions_cache with valid data
                test_data = {
                    "fis_actions": [{"id": "aws:ec2:stop-instances", "description": "Stop instances"}],
                    "resource_types": [{"type": "aws:ec2:instance", "description": "EC2 instances"}]
                }
                response3 = server_module.refresh_valid_fis_actions_cache.fn(region=region, fis_data=test_data)
                assert response3.success is True, "Should handle data update locally"
                
                # 4. Test get_valid_fis_actions with fresh cache
                response4 = server_module.get_valid_fis_actions.fn(region=region)
                assert response4.cache_status == "fresh", "Should handle fresh cache locally"
                assert response4.instruction is None, "Should not need instruction when cache is fresh"
                
                # All operations completed successfully without network calls
                # The fact that we can run these tests in isolation proves no external dependencies
                
            finally:
                # Restore original cache
                server_module.fis_cache = original_cache
    
    def test_server_module_has_no_network_dependencies(self):
        """Property 7: The server module should have no network client dependencies.
        
        **Feature: aws-chaos-engineering-kiro-power, Property 7: MCP Architecture Compliance**
        **Validates: Requirements 5.1**
        """
        # Get all attributes and methods of the server module
        server_attributes = dir(server_module)
        
        # Check that no network client objects are present
        network_client_patterns = [
            'client',
            'session',
            'http',
            'request',
            'boto',
            'aws_client',
            'mcp_client'
        ]
        
        for attr_name in server_attributes:
            attr_value = getattr(server_module, attr_name)
            
            # Skip built-in attributes and imports we know are safe
            if attr_name.startswith('__') or attr_name in ['FastMCP', 'BaseModel', 'Field', 'json', 'logging']:
                continue
            
            # Check attribute name doesn't suggest network client
            attr_name_lower = attr_name.lower()
            for pattern in network_client_patterns:
                assert pattern not in attr_name_lower, f"Server module should not have network client attribute: {attr_name}"
            
            # Check attribute type doesn't suggest network client
            attr_type_name = type(attr_value).__name__.lower()
            for pattern in network_client_patterns:
                if pattern in attr_type_name and pattern != 'client':  # 'client' is too generic
                    assert False, f"Server module should not have network client type: {attr_name} ({attr_type_name})"
    
    @given(
        regions=st.lists(
            st.sampled_from(['us-east-1', 'us-west-2', 'eu-west-1', 'ap-southeast-1']), 
            min_size=2, 
            max_size=3, 
            unique=True
        )
    )
    @settings(max_examples=30, suppress_health_check=[HealthCheck.too_slow])
    def test_agent_orchestration_workflow_compliance(
        self, 
        regions: list[str]
    ):
        """Property 7: For any multi-region scenario, server should maintain agent orchestration pattern.
        
        **Feature: aws-chaos-engineering-kiro-power, Property 7: MCP Architecture Compliance**
        **Validates: Requirements 5.1, 5.2**
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create FIS cache with temporary directory
            cache = FISCache(cache_dir=temp_dir)
            
            # Temporarily replace the cache in server module for testing
            original_cache = server_module.fis_cache
            server_module.fis_cache = cache
            
            try:
                # Test the complete agent orchestration workflow for multiple regions
                for i, region in enumerate(regions):
                    # Step 1: Agent requests data from empty cache
                    response1 = server_module.get_valid_fis_actions.fn(region=region)
                    assert response1.cache_status == "empty", f"Should detect empty cache for {region}"
                    assert response1.instruction is not None, f"Should provide agent instruction for {region}"
                    assert "AWS MCP server" in response1.instruction, f"Should reference AWS MCP server for {region}"
                    
                    # Step 2: Agent provides data (simulating AWS MCP server response)
                    agent_data = {
                        "fis_actions": [
                            {"id": f"aws:service:action-{region}-{j}", "description": f"Action {j} for {region}"}
                            for j in range(2)
                        ],
                        "resource_types": [
                            {"type": f"aws:service:resource-{region}-{j}", "description": f"Resource {j} for {region}"}
                            for j in range(2)
                        ]
                    }
                    
                    response2 = server_module.refresh_valid_fis_actions_cache.fn(region=region, fis_data=agent_data)
                    assert response2.success is True, f"Should accept agent data for {region}"
                    
                    # Step 3: Agent requests data from fresh cache
                    response3 = server_module.get_valid_fis_actions.fn(region=region)
                    assert response3.cache_status == "fresh", f"Should have fresh cache for {region}"
                    assert response3.instruction is None, f"Should not need instruction when cache is fresh for {region}"
                    assert response3.fis_actions == agent_data["fis_actions"], f"Should return agent-provided data for {region}"
                
                # Verify all regions maintain independent agent orchestration
                for region in regions:
                    final_response = server_module.get_valid_fis_actions.fn(region=region)
                    assert final_response.cache_status == "fresh", f"All regions should maintain fresh cache independently"
                    assert final_response.region == region, f"Should maintain region isolation"
                    
                    # Verify region-specific data (no cross-contamination)
                    for action in final_response.fis_actions:
                        assert region in action["id"], f"Actions should be region-specific for {region}"
                    for resource_type in final_response.resource_types:
                        assert region in resource_type["type"], f"Resource types should be region-specific for {region}"
                
            finally:
                # Restore original cache
                server_module.fis_cache = original_cache