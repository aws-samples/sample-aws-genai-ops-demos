"""Property-based tests for cache TTL behavior.

Tests Property 3: Cache TTL Behavior
Validates: Requirements 2.2
"""

import json
import tempfile
import time
import os
from pathlib import Path
from datetime import datetime, timezone
import pytest
from hypothesis import given, strategies as st, settings
from typing import Dict, Any

from aws_chaos_engineering.fis_cache import FISCache


class TestCacheTTLBehavior:
    """Property-based tests for cache TTL behavior."""
    
    @given(
        region=st.sampled_from(['us-east-1', 'us-west-2', 'eu-west-1', 'test-region']),
        cache_age_hours=st.floats(min_value=24.1, max_value=48.0)  # Stale cache (older than 24 hours)
    )
    @settings(max_examples=100)
    def test_stale_cache_returns_refresh_instruction(
        self, 
        region: str, 
        cache_age_hours: float
    ):
        """Property 3: For any cached data older than 24 hours, the server should instruct agents to refresh via AWS MCP server.
        
        **Feature: aws-chaos-engineering-kiro-power, Property 3: Cache TTL Behavior**
        **Validates: Requirements 2.2**
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create FIS cache with temporary directory
            cache = FISCache(cache_dir=temp_dir)
            cache_file = cache._get_cache_file_path(region)
            
            # Create valid cache data
            valid_cache_data = {
                "fis_actions": [
                    {"id": "aws:ec2:stop-instances", "description": "Stop EC2 instances"},
                    {"id": "aws:rds:failover-db-cluster", "description": "Failover RDS cluster"}
                ],
                "resource_types": [
                    {"type": "aws:ec2:instance", "description": "EC2 instances"},
                    {"type": "aws:rds:cluster", "description": "RDS clusters"}
                ],
                "last_updated": datetime.now(timezone.utc).isoformat(),
                "region": region,
                "cache_ttl_hours": 24
            }
            
            # Write cache file
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump(valid_cache_data, f, indent=2)
            
            # Artificially age the cache file by modifying its timestamp
            cache_age_seconds = cache_age_hours * 3600
            current_time = time.time()
            old_time = current_time - cache_age_seconds
            os.utime(cache_file, (old_time, old_time))
            
            # Test cache status - should be stale
            cache_status = cache.get_cache_status(region)
            assert cache_status == "stale", f"Cache older than 24 hours should be stale, got: {cache_status}"
            
            # Test that cache is not considered fresh
            assert not cache._is_cache_fresh(cache_file), "Cache older than 24 hours should not be fresh"
            
            # The cached data should still be retrievable (for fallback scenarios)
            # but the status indicates it needs refresh
            cached_data = cache.get_cached_data(region)
            assert cached_data is not None, "Stale cache should still return data for fallback"
            assert cached_data["region"] == region, "Cached data should preserve region"
            assert "fis_actions" in cached_data, "Cached data should contain fis_actions"
            assert "resource_types" in cached_data, "Cached data should contain resource_types"
    
    @given(
        region=st.sampled_from(['us-east-1', 'us-west-2', 'eu-west-1', 'test-region']),
        cache_age_hours=st.floats(min_value=0.0, max_value=23.9)  # Fresh cache (less than 24 hours)
    )
    @settings(max_examples=100)
    def test_fresh_cache_returns_data_directly(
        self, 
        region: str, 
        cache_age_hours: float
    ):
        """Property 3: For any cached data less than 24 hours old, the server should return cached data directly.
        
        **Feature: aws-chaos-engineering-kiro-power, Property 3: Cache TTL Behavior**
        **Validates: Requirements 2.2**
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create FIS cache with temporary directory
            cache = FISCache(cache_dir=temp_dir)
            cache_file = cache._get_cache_file_path(region)
            
            # Create valid cache data
            valid_cache_data = {
                "fis_actions": [
                    {"id": "aws:ec2:stop-instances", "description": "Stop EC2 instances"},
                    {"id": "aws:rds:failover-db-cluster", "description": "Failover RDS cluster"}
                ],
                "resource_types": [
                    {"type": "aws:ec2:instance", "description": "EC2 instances"},
                    {"type": "aws:rds:cluster", "description": "RDS clusters"}
                ],
                "last_updated": datetime.now(timezone.utc).isoformat(),
                "region": region,
                "cache_ttl_hours": 24
            }
            
            # Write cache file
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump(valid_cache_data, f, indent=2)
            
            # Artificially age the cache file (but keep it fresh)
            cache_age_seconds = cache_age_hours * 3600
            current_time = time.time()
            fresh_time = current_time - cache_age_seconds
            os.utime(cache_file, (fresh_time, fresh_time))
            
            # Test cache status - should be fresh
            cache_status = cache.get_cache_status(region)
            assert cache_status == "fresh", f"Cache less than 24 hours old should be fresh, got: {cache_status}"
            
            # Test that cache is considered fresh
            assert cache._is_cache_fresh(cache_file), "Cache less than 24 hours old should be fresh"
            
            # The cached data should be retrievable
            cached_data = cache.get_cached_data(region)
            assert cached_data is not None, "Fresh cache should return data"
            assert cached_data["region"] == region, "Cached data should preserve region"
            assert "fis_actions" in cached_data, "Cached data should contain fis_actions"
            assert "resource_types" in cached_data, "Cached data should contain resource_types"
            assert len(cached_data["fis_actions"]) == 2, "Should preserve all fis_actions"
            assert len(cached_data["resource_types"]) == 2, "Should preserve all resource_types"
    
    @given(
        region=st.sampled_from(['us-east-1', 'us-west-2', 'eu-west-1', 'test-region'])
    )
    @settings(max_examples=50)
    def test_nonexistent_cache_returns_empty_status(
        self, 
        region: str
    ):
        """Property 3: For any region with no cached data, the server should return empty status.
        
        **Feature: aws-chaos-engineering-kiro-power, Property 3: Cache TTL Behavior**
        **Validates: Requirements 2.2**
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create FIS cache with temporary directory (no cache files)
            cache = FISCache(cache_dir=temp_dir)
            
            # Test cache status - should be empty
            cache_status = cache.get_cache_status(region)
            assert cache_status == "empty", f"Non-existent cache should be empty, got: {cache_status}"
            
            # The cached data should be None
            cached_data = cache.get_cached_data(region)
            assert cached_data is None, "Non-existent cache should return None"
    
    @given(
        region=st.sampled_from(['us-east-1', 'us-west-2', 'eu-west-1', 'test-region']),
        ttl_boundary_offset=st.floats(min_value=-0.1, max_value=0.1, allow_subnormal=False)  # Test around 24-hour boundary
    )
    @settings(max_examples=50)
    def test_ttl_boundary_behavior(
        self, 
        region: str, 
        ttl_boundary_offset: float
    ):
        """Property 3: Cache TTL behavior should be consistent around the 24-hour boundary.
        
        **Feature: aws-chaos-engineering-kiro-power, Property 3: Cache TTL Behavior**
        **Validates: Requirements 2.2**
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create FIS cache with temporary directory
            cache = FISCache(cache_dir=temp_dir)
            cache_file = cache._get_cache_file_path(region)
            
            # Create valid cache data
            valid_cache_data = {
                "fis_actions": [{"id": "test-action", "description": "Test action"}],
                "resource_types": [{"type": "test-resource", "description": "Test resource"}],
                "last_updated": datetime.now(timezone.utc).isoformat(),
                "region": region,
                "cache_ttl_hours": 24
            }
            
            # Write cache file
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump(valid_cache_data, f, indent=2)
            
            # Set cache age to exactly 24 hours + offset
            cache_age_seconds = (24.0 + ttl_boundary_offset) * 3600
            current_time = time.time()
            boundary_time = current_time - cache_age_seconds
            os.utime(cache_file, (boundary_time, boundary_time))
            
            # Test cache status
            cache_status = cache.get_cache_status(region)
            is_fresh = cache._is_cache_fresh(cache_file)
            
            # Verify consistent behavior around boundary
            # Use a small epsilon to handle floating-point precision issues
            epsilon = 1e-6  # Increased epsilon to handle floating-point precision
            if ttl_boundary_offset < -epsilon:  # Clearly less than 24 hours
                assert cache_status == "fresh", f"Cache just under 24 hours should be fresh, got: {cache_status} (offset: {ttl_boundary_offset})"
                assert is_fresh, f"Cache just under 24 hours should be considered fresh (offset: {ttl_boundary_offset})"
            elif ttl_boundary_offset > epsilon:  # Clearly more than 24 hours
                assert cache_status == "stale", f"Cache just over 24 hours should be stale, got: {cache_status} (offset: {ttl_boundary_offset})"
                assert not is_fresh, f"Cache just over 24 hours should not be considered fresh (offset: {ttl_boundary_offset})"
            # For values very close to zero (within epsilon), we don't make strict assertions
            # as the behavior may vary due to floating-point precision
    
    @given(
        region=st.sampled_from(['us-east-1', 'us-west-2', 'eu-west-1', 'test-region']),
        fis_actions_count=st.integers(min_value=0, max_value=10),
        resource_types_count=st.integers(min_value=0, max_value=10)
    )
    @settings(max_examples=50)
    def test_cache_update_resets_ttl(
        self, 
        region: str, 
        fis_actions_count: int, 
        resource_types_count: int
    ):
        """Property 3: Updating cache should reset the TTL timer regardless of data size.
        
        **Feature: aws-chaos-engineering-kiro-power, Property 3: Cache TTL Behavior**
        **Validates: Requirements 2.2**
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create FIS cache with temporary directory
            cache = FISCache(cache_dir=temp_dir)
            
            # Generate test data
            fis_actions = [
                {"id": f"test-action-{i}", "description": f"Test action {i}"}
                for i in range(fis_actions_count)
            ]
            resource_types = [
                {"type": f"test-resource-{i}", "description": f"Test resource {i}"}
                for i in range(resource_types_count)
            ]
            
            fresh_data = {
                "fis_actions": fis_actions,
                "resource_types": resource_types
            }
            
            # Update cache with fresh data
            success, message, timestamp = cache.update_cache(region, fresh_data)
            assert success, f"Cache update should succeed: {message}"
            assert timestamp is not None, "Should return timestamp"
            
            # Immediately check cache status - should be fresh
            cache_status = cache.get_cache_status(region)
            assert cache_status == "fresh", f"Newly updated cache should be fresh, got: {cache_status}"
            
            # Verify cached data
            cached_data = cache.get_cached_data(region)
            assert cached_data is not None, "Updated cache should return data"
            assert len(cached_data["fis_actions"]) == fis_actions_count, "Should preserve fis_actions count"
            assert len(cached_data["resource_types"]) == resource_types_count, "Should preserve resource_types count"
            
            # Verify TTL fields are set correctly
            assert cached_data["cache_ttl_hours"] == 24, "Should set TTL to 24 hours"
            assert "last_updated" in cached_data, "Should include last_updated timestamp"
    
    def test_cache_ttl_constant_value(self):
        """Property 3: Cache TTL should always be 24 hours (86400 seconds).
        
        **Feature: aws-chaos-engineering-kiro-power, Property 3: Cache TTL Behavior**
        **Validates: Requirements 2.2**
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = FISCache(cache_dir=temp_dir)
            
            # Verify TTL constant
            expected_ttl_seconds = 24 * 60 * 60  # 24 hours in seconds
            assert cache.cache_ttl == expected_ttl_seconds, f"Cache TTL should be 24 hours ({expected_ttl_seconds} seconds), got: {cache.cache_ttl}"
            
            # Test with actual cache update
            test_data = {
                "fis_actions": [{"id": "test", "description": "test"}],
                "resource_types": [{"type": "test", "description": "test"}]
            }
            
            success, message, timestamp = cache.update_cache("us-east-1", test_data)
            assert success, f"Cache update should succeed: {message}"
            
            # Verify cached data has correct TTL
            cached_data = cache.get_cached_data("us-east-1")
            assert cached_data is not None, "Should return cached data"
            assert cached_data["cache_ttl_hours"] == 24, "Cached data should indicate 24-hour TTL"