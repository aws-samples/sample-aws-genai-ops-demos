"""Configuration management for the New Employee Order EventBridge Integration."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


@dataclass
class Config:
    """Application configuration.
    
    Attributes:
        subject_pattern: Pattern to match in email subjects (case-insensitive)
        polling_interval_seconds: How often to poll the inbox
        aws_region: AWS region for browser automation
        browser_id: Browser ID for AgentCore Browser Tool (can be overridden by BROWSER_ID env var)
    """
    subject_pattern: str = "NEW EMPLOYEE ORDER"
    polling_interval_seconds: int = 30
    aws_region: str = "us-east-1"
    browser_id: Optional[str] = None


def load_config(config_path: Optional[str] = None) -> Config:
    """Load configuration from file or use defaults.
    
    Args:
        config_path: Path to YAML configuration file. If None, uses defaults.
        
    Returns:
        Config object with loaded or default values.
        
    Notes:
        - AWS region can be overridden by AWS_REGION environment variable
        - Browser ID can be overridden by BROWSER_ID environment variable
        - Missing config file results in default values being used
    """
    config_data = {}
    
    if config_path:
        path = Path(config_path)
        if path.exists():
            with open(path, 'r', encoding='utf-8') as f:
                config_data = yaml.safe_load(f) or {}
    
    # Build config from file data with defaults
    config = Config(
        subject_pattern=config_data.get('subject_pattern', Config.subject_pattern),
        polling_interval_seconds=config_data.get('polling_interval_seconds', Config.polling_interval_seconds),
        aws_region=config_data.get('aws_region', Config.aws_region),
        browser_id=config_data.get('browser_id', Config.browser_id),
    )
    
    # Environment variable overrides
    env_region = os.environ.get('AWS_REGION')
    if env_region:
        config.aws_region = env_region
    
    env_browser_id = os.environ.get('BROWSER_ID')
    if env_browser_id:
        config.browser_id = env_browser_id
    
    return config
