"""Property-based tests for cache management.

Tests Property 2: Cache Management
Validates: Requirements 2.1, 2.3, 2.4
"""

import json
import tempfile
import time
from pathlib import Path
from datetime import datetime, timezone
import pytest
from hypothesis import given, strategies as st, settings, HealthCheck
from typing import Dict, Any

from aws_chaos_engineering.fis_cache import FISCache


class TestCacheManagement:
    """Property-based tests for cache management functionality."""
    
    @given(
        region=st.sampled_from(['us-east-1', 'us-west-2', 'eu-west-1', 'test-region']),
        fis_actions_count=st.integers(min_value=0, max_value=5),
        resource_types_count=st.integers(min_value=0, max_value=5)
    )
    @settings(max_examples=100)
    def test_cache_stores_and_retrieves_data_locally(
        self, 
        region: str, 
        fis_actions_count: int,
        resource_types_count: int
    ):
        """Property 2: For any FIS data, the server should store it locally and retrieve it correctly.
        
        **Feature: aws-chaos-engineering-kiro-power, Property 2: Cache Management**
        **Validates: Requirements 2.1**
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create FIS cache with temporary directory
            cache = FISCache(cache_dir=temp_dir)
            
            # Generate test data
            fis_actions = [
                {"id": f"aws:service:action-{i}", "description": f"Test action {i}"}
                for i in range(fis_actions_count)
            ]
            resource_types = [
                {"type": f"aws:service:resource-{i}", "description": f"Test resource {i}"}
                for i in range(resource_types_count)
            ]
            
            fis_data = {
                "fis_actions": fis_actions,
                "resource_types": resource_types
            }
            
            # Store data in cache
            success, message, timestamp = cache.update_cache(region, fis_data)
            assert success, f"Cache update should succeed: {message}"
            assert timestamp is not None, "Should return timestamp"
            
            # Retrieve data from cache
            cached_data = cache.get_cached_data(region)
            assert cached_data is not None, "Should retrieve cached data"
            
            # Verify data integrity
            assert cached_data["region"] == region, "Should preserve region"
            assert cached_data["fis_actions"] == fis_actions, "Should preserve fis_actions"
            assert cached_data["resource_types"] == resource_types, "Should preserve resource_types"
            assert "last_updated" in cached_data, "Should include last_updated timestamp"
            assert "cache_ttl_hours" in cached_data, "Should include cache_ttl_hours"
            
            # Verify cache file exists locally
            cache_file = cache._get_cache_file_path(region)
            assert cache_file.exists(), "Cache file should exist locally"
    
    @given(
        region=st.sampled_from(['us-east-1', 'us-west-2', 'eu-west-1', 'test-region']),
        initial_actions_count=st.integers(min_value=0, max_value=3),
        updated_actions_count=st.integers(min_value=0, max_value=3)
    )
    @settings(max_examples=100)
    def test_cache_updates_immediately_with_fresh_data(
        self, 
        region: str, 
        initial_actions_count: int,
        updated_actions_count: int
    ):
        """Property 2: For any fresh data provided, the server should update cache immediately.
        
        **Feature: aws-chaos-engineering-kiro-power, Property 2: Cache Management**
        **Validates: Requirements 2.3**
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create FIS cache with temporary directory
            cache = FISCache(cache_dir=temp_dir)
            
            # Create initial data
            initial_data = {
                "fis_actions": [
                    {"id": f"aws:initial:action-{i}", "description": f"Initial action {i}"}
                    for i in range(initial_actions_count)
                ],
                "resource_types": [
                    {"type": f"aws:initial:resource-{i}", "description": f"Initial resource {i}"}
                    for i in range(initial_actions_count)
                ]
            }
            
            # Store initial data
            success1, message1, timestamp1 = cache.update_cache(region, initial_data)
            assert success1, f"Initial cache update should succeed: {message1}"
            
            # Small delay to ensure timestamp difference
            time.sleep(0.01)
            
            # Create updated data
            updated_data = {
                "fis_actions": [
                    {"id": f"aws:updated:action-{i}", "description": f"Updated action {i}"}
                    for i in range(updated_actions_count)
                ],
                "resource_types": [
                    {"type": f"aws:updated:resource-{i}", "description": f"Updated resource {i}"}
                    for i in range(updated_actions_count)
                ]
            }
            
            # Update with fresh data immediately
            success2, message2, timestamp2 = cache.update_cache(region, updated_data)
            assert success2, f"Fresh data update should succeed: {message2}"
            assert timestamp2 is not None, "Should return new timestamp"
            assert timestamp2 != timestamp1, "Should have different timestamp after update"
            
            # Verify immediate update - cache should contain new data
            cached_data = cache.get_cached_data(region)
            assert cached_data is not None, "Should retrieve updated data immediately"
            assert cached_data["fis_actions"] == updated_data["fis_actions"], "Should contain updated fis_actions"
            assert cached_data["resource_types"] == updated_data["resource_types"], "Should contain updated resource_types"
            assert cached_data["last_updated"] == timestamp2, "Should have updated timestamp"
            
            # Verify cache status is fresh after immediate update
            cache_status = cache.get_cache_status(region)
            assert cache_status == "fresh", "Cache should be fresh immediately after update"
    
    @given(
        region=st.sampled_from(['us-east-1', 'us-west-2', 'eu-west-1', 'test-region']),
        fis_actions_count=st.integers(min_value=0, max_value=5),
        resource_types_count=st.integers(min_value=0, max_value=5)
    )
    @settings(max_examples=100)
    def test_cache_provides_structured_data_for_agent_prompts(
        self, 
        region: str, 
        fis_actions_count: int,
        resource_types_count: int
    ):
        """Property 2: For any FIS data request, the server should provide structured data for agent system prompts.
        
        **Feature: aws-chaos-engineering-kiro-power, Property 2: Cache Management**
        **Validates: Requirements 2.4**
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create FIS cache with temporary directory
            cache = FISCache(cache_dir=temp_dir)
            
            # Generate test data
            fis_actions = [
                {"id": f"aws:service:action-{i}", "description": f"Test action {i}"}
                for i in range(fis_actions_count)
            ]
            resource_types = [
                {"type": f"aws:service:resource-{i}", "description": f"Test resource {i}"}
                for i in range(resource_types_count)
            ]
            
            fis_data = {
                "fis_actions": fis_actions,
                "resource_types": resource_types
            }
            
            # Store data in cache
            success, message, timestamp = cache.update_cache(region, fis_data)
            assert success, f"Cache update should succeed: {message}"
            
            # Test structured data format for agent system prompts
            cached_data = cache.get_cached_data(region)
            assert cached_data is not None, "Should retrieve cached data"
            
            # Verify structured response format required for agent system prompts
            assert isinstance(cached_data, dict), "Cached data should be a dictionary"
            assert "fis_actions" in cached_data, "Should have fis_actions field"
            assert "resource_types" in cached_data, "Should have resource_types field"
            assert "last_updated" in cached_data, "Should have last_updated field"
            assert "region" in cached_data, "Should have region field"
            assert "cache_ttl_hours" in cached_data, "Should have cache_ttl_hours field"
            
            # Verify data content matches input data
            assert cached_data["fis_actions"] == fis_actions, "Should return cached fis_actions"
            assert cached_data["resource_types"] == resource_types, "Should return cached resource_types"
            assert cached_data["region"] == region, "Should return correct region"
            assert cached_data["last_updated"] == timestamp, "Should return correct timestamp"
            assert cached_data["cache_ttl_hours"] == 24, "Should have 24-hour TTL"
            
            # Verify each action has required structure for agent prompts
            for action in cached_data["fis_actions"]:
                assert isinstance(action, dict), "Each action should be a dictionary"
                assert "id" in action, "Each action should have an id field"
                assert "description" in action, "Each action should have a description field"
                assert isinstance(action["id"], str), "Action id should be string"
                assert isinstance(action["description"], str), "Action description should be string"
            
            # Verify each resource type has required structure for agent prompts
            for resource_type in cached_data["resource_types"]:
                assert isinstance(resource_type, dict), "Each resource type should be a dictionary"
                assert "type" in resource_type, "Each resource type should have a type field"
                assert "description" in resource_type, "Each resource type should have a description field"
                assert isinstance(resource_type["type"], str), "Resource type should be string"
                assert isinstance(resource_type["description"], str), "Resource description should be string"
    
    @given(
        regions=st.lists(
            st.sampled_from(['us-east-1', 'us-west-2', 'eu-west-1', 'ap-southeast-1']), 
            min_size=2, 
            max_size=3, 
            unique=True
        )
    )
    @settings(max_examples=30, suppress_health_check=[HealthCheck.too_slow])
    def test_cache_isolates_data_by_region(
        self, 
        regions: list[str]
    ):
        """Property 2: For any regions, cache should isolate data correctly per region.
        
        **Feature: aws-chaos-engineering-kiro-power, Property 2: Cache Management**
        **Validates: Requirements 2.1**
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create FIS cache with temporary directory
            cache = FISCache(cache_dir=temp_dir)
            
            # Store different data for each region
            region_data = {}
            for i, region in enumerate(regions):
                # Create region-specific data (ensure it's different for each region)
                region_specific_data = {
                    "fis_actions": [
                        {"id": f"aws:service:action-{region}-{j}", "description": f"Action {j} for {region}"}
                        for j in range(2)  # Always create 2 items to ensure differences
                    ],
                    "resource_types": [
                        {"type": f"aws:service:resource-{region}-{j}", "description": f"Resource {j} for {region}"}
                        for j in range(2)  # Always create 2 items to ensure differences
                    ]
                }
                region_data[region] = region_specific_data
                
                # Store in cache
                success, message, timestamp = cache.update_cache(region, region_specific_data)
                assert success, f"Cache update should succeed for region {region}: {message}"
            
            # Verify data isolation - each region should have its own data
            for region in regions:
                cached_data = cache.get_cached_data(region)
                assert cached_data is not None, f"Should retrieve data for region {region}"
                assert cached_data["region"] == region, f"Should preserve region {region}"
                
                # Verify region-specific data
                expected_data = region_data[region]
                assert cached_data["fis_actions"] == expected_data["fis_actions"], f"Should have correct fis_actions for {region}"
                assert cached_data["resource_types"] == expected_data["resource_types"], f"Should have correct resource_types for {region}"
                
                # Verify no cross-contamination
                for other_region in regions:
                    if other_region != region:
                        other_expected = region_data[other_region]
                        assert cached_data["fis_actions"] != other_expected["fis_actions"], f"Region {region} should not have data from {other_region}"
                        assert cached_data["resource_types"] != other_expected["resource_types"], f"Region {region} should not have data from {other_region}"
    
    @given(
        region=st.sampled_from(['us-east-1', 'us-west-2', 'eu-west-1', 'test-region'])
    )
    @settings(max_examples=50)
    def test_cache_handles_empty_data_correctly(
        self, 
        region: str
    ):
        """Property 2: For empty FIS data, cache should handle it correctly and provide structured response.
        
        **Feature: aws-chaos-engineering-kiro-power, Property 2: Cache Management**
        **Validates: Requirements 2.1, 2.3, 2.4**
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create FIS cache with temporary directory
            cache = FISCache(cache_dir=temp_dir)
            
            # Test with empty data
            empty_data = {
                "fis_actions": [],
                "resource_types": []
            }
            
            # Store empty data
            success, message, timestamp = cache.update_cache(region, empty_data)
            assert success, f"Cache should handle empty data: {message}"
            
            # Verify retrieval of empty data
            cached_data = cache.get_cached_data(region)
            assert cached_data is not None, "Should retrieve empty data"
            assert cached_data["fis_actions"] == [], "Should preserve empty fis_actions list"
            assert cached_data["resource_types"] == [], "Should preserve empty resource_types list"
            assert cached_data["region"] == region, "Should preserve region"
            assert "last_updated" in cached_data, "Should include timestamp even for empty data"
            
            # Verify structured response format (testing what would be provided to agent prompts)
            assert cached_data["fis_actions"] == [], "Should return empty fis_actions list"
            assert cached_data["resource_types"] == [], "Should return empty resource_types list"
            assert cached_data["region"] == region, "Should return correct region"
            assert cached_data["last_updated"] == timestamp, "Should return correct timestamp"
            assert cached_data["cache_ttl_hours"] == 24, "Should have 24-hour TTL even for empty data"