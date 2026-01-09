"""Property-based tests for complete agent workflow.

Tests Property 1: Complete Agent Workflow
Validates: Requirements 1.2, 1.3, 1.4
"""

import json
import tempfile
from datetime import datetime, timezone, timedelta
import pytest
from hypothesis import given, strategies as st, assume, settings, HealthCheck
from typing import Dict, Any, List

from aws_chaos_engineering.fis_cache import FISCache
from aws_chaos_engineering.validators import FISTemplateValidator
from aws_chaos_engineering.prompt_templates import generate_system_prompt


# Test data generators
@st.composite
def generate_fis_action(draw):
    """Generate a valid FIS action for testing."""
    action_id = draw(st.sampled_from([
        "aws:ec2:stop-instances",
        "aws:rds:failover-db-cluster", 
        "aws:ecs:stop-task",
        "aws:lambda:invocation-error"
    ]))
    description = draw(st.text(min_size=10, max_size=100))
    return {"id": action_id, "description": description}


@st.composite
def generate_resource_type(draw):
    """Generate a valid resource type for testing."""
    resource_type = draw(st.sampled_from([
        "aws:ec2:instance",
        "aws:rds:cluster",
        "aws:ecs:task",
        "aws:lambda:function"
    ]))
    description = draw(st.text(min_size=10, max_size=100))
    return {"type": resource_type, "description": description}


@st.composite
def generate_fis_data(draw):
    """Generate complete FIS data for testing."""
    fis_actions = draw(st.lists(generate_fis_action(), min_size=1, max_size=5))
    resource_types = draw(st.lists(generate_resource_type(), min_size=1, max_size=5))
    
    return {
        "fis_actions": fis_actions,
        "resource_types": resource_types,
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "region": "us-east-1"
    }


@st.composite
def generate_architecture_description(draw):
    """Generate architecture descriptions for testing."""
    components = draw(st.lists(
        st.sampled_from([
            "EC2 instances", "RDS database", "Lambda functions", 
            "ECS tasks", "Load balancer", "Auto Scaling Group"
        ]),
        min_size=1, max_size=3
    ))
    
    return f"Architecture with {', '.join(components)} deployed in AWS"


@st.composite
def generate_fis_template(draw, fis_actions, resource_types):
    """Generate a FIS template using provided actions and resource types."""
    # Select random action and resource type from available ones
    action = draw(st.sampled_from(fis_actions))
    resource_type = draw(st.sampled_from(resource_types))
    
    template = {
        "description": draw(st.text(min_size=10, max_size=100)),
        "roleArn": "arn:aws:iam::123456789012:role/FISExperimentRole",
        "actions": {
            "TestAction": {
                "actionId": action["id"],
                "description": "Test action for chaos engineering",
                "targets": {
                    "TestTarget": "TestTargetName"
                }
            }
        },
        "targets": {
            "TestTargetName": {
                "resourceType": resource_type["type"],
                "resourceArns": ["arn:aws:ec2:us-east-1:123456789012:instance/i-1234567890abcdef0"],
                "selectionMode": "ALL"
            }
        },
        "stopConditions": [
            {
                "source": "aws:cloudwatch:alarm",
                "value": "arn:aws:cloudwatch:us-east-1:123456789012:alarm:TestAlarm"
            }
        ]
    }
    
    return template


class TestCompleteAgentWorkflow:
    """Property-based tests for complete agent workflow."""
    
    @given(
        fis_data=generate_fis_data(),
        architecture=generate_architecture_description(),
        region=st.sampled_from(["us-east-1", "us-west-2", "eu-west-1"]),
        data=st.data()
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_complete_agent_workflow_property(self, fis_data, architecture, region, data):
        """
        **Feature: aws-chaos-engineering-kiro-power, Property 1: Complete Agent Workflow**
        
        For any natural language chaos engineering request, the agent should:
        1. Fetch current FIS capabilities (Requirements 1.2)
        2. Generate and validate templates (Requirements 1.3) 
        3. Return deployable FIS JSON (Requirements 1.4)
        
        **Validates: Requirements 1.2, 1.3, 1.4**
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            # Setup: Create cache and validator instances
            cache = FISCache(cache_dir=temp_dir)
            validator = FISTemplateValidator()
            
            # Step 1: Agent fetches current FIS capabilities (Requirement 1.2)
            # Initially cache should be empty
            initial_status = cache.get_cache_status(region)
            assert initial_status == "empty"
            
            initial_data = cache.get_cached_data(region)
            assert initial_data is None
            
            # Agent refreshes cache with fresh data (simulating AWS MCP server call)
            success, message, timestamp = cache.update_cache(region, fis_data)
            assert success is True
            assert timestamp is not None
            
            # After refresh, cache should have fresh data
            updated_status = cache.get_cache_status(region)
            assert updated_status == "fresh"
            
            cached_data = cache.get_cached_data(region)
            assert cached_data is not None
            assert len(cached_data["fis_actions"]) > 0
            assert len(cached_data["resource_types"]) > 0
            
            # Step 2: Agent generates system prompt with current capabilities (Requirement 1.2)
            fis_actions = cached_data["fis_actions"]
            resource_types = cached_data["resource_types"]
            
            system_prompt = generate_system_prompt(fis_actions, resource_types, architecture)
            assert len(system_prompt) > 0
            
            # Verify system prompt contains current FIS data
            for action in fis_actions:
                assert action["id"] in system_prompt
            for resource_type in resource_types:
                assert resource_type["type"] in system_prompt
            assert architecture in system_prompt
            
            # Step 3: Agent validates generated template (Requirement 1.3)
            # Generate a valid template using the cached data
            test_template = data.draw(generate_fis_template(fis_actions, resource_types))
            
            validation_result = validator.validate_template(test_template, cache)
            
            # Step 4: Verify deployable FIS JSON is returned (Requirement 1.4)
            # Template should be valid since it uses cached actions/resource types
            assert validation_result["valid"] is True
            assert len(validation_result["errors"]) == 0
            assert len(validation_result["invalid_actions"]) == 0
            assert len(validation_result["invalid_resource_types"]) == 0
            assert validation_result["validation_timestamp"] != ""
            
            # Verify template structure is deployable
            assert "description" in test_template
            assert "roleArn" in test_template
            assert "actions" in test_template
            assert "targets" in test_template
            
            # Verify actions reference valid action IDs
            for action_name, action_config in test_template["actions"].items():
                action_id = action_config["actionId"]
                cached_action_ids = [a["id"] for a in fis_actions]
                assert action_id in cached_action_ids
            
            # Verify targets use valid resource types
            for target_name, target_config in test_template["targets"].items():
                resource_type = target_config["resourceType"]
                cached_resource_types = [rt["type"] for rt in resource_types]
                assert resource_type in cached_resource_types
    
    @given(
        fis_data=generate_fis_data(),
        architecture=generate_architecture_description()
    )
    @settings(max_examples=30)
    def test_workflow_handles_stale_cache(self, fis_data, architecture):
        """Test that workflow properly handles stale cache scenarios."""
        import os
        import time
        
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = FISCache(cache_dir=temp_dir)
            
            # First, create fresh cache data
            success, message, timestamp = cache.update_cache("us-east-1", fis_data)
            assert success is True
            
            # Manually make the cache file stale by changing its modification time
            cache_file = cache._get_cache_file_path("us-east-1")
            assert cache_file.exists()
            
            # Set file modification time to 25 hours ago
            stale_time = time.time() - (25 * 60 * 60)  # 25 hours ago
            os.utime(cache_file, (stale_time, stale_time))
            
            # Agent should detect stale cache
            cache_status = cache.get_cache_status("us-east-1")
            assert cache_status == "stale"
            
            # System prompt generation should handle stale cache appropriately
            # (In real workflow, agent would refresh cache first)
            cached_data = cache.get_cached_data("us-east-1")
            assert cached_data is not None  # Stale data is still retrievable
            
            # But cache status indicates it needs refresh
            assert cache_status == "stale"
    
    @given(
        architecture=generate_architecture_description()
    )
    @settings(max_examples=30)
    def test_workflow_handles_empty_cache(self, architecture):
        """Test that workflow properly handles empty cache scenarios."""
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = FISCache(cache_dir=temp_dir)
            
            # Agent should detect empty cache
            cache_status = cache.get_cache_status("us-east-1")
            assert cache_status == "empty"
            
            cached_data = cache.get_cached_data("us-east-1")
            assert cached_data is None
            
            # System prompt generation would need fresh data first
            # (In real workflow, agent would fetch data via AWS MCP server)
    
    @given(
        fis_data=generate_fis_data()
    )
    @settings(max_examples=30)
    def test_workflow_validates_invalid_templates(self, fis_data):
        """Test that workflow properly validates templates with invalid actions/resources."""
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = FISCache(cache_dir=temp_dir)
            validator = FISTemplateValidator()
            
            # Setup cache with valid data
            success, message, timestamp = cache.update_cache("us-east-1", fis_data)
            assert success is True
            
            # Create template with invalid action ID
            invalid_template = {
                "description": "Test template with invalid action",
                "roleArn": "arn:aws:iam::123456789012:role/FISExperimentRole",
                "actions": {
                    "InvalidAction": {
                        "actionId": "aws:invalid:nonexistent-action",
                        "description": "Invalid action for testing",
                        "targets": {"TestTarget": "TestTargetName"}
                    }
                },
                "targets": {
                    "TestTargetName": {
                        "resourceType": "aws:invalid:nonexistent-resource",
                        "resourceArns": ["arn:aws:ec2:us-east-1:123456789012:instance/i-invalid"],
                        "selectionMode": "ALL"
                    }
                }
            }
            
            # Validation should detect invalid action and resource type
            validation_result = validator.validate_template(invalid_template, cache)
            assert validation_result["valid"] is False
            assert len(validation_result["invalid_actions"]) > 0
            assert len(validation_result["invalid_resource_types"]) > 0
            assert "aws:invalid:nonexistent-action" in validation_result["invalid_actions"]
            assert "aws:invalid:nonexistent-resource" in validation_result["invalid_resource_types"]
    
    @given(
        fis_data=generate_fis_data(),
        architecture=generate_architecture_description()
    )
    @settings(max_examples=30)
    def test_workflow_system_prompt_integration(self, fis_data, architecture):
        """Test that system prompt properly integrates FIS data for agent use."""
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = FISCache(cache_dir=temp_dir)
            
            # Setup cache with data
            success, message, timestamp = cache.update_cache("us-east-1", fis_data)
            assert success is True
            
            cached_data = cache.get_cached_data("us-east-1")
            fis_actions = cached_data["fis_actions"]
            resource_types = cached_data["resource_types"]
            
            # Generate system prompt
            system_prompt = generate_system_prompt(fis_actions, resource_types, architecture)
            
            # Verify prompt structure and content
            assert len(system_prompt) > 0
            assert "expert in building large, complex systems" in system_prompt
            assert "AWS Fault Injection Service" in system_prompt
            assert "CRITICAL: Only use these current valid FIS actions" in system_prompt
            assert "CRITICAL: Only use these current valid resource types" in system_prompt
            
            # Verify all FIS actions are included
            for action in fis_actions:
                assert action["id"] in system_prompt
                assert action["description"] in system_prompt
            
            # Verify all resource types are included
            for resource_type in resource_types:
                assert resource_type["type"] in system_prompt
                assert resource_type["description"] in system_prompt
            
            # Verify architecture is included
            assert architecture in system_prompt
            
            # Verify safety guidelines are present
            assert "SAFETY GUIDELINES" in system_prompt
            assert "stop conditions" in system_prompt
            assert "gradual escalation" in system_prompt
            
            # Verify example templates are present
            assert "EXAMPLE FIS TEMPLATES" in system_prompt
            assert "aws:ec2:stop-instances" in system_prompt or "Example 1" in system_prompt