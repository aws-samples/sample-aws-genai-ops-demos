"""FIS actions cache management.

This module handles local file-based caching of AWS FIS actions and resource types
with 24-hour TTL logic and error handling.
"""

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, Tuple
import logging

logger = logging.getLogger(__name__)


class FISCache:
    """Manages local file-based caching of FIS actions and resource types."""
    
    def __init__(self, cache_dir: Optional[str] = None):
        """Initialize the FIS cache.
        
        Args:
            cache_dir: Custom cache directory path. If None, uses user's cache directory.
        """
        if cache_dir:
            self.cache_dir = Path(cache_dir)
        else:
            # Use user's cache directory with fallback for Windows Store Python
            if os.name == 'nt':  # Windows
                # Try user profile first to avoid Windows Store Python sandbox
                user_profile = os.path.expanduser('~')
                cache_base = os.path.join(user_profile, '.aws-chaos-engineering')
                
                # Fallback to LOCALAPPDATA if user profile isn't writable
                try:
                    test_path = Path(cache_base)
                    test_path.mkdir(parents=True, exist_ok=True)
                    # Test write permissions
                    test_file = test_path / 'test_write.tmp'
                    test_file.write_text('test')
                    test_file.unlink()
                    cache_base = cache_base
                except (PermissionError, OSError):
                    # Fall back to LOCALAPPDATA
                    cache_base = os.environ.get('LOCALAPPDATA', os.path.expanduser('~\\AppData\\Local'))
                    cache_base = os.path.join(cache_base, 'aws-chaos-engineering')
            else:  # Unix-like systems
                cache_base = os.environ.get('XDG_CACHE_HOME', os.path.expanduser('~/.cache'))
                cache_base = os.path.join(cache_base, 'aws-chaos-engineering')
            
            self.cache_dir = Path(cache_base)
        
        # Ensure cache directory exists
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        # Cache TTL in seconds (24 hours)
        self.cache_ttl = 24 * 60 * 60
    
    def _get_cache_file_path(self, region: str) -> Path:
        """Get the cache file path for a specific region.
        
        Args:
            region: AWS region name
            
        Returns:
            Path to the cache file for the region
        """
        return self.cache_dir / f"fis_actions_{region}.json"
    
    def _is_cache_fresh(self, cache_file: Path) -> bool:
        """Check if cache file is fresh (within TTL).
        
        Args:
            cache_file: Path to the cache file
            
        Returns:
            True if cache is fresh, False if stale or doesn't exist
        """
        try:
            if not cache_file.exists():
                return False
            
            # Check file modification time
            file_mtime = cache_file.stat().st_mtime
            current_time = time.time()
            
            return (current_time - file_mtime) < self.cache_ttl
            
        except Exception as e:
            logger.warning(f"Error checking cache freshness: {e}")
            return False
    
    def get_cache_status(self, region: str) -> str:
        """Get the status of the cache for a region.
        
        Args:
            region: AWS region name
            
        Returns:
            Cache status: 'fresh', 'stale', or 'empty'
        """
        cache_file = self._get_cache_file_path(region)
        
        if not cache_file.exists():
            return "empty"
        
        if self._is_cache_fresh(cache_file):
            return "fresh"
        else:
            return "stale"
    
    def get_cached_data(self, region: str) -> Optional[Dict[str, Any]]:
        """Get cached FIS data for a region.
        
        Args:
            region: AWS region name
            
        Returns:
            Cached data dictionary or None if no valid cache exists
        """
        cache_file = self._get_cache_file_path(region)
        
        try:
            if not cache_file.exists():
                logger.info(f"No cache file found for region {region}")
                return None
            
            with open(cache_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # Validate cache structure
            if not isinstance(data, dict):
                logger.warning(f"Invalid cache structure for region {region}")
                return None
            
            required_fields = ['fis_actions', 'resource_types', 'last_updated', 'region']
            if not all(field in data for field in required_fields):
                logger.warning(f"Missing required fields in cache for region {region}")
                return None
            
            return data
            
        except json.JSONDecodeError as e:
            logger.error(f"Cache corruption detected for region {region}: {e}")
            # Remove corrupted cache file
            try:
                cache_file.unlink()
                logger.info(f"Removed corrupted cache file for region {region}")
            except OSError as unlink_error:
                logger.warning(f"Could not remove corrupted cache file for region {region}: {unlink_error}")
            return None
        except Exception as e:
            logger.error(f"Error reading cache for region {region}: {e}")
            return None
    
    def update_cache(self, region: str, fis_data: Dict[str, Any]) -> Tuple[bool, str, Optional[str]]:
        """Update the cache with fresh FIS data.
        
        Args:
            region: AWS region name
            fis_data: Fresh FIS data from AWS MCP server
            
        Returns:
            Tuple of (success, message, timestamp)
        """
        try:
            # Validate input data structure
            if not isinstance(fis_data, dict):
                return False, "Invalid data format: expected dictionary", None
            
            # Create cache entry with timestamp
            timestamp = datetime.now(timezone.utc).isoformat()
            cache_entry = {
                "fis_actions": fis_data.get("fis_actions", []),
                "resource_types": fis_data.get("resource_types", []),
                "last_updated": timestamp,
                "region": region,
                "cache_ttl_hours": 24
            }
            
            # Write to cache file
            cache_file = self._get_cache_file_path(region)
            
            # Ensure directory exists
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            
            # Write atomically using temporary file
            temp_file = cache_file.with_suffix('.tmp')
            try:
                with open(temp_file, 'w', encoding='utf-8') as f:
                    json.dump(cache_entry, f, indent=2, ensure_ascii=False)
                
                # Atomic move
                temp_file.replace(cache_file)
                
                logger.info(f"Successfully updated cache for region {region}")
                return True, f"Cache updated successfully for region {region}", timestamp
                
            except Exception as e:
                # Clean up temp file on error
                if temp_file.exists():
                    temp_file.unlink()
                raise e
            
        except Exception as e:
            logger.error(f"Error updating cache for region {region}: {e}")
            return False, f"Failed to update cache: {str(e)}", None
    
    def clear_cache(self, region: Optional[str] = None) -> bool:
        """Clear cache for a specific region or all regions.
        
        Args:
            region: AWS region name. If None, clears all cached data.
            
        Returns:
            True if successful, False otherwise
        """
        try:
            if region:
                # Clear specific region cache
                cache_file = self._get_cache_file_path(region)
                if cache_file.exists():
                    cache_file.unlink()
                    logger.info(f"Cleared cache for region {region}")
            else:
                # Clear all cache files
                for cache_file in self.cache_dir.glob("fis_actions_*.json"):
                    cache_file.unlink()
                logger.info("Cleared all cached FIS data")
            
            return True
            
        except Exception as e:
            logger.error(f"Error clearing cache: {e}")
            return False