"""Property-based tests for validation scope limitation.

Tests Property 5: Validation Scope Limitation
Validates: Requirements 3.4
"""

import tempfile
from datetime import datetime, timezone
import pytest
from hypothesis import given, strategies as st, settings, HealthCheck
from typing import Dict, Any, List

from aws_chaos_engineering.fis_cache import FISCache
from aws_chaos_engineering.validators import FISTemplateValidator


class TestValidationScopeLimitation:
    """Property-based tests for FIS template validation scope limitation."""
    
    @given(
        region=st.sampled_from(['us-east-1', 'us-west-2', 'eu-west-1']),
        valid_actions=st.lists(
            st.text(min_size=5, max_size=50, alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd'), whitelist_characters=':_-')),
            min_size=1,
            max_size=3,
            unique=True
        ),
        valid_resource_types=st.lists(
            st.text(min_size=5, max_size=50, alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd'), whitelist_characters=':_-')),
            min_size=1,
            max_size=3,
            unique=True
        ),
        iam_roles=st.lists(
            st.text(min_size=10, max_size=100, alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd'), whitelist_characters=':-/_')),
            min_size=1,
            max_size=3,
            unique=True
        ),
        resource_arns=st.lists(
            st.text(min_size=20, max_size=150, alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd'), whitelist_characters=':-/_*')),
            min_size=1,
            max_size=5,
            unique=True
        ),
        business_logic_params=st.dictionaries(
            st.text(min_size=3, max_size=20, alphabet=st.characters(whitelist_categories=('Lu', 'Ll'))),
            st.one_of(
                st.text(min_size=1, max_size=50),
                st.integers(min_value=1, max_value=1000),
                st.booleans()
            ),
            min_size=1,
            max_size=5
        )
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_validation_ignores_iam_permissions_arns_and_business_logic(
        self,
        region: str,
        valid_actions: List[str],
        valid_resource_types: List[str],
        iam_roles: List[str],
        resource_arns: List[str],
        business_logic_params: Dict[str, Any]
    ):
        """Property 5: For any FIS template containing IAM permissions, ARNs, or business logic, 
        the validator should ignore these elements and only check action IDs and resource types.
        
        **Feature: aws-chaos-engineering-kiro-power, Property 5: Validation Scope Limitation**
        **Validates: Requirements 3.4**
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
            
            # Create template with valid actions/resources but including IAM, ARNs, and business logic
            template = {
                "actions": {
                    f"action{i}": {
                        "actionId": action_id,
                        # Add IAM-related fields that should be ignored
                        "roleArn": f"arn:aws:iam::123456789012:role/{iam_roles[i % len(iam_roles)]}",
                        "parameters": business_logic_params,
                        # Add business logic parameters
                        "duration": "PT10M",
                        "percentage": 50,
                        "instanceIds": resource_arns[:2],  # ARNs that should be ignored
                    }
                    for i, action_id in enumerate(valid_actions)
                },
                "targets": {
                    f"target{i}": {
                        "resourceType": resource_type,
                        # Add ARNs and resource identifiers that should be ignored
                        "resourceArns": resource_arns,
                        "resourceTags": {"Environment": "test", "Team": "ops"},
                        "selectionMode": "PERCENT(50)",
                        # Add business logic selection criteria
                        "filters": [
                            {"path": "State.Name", "values": ["running", "stopped"]},
                            {"path": "InstanceType", "values": ["t3.micro", "t3.small"]}
                        ]
                    }
                    for i, resource_type in enumerate(valid_resource_types)
                },
                # Add top-level IAM and business logic fields
                "roleArn": f"arn:aws:iam::123456789012:role/{iam_roles[0]}",
                "description": "Test experiment with complex business logic",
                "stopConditions": [
                    {
                        "source": "aws:cloudwatch:alarm",
                        "value": f"arn:aws:cloudwatch:us-east-1:123456789012:alarm/{business_logic_params.get('alarmName', 'test-alarm')}"
                    }
                ],
                "tags": {
                    "Owner": "test-team",
                    "Environment": "development",
                    "CostCenter": "engineering"
                },
                # Add complex business logic configuration
                "experimentOptions": {
                    "accountTargeting": "single-account",
                    "emptyTargetResolutionMode": "fail"
                }
            }
            
            # Validate template
            result = validator.validate_template(template, cache)
            
            # Should be valid because actions and resource types are valid
            # (IAM, ARNs, and business logic should be ignored)
            assert result["valid"], f"Template should be valid when actions/resources are valid, regardless of IAM/ARNs/business logic. Errors: {result.get('errors', [])}"
            
            # Should not report any invalid actions or resource types
            assert result["invalid_actions"] == [], "Should not report invalid actions when actions are valid"
            assert result["invalid_resource_types"] == [], "Should not report invalid resource types when resource types are valid"
            
            # Should not have validation errors related to IAM, ARNs, or business logic
            iam_arn_errors = [
                error for error in result["errors"] 
                if any(keyword in error.lower() for keyword in ['iam', 'arn', 'role', 'permission', 'duration', 'percentage', 'tag', 'filter', 'alarm'])
            ]
            assert len(iam_arn_errors) == 0, f"Should not validate IAM, ARNs, or business logic, but found errors: {iam_arn_errors}"
            
            # Should have the scope limitation warning
            scope_warning_found = any(
                "Validation covers only action IDs and resource types" in warning or
                "IAM permissions, ARNs, and business logic are not validated" in warning
                for warning in result["warnings"]
            )
            assert scope_warning_found, "Should include warning about validation scope limitations"
    
    @given(
        region=st.sampled_from(['us-east-1', 'us-west-2', 'eu-west-1']),
        valid_actions=st.lists(
            st.text(min_size=5, max_size=50, alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd'), whitelist_characters=':_-')),
            min_size=1,
            max_size=2,
            unique=True
        ),
        valid_resource_types=st.lists(
            st.text(min_size=5, max_size=50, alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd'), whitelist_characters=':_-')),
            min_size=1,
            max_size=2,
            unique=True
        ),
        invalid_actions=st.lists(
            st.text(min_size=5, max_size=50, alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd'), whitelist_characters=':_-')),
            min_size=1,
            max_size=2,
            unique=True
        )
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_validation_still_catches_invalid_actions_despite_complex_template(
        self,
        region: str,
        valid_actions: List[str],
        valid_resource_types: List[str],
        invalid_actions: List[str]
    ):
        """Property 5: For any template with invalid actions but valid business logic/IAM/ARNs, 
        validation should still catch the invalid actions while ignoring other elements.
        
        **Feature: aws-chaos-engineering-kiro-power, Property 5: Validation Scope Limitation**
        **Validates: Requirements 3.4**
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            # Set up cache with valid capabilities
            cache = FISCache(cache_dir=temp_dir)
            validator = FISTemplateValidator()
            
            # Ensure invalid actions are truly different from valid ones
            invalid_actions = [action for action in invalid_actions if action not in valid_actions]
            if not invalid_actions:
                # Generate guaranteed invalid actions
                invalid_actions = [f"invalid-action-{i}" for i in range(2)]
            
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
            
            # Create template with invalid actions but complex valid IAM/business logic
            template = {
                "actions": {
                    # Mix of valid and invalid actions
                    "valid_action": {
                        "actionId": valid_actions[0],
                        "roleArn": "arn:aws:iam::123456789012:role/ValidRole",
                        "parameters": {"duration": "PT5M", "percentage": 25}
                    },
                    "invalid_action": {
                        "actionId": invalid_actions[0],
                        "roleArn": "arn:aws:iam::123456789012:role/AnotherValidRole",
                        "parameters": {"duration": "PT10M", "force": True}
                    }
                },
                "targets": {
                    "valid_target": {
                        "resourceType": valid_resource_types[0],
                        "resourceArns": [
                            "arn:aws:ec2:us-east-1:123456789012:instance/i-1234567890abcdef0",
                            "arn:aws:ec2:us-east-1:123456789012:instance/i-0987654321fedcba0"
                        ],
                        "resourceTags": {"Environment": "production"},
                        "selectionMode": "COUNT(2)"
                    }
                },
                # Valid IAM and business logic that should be ignored
                "roleArn": "arn:aws:iam::123456789012:role/FISExperimentRole",
                "description": "Complex experiment with proper IAM setup",
                "stopConditions": [
                    {
                        "source": "aws:cloudwatch:alarm",
                        "value": "arn:aws:cloudwatch:us-east-1:123456789012:alarm/HighCPUAlarm"
                    }
                ],
                "tags": {"Team": "reliability", "Project": "chaos-testing"},
                "experimentOptions": {
                    "accountTargeting": "single-account",
                    "emptyTargetResolutionMode": "skip"
                }
            }
            
            # Validate template
            result = validator.validate_template(template, cache)
            
            # Should be invalid due to invalid action, despite valid IAM/business logic
            assert not result["valid"], "Template should be invalid when it contains invalid actions, regardless of valid IAM/business logic"
            
            # Should report the invalid action
            assert invalid_actions[0] in result["invalid_actions"], f"Should report invalid action '{invalid_actions[0]}'"
            
            # Should have error message for the invalid action
            invalid_action_error_found = any(
                invalid_actions[0] in error and "Invalid action ID" in error
                for error in result["errors"]
            )
            assert invalid_action_error_found, f"Should have specific error for invalid action '{invalid_actions[0]}'"
            
            # Should NOT have errors about IAM, ARNs, or business logic
            iam_business_errors = [
                error for error in result["errors"]
                if any(keyword in error.lower() for keyword in ['role', 'arn', 'permission', 'tag', 'alarm', 'duration', 'percentage'])
                and "Invalid action ID" not in error and "Invalid resource type" not in error
            ]
            assert len(iam_business_errors) == 0, f"Should not validate IAM/business logic, but found errors: {iam_business_errors}"
    
    @given(
        region=st.sampled_from(['us-east-1', 'us-west-2', 'eu-west-1']),
        valid_actions=st.lists(
            st.text(min_size=5, max_size=50, alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd'), whitelist_characters=':_-')),
            min_size=1,
            max_size=2,
            unique=True
        ),
        valid_resource_types=st.lists(
            st.text(min_size=5, max_size=50, alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd'), whitelist_characters=':_-')),
            min_size=1,
            max_size=2,
            unique=True
        )
    )
    @settings(max_examples=50)
    def test_validation_ignores_malformed_iam_and_business_logic_fields(
        self,
        region: str,
        valid_actions: List[str],
        valid_resource_types: List[str]
    ):
        """Property 5: For any template with malformed IAM/ARN/business logic fields but valid actions/resources,
        validation should succeed by ignoring the malformed non-validated fields.
        
        **Feature: aws-chaos-engineering-kiro-power, Property 5: Validation Scope Limitation**
        **Validates: Requirements 3.4**
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
            
            # Create template with valid actions/resources but malformed IAM/business logic
            template = {
                "actions": {
                    "action1": {
                        "actionId": valid_actions[0],
                        # Malformed IAM fields that should be ignored
                        "roleArn": "not-a-valid-arn-format",
                        "invalidField": {"nested": "invalid", "structure": True},
                        "parameters": "this-should-be-a-dict-but-is-string",
                        "duration": -999,  # Invalid duration
                        "percentage": 150  # Invalid percentage > 100
                    }
                },
                "targets": {
                    "target1": {
                        "resourceType": valid_resource_types[0],
                        # Malformed resource fields that should be ignored
                        "resourceArns": "should-be-list-but-is-string",
                        "resourceTags": ["should", "be", "dict", "but", "is", "list"],
                        "selectionMode": {"invalid": "structure"},
                        "filters": "malformed-filter-structure"
                    }
                },
                # Malformed top-level fields that should be ignored
                "roleArn": 12345,  # Should be string
                "description": {"should": "be", "string": True},
                "stopConditions": "malformed-stop-conditions",
                "tags": ["should", "be", "dict"],
                "experimentOptions": "invalid-options-format"
            }
            
            # Validate template
            result = validator.validate_template(template, cache)
            
            # Should be valid because actions and resource types are valid
            # (malformed IAM/business logic fields should be ignored)
            assert result["valid"], f"Template should be valid when actions/resources are valid, even with malformed IAM/business logic. Errors: {result.get('errors', [])}"
            
            # Should not report any invalid actions or resource types
            assert result["invalid_actions"] == [], "Should not report invalid actions when actions are valid"
            assert result["invalid_resource_types"] == [], "Should not report invalid resource types when resource types are valid"
            
            # Should not have validation errors about malformed IAM/business logic fields
            malformed_field_errors = [
                error for error in result["errors"]
                if any(keyword in error.lower() for keyword in ['malformed', 'invalid field', 'format', 'structure', 'duration', 'percentage'])
                and "Invalid action ID" not in error and "Invalid resource type" not in error
            ]
            assert len(malformed_field_errors) == 0, f"Should not validate malformed IAM/business logic fields, but found errors: {malformed_field_errors}"
    
    @given(
        region=st.sampled_from(['us-east-1', 'us-west-2', 'eu-west-1']),
        valid_actions=st.lists(
            st.text(min_size=5, max_size=50, alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd'), whitelist_characters=':_-')),
            min_size=1,
            max_size=2,
            unique=True
        ),
        valid_resource_types=st.lists(
            st.text(min_size=5, max_size=50, alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd'), whitelist_characters=':_-')),
            min_size=1,
            max_size=2,
            unique=True
        )
    )
    @settings(max_examples=30)
    def test_validation_scope_warning_always_present(
        self,
        region: str,
        valid_actions: List[str],
        valid_resource_types: List[str]
    ):
        """Property 5: For any template validation, the result should include a warning about validation scope limitations.
        
        **Feature: aws-chaos-engineering-kiro-power, Property 5: Validation Scope Limitation**
        **Validates: Requirements 3.4**
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
            
            # Create simple template with just actions and resource types
            template = {
                "actions": {
                    "action1": {"actionId": valid_actions[0]}
                },
                "targets": {
                    "target1": {"resourceType": valid_resource_types[0]}
                }
            }
            
            # Validate template
            result = validator.validate_template(template, cache)
            
            # Should always include scope limitation warning
            scope_warning_found = any(
                "Validation covers only action IDs and resource types" in warning or
                "IAM permissions, ARNs, and business logic are not validated" in warning
                for warning in result["warnings"]
            )
            assert scope_warning_found, f"Should always include validation scope limitation warning. Warnings: {result.get('warnings', [])}"
            
            # The warning should be informative and clear
            scope_warnings = [
                warning for warning in result["warnings"]
                if "IAM permissions, ARNs, and business logic are not validated" in warning
            ]
            assert len(scope_warnings) > 0, "Should have specific warning about what is NOT validated"