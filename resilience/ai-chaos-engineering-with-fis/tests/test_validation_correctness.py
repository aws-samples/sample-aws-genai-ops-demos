"""Property-based tests for validation correctness.

Tests Property 4: Validation Correctness
Validates: Requirements 3.1, 3.2, 3.3
"""

import tempfile
from datetime import datetime, timezone
import pytest
from hypothesis import given, strategies as st, settings, HealthCheck
from typing import Dict, Any, List, Set

from aws_chaos_engineering.fis_cache import FISCache
from aws_chaos_engineering.validators import FISTemplateValidator


class TestValidationCorrectness:
    """Property-based tests for FIS template validation correctness."""
    
    @given(
        region=st.sampled_from(['us-east-1', 'us-west-2', 'eu-west-1']),
        valid_actions=st.lists(
            st.text(min_size=5, max_size=50, alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd'), whitelist_characters=':_-')),
            min_size=1,
            max_size=5,
            unique=True
        ),
        valid_resource_types=st.lists(
            st.text(min_size=5, max_size=50, alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd'), whitelist_characters=':_-')),
            min_size=1,
            max_size=5,
            unique=True
        ),
        template_actions=st.lists(
            st.text(min_size=5, max_size=50, alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd'), whitelist_characters=':_-')),
            min_size=0,
            max_size=3,
            unique=True
        ),
        template_resource_types=st.lists(
            st.text(min_size=5, max_size=50, alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd'), whitelist_characters=':_-')),
            min_size=0,
            max_size=3,
            unique=True
        )
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_validation_checks_action_ids_against_capabilities(
        self,
        region: str,
        valid_actions: List[str],
        valid_resource_types: List[str],
        template_actions: List[str],
        template_resource_types: List[str]
    ):
        """Property 4: For any generated FIS template, validation should check action IDs against current capabilities.
        
        **Feature: aws-chaos-engineering-kiro-power, Property 4: Validation Correctness**
        **Validates: Requirements 3.1**
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            # Set up cache with valid capabilities
            cache = FISCache(cache_dir=temp_dir)
            validator = FISTemplateValidator()
            
            # Create cache data with valid actions and resource types
            fis_data = {
                "fis_actions": [
                    {"id": action_id, "description": f"Description for {action_id}"}
                    for action_id in valid_actions
                ],
                "resource_types": [
                    {"type": resource_type, "description": f"Description for {resource_type}"}
                    for resource_type in valid_resource_types
                ]
            }
            
            # Update cache
            success, message, timestamp = cache.update_cache(region, fis_data)
            assert success, f"Cache update should succeed: {message}"
            
            # Create FIS template with the specified actions and resource types
            template = {
                "actions": {
                    f"action{i}": {"actionId": action_id}
                    for i, action_id in enumerate(template_actions)
                },
                "targets": {
                    f"target{i}": {"resourceType": resource_type}
                    for i, resource_type in enumerate(template_resource_types)
                }
            }
            
            # Validate template
            result = validator.validate_template(template, cache)
            
            # Verify validation result structure
            assert isinstance(result, dict), "Validation result should be a dictionary"
            assert "valid" in result, "Result should have 'valid' field"
            assert "errors" in result, "Result should have 'errors' field"
            assert "warnings" in result, "Result should have 'warnings' field"
            assert "invalid_actions" in result, "Result should have 'invalid_actions' field"
            assert "invalid_resource_types" in result, "Result should have 'invalid_resource_types' field"
            assert "validation_timestamp" in result, "Result should have 'validation_timestamp' field"
            
            # Check action ID validation correctness
            template_action_set = set(template_actions)
            valid_action_set = set(valid_actions)
            expected_invalid_actions = template_action_set - valid_action_set
            
            if expected_invalid_actions:
                # Should detect invalid actions
                assert not result["valid"], "Template with invalid actions should be marked invalid"
                assert set(result["invalid_actions"]) == expected_invalid_actions, f"Should detect invalid actions: expected {expected_invalid_actions}, got {set(result['invalid_actions'])}"
                
                # Should have specific error messages for invalid actions
                for invalid_action in expected_invalid_actions:
                    action_error_found = any(
                        invalid_action in error and "Invalid action ID" in error
                        for error in result["errors"]
                    )
                    assert action_error_found, f"Should have specific error message for invalid action '{invalid_action}'"
            else:
                # All actions are valid, should not have action-related errors
                assert result["invalid_actions"] == [], "Should not report invalid actions when all are valid"
                action_errors = [error for error in result["errors"] if "Invalid action ID" in error]
                assert len(action_errors) == 0, "Should not have action ID errors when all actions are valid"
    
    @given(
        region=st.sampled_from(['us-east-1', 'us-west-2', 'eu-west-1']),
        valid_actions=st.lists(
            st.text(min_size=5, max_size=50, alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd'), whitelist_characters=':_-')),
            min_size=1,
            max_size=5,
            unique=True
        ),
        valid_resource_types=st.lists(
            st.text(min_size=5, max_size=50, alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd'), whitelist_characters=':_-')),
            min_size=1,
            max_size=5,
            unique=True
        ),
        template_actions=st.lists(
            st.text(min_size=5, max_size=50, alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd'), whitelist_characters=':_-')),
            min_size=0,
            max_size=3,
            unique=True
        ),
        template_resource_types=st.lists(
            st.text(min_size=5, max_size=50, alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd'), whitelist_characters=':_-')),
            min_size=0,
            max_size=3,
            unique=True
        )
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_validation_checks_resource_types_against_capabilities(
        self,
        region: str,
        valid_actions: List[str],
        valid_resource_types: List[str],
        template_actions: List[str],
        template_resource_types: List[str]
    ):
        """Property 4: For any generated FIS template, validation should check resource types against current capabilities.
        
        **Feature: aws-chaos-engineering-kiro-power, Property 4: Validation Correctness**
        **Validates: Requirements 3.2**
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            # Set up cache with valid capabilities
            cache = FISCache(cache_dir=temp_dir)
            validator = FISTemplateValidator()
            
            # Create cache data with valid actions and resource types
            fis_data = {
                "fis_actions": [
                    {"id": action_id, "description": f"Description for {action_id}"}
                    for action_id in valid_actions
                ],
                "resource_types": [
                    {"type": resource_type, "description": f"Description for {resource_type}"}
                    for resource_type in valid_resource_types
                ]
            }
            
            # Update cache
            success, message, timestamp = cache.update_cache(region, fis_data)
            assert success, f"Cache update should succeed: {message}"
            
            # Create FIS template with the specified actions and resource types
            template = {
                "actions": {
                    f"action{i}": {"actionId": action_id}
                    for i, action_id in enumerate(template_actions)
                },
                "targets": {
                    f"target{i}": {"resourceType": resource_type}
                    for i, resource_type in enumerate(template_resource_types)
                }
            }
            
            # Validate template
            result = validator.validate_template(template, cache)
            
            # Check resource type validation correctness
            template_resource_set = set(template_resource_types)
            valid_resource_set = set(valid_resource_types)
            expected_invalid_resources = template_resource_set - valid_resource_set
            
            if expected_invalid_resources:
                # Should detect invalid resource types
                assert not result["valid"], "Template with invalid resource types should be marked invalid"
                assert set(result["invalid_resource_types"]) == expected_invalid_resources, f"Should detect invalid resource types: expected {expected_invalid_resources}, got {set(result['invalid_resource_types'])}"
                
                # Should have specific error messages for invalid resource types
                for invalid_resource in expected_invalid_resources:
                    resource_error_found = any(
                        invalid_resource in error and "Invalid resource type" in error
                        for error in result["errors"]
                    )
                    assert resource_error_found, f"Should have specific error message for invalid resource type '{invalid_resource}'"
            else:
                # All resource types are valid, should not have resource-related errors
                assert result["invalid_resource_types"] == [], "Should not report invalid resource types when all are valid"
                resource_errors = [error for error in result["errors"] if "Invalid resource type" in error]
                assert len(resource_errors) == 0, "Should not have resource type errors when all resource types are valid"
    
    @given(
        region=st.sampled_from(['us-east-1', 'us-west-2', 'eu-west-1']),
        valid_actions=st.lists(
            st.text(min_size=5, max_size=50, alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd'), whitelist_characters=':_-')),
            min_size=2,
            max_size=5,
            unique=True
        ),
        valid_resource_types=st.lists(
            st.text(min_size=5, max_size=50, alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd'), whitelist_characters=':_-')),
            min_size=2,
            max_size=5,
            unique=True
        ),
        invalid_action_count=st.integers(min_value=1, max_value=3),
        invalid_resource_count=st.integers(min_value=1, max_value=3)
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_validation_returns_specific_errors_for_invalid_items(
        self,
        region: str,
        valid_actions: List[str],
        valid_resource_types: List[str],
        invalid_action_count: int,
        invalid_resource_count: int
    ):
        """Property 4: For any validation failures, the server should return specific errors about invalid actions or resource types.
        
        **Feature: aws-chaos-engineering-kiro-power, Property 4: Validation Correctness**
        **Validates: Requirements 3.3**
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            # Set up cache with valid capabilities
            cache = FISCache(cache_dir=temp_dir)
            validator = FISTemplateValidator()
            
            # Create cache data with valid actions and resource types
            fis_data = {
                "fis_actions": [
                    {"id": action_id, "description": f"Description for {action_id}"}
                    for action_id in valid_actions
                ],
                "resource_types": [
                    {"type": resource_type, "description": f"Description for {resource_type}"}
                    for resource_type in valid_resource_types
                ]
            }
            
            # Update cache
            success, message, timestamp = cache.update_cache(region, fis_data)
            assert success, f"Cache update should succeed: {message}"
            
            # Create invalid actions and resource types (guaranteed to be different from valid ones)
            invalid_actions = [f"invalid-action-{i}" for i in range(invalid_action_count)]
            invalid_resources = [f"invalid-resource-{i}" for i in range(invalid_resource_count)]
            
            # Ensure they don't accidentally match valid ones
            invalid_actions = [action for action in invalid_actions if action not in valid_actions]
            invalid_resources = [resource for resource in invalid_resources if resource not in valid_resource_types]
            
            # Skip test if we couldn't generate truly invalid items
            if not invalid_actions or not invalid_resources:
                return
            
            # Create template with mix of valid and invalid items
            template = {
                "actions": {
                    # Include some valid actions
                    f"valid_action{i}": {"actionId": valid_actions[i % len(valid_actions)]}
                    for i in range(min(2, len(valid_actions)))
                },
                "targets": {
                    # Include some valid resource types
                    f"valid_target{i}": {"resourceType": valid_resource_types[i % len(valid_resource_types)]}
                    for i in range(min(2, len(valid_resource_types)))
                }
            }
            
            # Add invalid actions
            for i, invalid_action in enumerate(invalid_actions):
                template["actions"][f"invalid_action{i}"] = {"actionId": invalid_action}
            
            # Add invalid resource types
            for i, invalid_resource in enumerate(invalid_resources):
                template["targets"][f"invalid_target{i}"] = {"resourceType": invalid_resource}
            
            # Validate template
            result = validator.validate_template(template, cache)
            
            # Should be marked as invalid
            assert not result["valid"], "Template with invalid items should be marked invalid"
            
            # Should report specific invalid actions
            assert len(result["invalid_actions"]) >= len(invalid_actions), f"Should report at least {len(invalid_actions)} invalid actions"
            for invalid_action in invalid_actions:
                assert invalid_action in result["invalid_actions"], f"Should report '{invalid_action}' as invalid"
            
            # Should report specific invalid resource types
            assert len(result["invalid_resource_types"]) >= len(invalid_resources), f"Should report at least {len(invalid_resources)} invalid resource types"
            for invalid_resource in invalid_resources:
                assert invalid_resource in result["invalid_resource_types"], f"Should report '{invalid_resource}' as invalid"
            
            # Should have specific error messages
            assert len(result["errors"]) > 0, "Should have error messages for invalid items"
            
            # Check that each invalid action has a specific error message
            for invalid_action in invalid_actions:
                action_error_found = any(
                    invalid_action in error and "Invalid action ID" in error
                    for error in result["errors"]
                )
                assert action_error_found, f"Should have specific error message for invalid action '{invalid_action}'"
            
            # Check that each invalid resource type has a specific error message
            for invalid_resource in invalid_resources:
                resource_error_found = any(
                    invalid_resource in error and "Invalid resource type" in error
                    for error in result["errors"]
                )
                assert resource_error_found, f"Should have specific error message for invalid resource type '{invalid_resource}'"
    
    @given(
        region=st.sampled_from(['us-east-1', 'us-west-2', 'eu-west-1']),
        valid_actions=st.lists(
            st.text(min_size=5, max_size=50, alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd'), whitelist_characters=':_-')),
            min_size=1,
            max_size=5,
            unique=True
        ),
        valid_resource_types=st.lists(
            st.text(min_size=5, max_size=50, alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd'), whitelist_characters=':_-')),
            min_size=1,
            max_size=5,
            unique=True
        )
    )
    @settings(max_examples=50)
    def test_validation_succeeds_for_all_valid_templates(
        self,
        region: str,
        valid_actions: List[str],
        valid_resource_types: List[str]
    ):
        """Property 4: For any template with only valid actions and resource types, validation should succeed.
        
        **Feature: aws-chaos-engineering-kiro-power, Property 4: Validation Correctness**
        **Validates: Requirements 3.1, 3.2**
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            # Set up cache with valid capabilities
            cache = FISCache(cache_dir=temp_dir)
            validator = FISTemplateValidator()
            
            # Create cache data with valid actions and resource types
            fis_data = {
                "fis_actions": [
                    {"id": action_id, "description": f"Description for {action_id}"}
                    for action_id in valid_actions
                ],
                "resource_types": [
                    {"type": resource_type, "description": f"Description for {resource_type}"}
                    for resource_type in valid_resource_types
                ]
            }
            
            # Update cache
            success, message, timestamp = cache.update_cache(region, fis_data)
            assert success, f"Cache update should succeed: {message}"
            
            # Create template using only valid actions and resource types
            template = {
                "actions": {
                    f"action{i}": {"actionId": action_id}
                    for i, action_id in enumerate(valid_actions)
                },
                "targets": {
                    f"target{i}": {"resourceType": resource_type}
                    for i, resource_type in enumerate(valid_resource_types)
                }
            }
            
            # Validate template
            result = validator.validate_template(template, cache)
            
            # Should be marked as valid
            assert result["valid"], "Template with only valid items should be marked valid"
            
            # Should not report any invalid items
            assert result["invalid_actions"] == [], "Should not report invalid actions for valid template"
            assert result["invalid_resource_types"] == [], "Should not report invalid resource types for valid template"
            
            # Should not have validation errors (warnings are OK)
            validation_errors = [error for error in result["errors"] if "Invalid" in error]
            assert len(validation_errors) == 0, f"Should not have validation errors for valid template, got: {validation_errors}"
    
    @given(
        region=st.sampled_from(['us-east-1', 'us-west-2', 'eu-west-1'])
    )
    @settings(max_examples=30)
    def test_validation_handles_empty_cache_gracefully(
        self,
        region: str
    ):
        """Property 4: For any template when cache is empty, validation should handle it gracefully.
        
        **Feature: aws-chaos-engineering-kiro-power, Property 4: Validation Correctness**
        **Validates: Requirements 3.1, 3.2, 3.3**
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            # Set up cache with no data
            cache = FISCache(cache_dir=temp_dir)
            validator = FISTemplateValidator()
            
            # Create a template
            template = {
                "actions": {
                    "action1": {"actionId": "aws:ec2:stop-instances"}
                },
                "targets": {
                    "target1": {"resourceType": "aws:ec2:instance"}
                }
            }
            
            # Validate template with empty cache
            result = validator.validate_template(template, cache)
            
            # Should handle gracefully
            assert isinstance(result, dict), "Should return validation result dictionary"
            assert "valid" in result, "Result should have 'valid' field"
            assert "errors" in result, "Result should have 'errors' field"
            assert "warnings" in result, "Result should have 'warnings' field"
            
            # Should have warning about missing cache data
            cache_warning_found = any(
                "No cached FIS capabilities" in warning or "refresh the cache" in warning
                for warning in result["warnings"]
            )
            assert cache_warning_found, "Should warn about missing cache data"
    
    @given(
        region=st.sampled_from(['us-east-1', 'us-west-2', 'eu-west-1']),
        template_structure=st.one_of(
            # CloudFormation-style template
            st.just({
                "Resources": {
                    "FISExperiment": {
                        "Type": "AWS::FIS::ExperimentTemplate",
                        "Properties": {
                            "Actions": {
                                "StopInstances": {"ActionId": "aws:ec2:stop-instances"}
                            },
                            "Targets": {
                                "EC2Instances": {"ResourceType": "aws:ec2:instance"}
                            }
                        }
                    }
                }
            }),
            # Direct FIS template style
            st.just({
                "actions": {
                    "stop_instances": {"actionId": "aws:ec2:stop-instances"}
                },
                "targets": {
                    "ec2_instances": {"resourceType": "aws:ec2:instance"}
                }
            })
        )
    )
    @settings(max_examples=50)
    def test_validation_handles_different_template_formats(
        self,
        region: str,
        template_structure: Dict[str, Any]
    ):
        """Property 4: For any template format, validation should extract and validate actions and resource types correctly.
        
        **Feature: aws-chaos-engineering-kiro-power, Property 4: Validation Correctness**
        **Validates: Requirements 3.1, 3.2**
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            # Set up cache with valid capabilities
            cache = FISCache(cache_dir=temp_dir)
            validator = FISTemplateValidator()
            
            # Create cache data with the actions/resources used in templates
            fis_data = {
                "fis_actions": [
                    {"id": "aws:ec2:stop-instances", "description": "Stop EC2 instances"}
                ],
                "resource_types": [
                    {"type": "aws:ec2:instance", "description": "EC2 instances"}
                ]
            }
            
            # Update cache
            success, message, timestamp = cache.update_cache(region, fis_data)
            assert success, f"Cache update should succeed: {message}"
            
            # Validate template
            result = validator.validate_template(template_structure, cache)
            
            # Should handle the template format correctly
            assert isinstance(result, dict), "Should return validation result dictionary"
            assert "valid" in result, "Result should have 'valid' field"
            
            # Should successfully validate the known good actions/resources
            # (Both template formats use aws:ec2:stop-instances and aws:ec2:instance which are in cache)
            assert result["valid"], f"Should validate successfully for known good template, got errors: {result.get('errors', [])}"
            assert result["invalid_actions"] == [], "Should not report invalid actions for valid template"
            assert result["invalid_resource_types"] == [], "Should not report invalid resource types for valid template"