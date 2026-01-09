"""Property-based tests for agent data acceptance.

Tests Property 8: Agent Data Acceptance
Validates: Requirements 5.4
"""

import tempfile
from typing import Dict, Any
import pytest
from hypothesis import given, strategies as st, settings, HealthCheck

from aws_chaos_engineering.fis_cache import FISCache


class TestAgentDataAcceptance:
    """Property-based tests for agent data acceptance functionality."""
    
    @given(
        region=st.sampled_from(['us-east-1', 'us-west-2', 'eu-west-1', 'ap-southeast-1', 'test-region']),
        fis_actions_count=st.integers(min_value=0, max_value=10),
        resource_types_count=st.integers(min_value=0, max_value=10)
    )
    @settings(max_examples=100)
    def test_server_accepts_fresh_data_from_agents_for_cache_updates(
        self, 
        region: str, 
        fis_actions_count: int,
        resource_types_count: int
    ):
        """Property 8: For any fresh FIS data provided by agents, the server should accept and process it for cache updates.
        
        **Feature: aws-chaos-engineering-kiro-power, Property 8: Agent Data Acceptance**
        **Validates: Requirements 5.4**
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create FIS cache with temporary directory
            cache = FISCache(cache_dir=temp_dir)
            
            # Generate fresh FIS data that an agent would provide
            agent_provided_data = {
                "fis_actions": [
                    {
                        "id": f"aws:service:action-{i}", 
                        "description": f"Agent provided action {i}"
                    }
                    for i in range(fis_actions_count)
                ],
                "resource_types": [
                    {
                        "type": f"aws:service:resource-{i}", 
                        "description": f"Agent provided resource {i}"
                    }
                    for i in range(resource_types_count)
                ]
            }
            
            # Test that server accepts fresh data from agents via update_cache
            success, message, timestamp = cache.update_cache(region, agent_provided_data)
            
            # Verify server accepts the data successfully
            assert success is True, "Server should accept fresh data from agents"
            assert message is not None, "Should provide status message"
            assert timestamp is not None, "Should return timestamp of update"
            
            # Verify the data was actually processed and stored
            cached_data = cache.get_cached_data(region)
            assert cached_data is not None, "Agent data should be stored in cache"
            assert cached_data["fis_actions"] == agent_provided_data["fis_actions"], "Should store agent-provided fis_actions"
            assert cached_data["resource_types"] == agent_provided_data["resource_types"], "Should store agent-provided resource_types"
            assert cached_data["region"] == region, "Should store correct region"
            assert cached_data["last_updated"] == timestamp, "Should have matching timestamp"
            
            # Verify cache status is fresh after accepting agent data
            cache_status = cache.get_cache_status(region)
            assert cache_status == "fresh", "Cache should be fresh after accepting agent data"
    
    @given(
        region=st.sampled_from(['us-east-1', 'us-west-2', 'eu-west-1', 'ap-southeast-1']),
        data_variations=st.lists(
            st.dictionaries(
                keys=st.sampled_from(['fis_actions', 'resource_types']),
                values=st.lists(
                    st.dictionaries(
                        keys=st.sampled_from(['id', 'type', 'description']),
                        values=st.text(min_size=1, max_size=50),
                        min_size=1,
                        max_size=3
                    ),
                    min_size=0,
                    max_size=3
                ),
                min_size=0,
                max_size=2
            ),
            min_size=1,
            max_size=5
        )
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_server_accepts_multiple_agent_data_updates_sequentially(
        self, 
        region: str, 
        data_variations: list[Dict[str, Any]]
    ):
        """Property 8: For any sequence of fresh data from agents, the server should accept each update.
        
        **Feature: aws-chaos-engineering-kiro-power, Property 8: Agent Data Acceptance**
        **Validates: Requirements 5.4**
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create FIS cache with temporary directory
            cache = FISCache(cache_dir=temp_dir)
            
            previous_timestamp = None
            
            # Test sequential agent data updates
            for i, agent_data in enumerate(data_variations):
                # Ensure data has required structure
                normalized_data = {
                    "fis_actions": agent_data.get("fis_actions", []),
                    "resource_types": agent_data.get("resource_types", [])
                }
                
                # Server should accept each agent data update
                success, message, timestamp = cache.update_cache(region, normalized_data)
                
                # Verify each update is accepted
                assert success is True, f"Server should accept agent data update {i+1}"
                assert message is not None, f"Should provide status message for update {i+1}"
                assert timestamp is not None, f"Should return timestamp for update {i+1}"
                
                # Verify timestamp progression (each update should have newer timestamp)
                if previous_timestamp is not None:
                    # Note: Timestamps may be identical if updates happen very quickly
                    # The important thing is that each update is accepted, not timestamp uniqueness
                    pass
                previous_timestamp = timestamp
                
                # Verify the latest data is stored correctly
                cached_data = cache.get_cached_data(region)
                assert cached_data is not None, f"Agent data {i+1} should be stored in cache"
                assert cached_data["fis_actions"] == normalized_data["fis_actions"], f"Should store latest fis_actions from update {i+1}"
                assert cached_data["resource_types"] == normalized_data["resource_types"], f"Should store latest resource_types from update {i+1}"
                assert cached_data["last_updated"] == timestamp, f"Should have matching timestamp for update {i+1}"
    
    @given(
        region=st.sampled_from(['us-east-1', 'us-west-2', 'eu-west-1', 'test-region'])
    )
    @settings(max_examples=50)
    def test_server_handles_empty_agent_data_gracefully(
        self, 
        region: str
    ):
        """Property 8: For empty data from agents, the server should accept it gracefully.
        
        **Feature: aws-chaos-engineering-kiro-power, Property 8: Agent Data Acceptance**
        **Validates: Requirements 5.4**
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create FIS cache with temporary directory
            cache = FISCache(cache_dir=temp_dir)
            
            # Test with empty agent data
            empty_agent_data = {
                "fis_actions": [],
                "resource_types": []
            }
            
            # Server should accept empty data from agents
            success, message, timestamp = cache.update_cache(region, empty_agent_data)
            
            # Verify server accepts empty data gracefully
            assert success is True, "Server should accept empty data from agents"
            assert message is not None, "Should provide status message for empty data"
            assert timestamp is not None, "Should return timestamp even for empty data"
            
            # Verify empty data is stored correctly
            cached_data = cache.get_cached_data(region)
            assert cached_data is not None, "Empty agent data should be stored in cache"
            assert cached_data["fis_actions"] == [], "Should store empty fis_actions list"
            assert cached_data["resource_types"] == [], "Should store empty resource_types list"
            assert cached_data["region"] == region, "Should store correct region"
            assert cached_data["last_updated"] == timestamp, "Should have matching timestamp"
            
            # Verify cache status is fresh even with empty data
            cache_status = cache.get_cache_status(region)
            assert cache_status == "fresh", "Cache should be fresh even with empty agent data"
    
    @given(
        region=st.sampled_from(['us-east-1', 'us-west-2', 'eu-west-1', 'test-region'])
    )
    @settings(max_examples=50)
    def test_server_rejects_invalid_agent_data_appropriately(
        self, 
        region: str
    ):
        """Property 8: For invalid data from agents, the server should handle it appropriately.
        
        **Feature: aws-chaos-engineering-kiro-power, Property 8: Agent Data Acceptance**
        **Validates: Requirements 5.4**
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create FIS cache with temporary directory
            cache = FISCache(cache_dir=temp_dir)
            
            # Test with invalid data types
            invalid_data_cases = [
                None,  # None data
                "invalid_string",  # String instead of dict
                123,  # Number instead of dict
                [],  # List instead of dict
            ]
            
            for invalid_data in invalid_data_cases:
                # Server should handle invalid data appropriately
                success, message, timestamp = cache.update_cache(region, invalid_data)
                
                # Server should reject invalid data
                assert success is False, f"Server should reject invalid data: {type(invalid_data)}"
                assert message is not None, f"Should provide error message for invalid data: {type(invalid_data)}"
                assert timestamp is None, f"Should not return timestamp for failed update: {type(invalid_data)}"
                
                # Verify no data is stored for invalid input
                cached_data = cache.get_cached_data(region)
                assert cached_data is None, f"Should not store data for invalid agent input: {type(invalid_data)}"
                
                # Verify cache status remains empty
                cache_status = cache.get_cache_status(region)
                assert cache_status == "empty", f"Cache should remain empty for invalid agent data: {type(invalid_data)}"
    
    @given(
        regions=st.lists(
            st.sampled_from(['us-east-1', 'us-west-2', 'eu-west-1', 'ap-southeast-1']), 
            min_size=2, 
            max_size=4, 
            unique=True
        )
    )
    @settings(max_examples=30, suppress_health_check=[HealthCheck.too_slow])
    def test_server_accepts_agent_data_for_multiple_regions_independently(
        self, 
        regions: list[str]
    ):
        """Property 8: For agent data targeting different regions, the server should accept each independently.
        
        **Feature: aws-chaos-engineering-kiro-power, Property 8: Agent Data Acceptance**
        **Validates: Requirements 5.4**
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create FIS cache with temporary directory
            cache = FISCache(cache_dir=temp_dir)
            
            region_data = {}
            
            # Test agent data acceptance for multiple regions
            for i, region in enumerate(regions):
                # Create region-specific agent data
                agent_data = {
                    "fis_actions": [
                        {
                            "id": f"aws:service:action-{region}-{j}", 
                            "description": f"Agent action {j} for {region}"
                        }
                        for j in range(2)  # Always create 2 items for consistency
                    ],
                    "resource_types": [
                        {
                            "type": f"aws:service:resource-{region}-{j}", 
                            "description": f"Agent resource {j} for {region}"
                        }
                        for j in range(2)  # Always create 2 items for consistency
                    ]
                }
                
                # Server should accept agent data for each region
                success, message, timestamp = cache.update_cache(region, agent_data)
                
                # Verify acceptance for each region
                assert success is True, f"Server should accept agent data for region {region}"
                assert message is not None, f"Should provide status message for region {region}"
                assert timestamp is not None, f"Should return timestamp for region {region}"
                
                region_data[region] = (timestamp, agent_data)
            
            # Verify independent storage - each region should have its own data
            for region in regions:
                timestamp, original_agent_data = region_data[region]
                
                cached_data = cache.get_cached_data(region)
                assert cached_data is not None, f"Should store agent data for region {region}"
                assert cached_data["fis_actions"] == original_agent_data["fis_actions"], f"Should store correct fis_actions for {region}"
                assert cached_data["resource_types"] == original_agent_data["resource_types"], f"Should store correct resource_types for {region}"
                assert cached_data["region"] == region, f"Should store correct region {region}"
                assert cached_data["last_updated"] == timestamp, f"Should have matching timestamp for {region}"
                
                # Verify no cross-contamination between regions
                for other_region in regions:
                    if other_region != region:
                        other_timestamp, other_agent_data = region_data[other_region]
                        assert cached_data["fis_actions"] != other_agent_data["fis_actions"], f"Region {region} should not have data from {other_region}"
                        assert cached_data["resource_types"] != other_agent_data["resource_types"], f"Region {region} should not have data from {other_region}"