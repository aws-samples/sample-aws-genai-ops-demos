"""Configuration management for the New Employee Onboarding Automation.

This module provides configuration loading and management for the onboarding
workflow, including portal URLs, AWS settings, and automation parameters.

Requirements: 9.1, 9.2, 9.3, 9.7, 9.8
"""

import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class OnboardingConfig:
    """Configuration for onboarding automation.
    
    Attributes:
        cloudfront_domain: CloudFront domain for the demo portal
        aws_region: AWS region for AgentCore Browser and SES
        browser_id: Browser ID from CDK stack for AgentCore
        step_pause_seconds: Pause duration between automation steps
        ses_sender_email: Email address for SES notifications
        ses_use_recipient_as_sender: Use recipient email as sender (for SES sandbox)
        bulk_order_quantity: Default quantity for bulk procurement orders
        polling_interval_seconds: Email polling interval
        subject_pattern: Pattern to match in email subjects
    
    Requirements: 9.1, 9.2, 9.3, 9.7, 9.8
    """
    cloudfront_domain: str
    aws_region: str = "us-east-1"
    browser_id: Optional[str] = None
    step_pause_seconds: int = 0
    ses_sender_email: str = "it-notifications@anycompany.com"
    ses_use_recipient_as_sender: bool = True  # Default True for SES sandbox testing
    bulk_order_quantity: int = 10
    polling_interval_seconds: int = 30
    subject_pattern: str = "NEW EMPLOYEE ORDER"
    
    @property
    def itsm_url(self) -> str:
        """URL for the ITSM portal.
        
        Requirements: 9.1
        """
        return f"https://{self.cloudfront_domain}/itsm.html"
    
    @property
    def inventory_url(self) -> str:
        """URL for the Inventory portal.
        
        Requirements: 9.1
        """
        return f"https://{self.cloudfront_domain}/inventory.html"
    
    @property
    def procurement_url(self) -> str:
        """URL for the Procurement portal.
        
        Requirements: 9.1
        """
        return f"https://{self.cloudfront_domain}/procurement.html"


def load_onboarding_config() -> OnboardingConfig:
    """Load configuration from environment variables.
    
    Environment Variables:
        CLOUDFRONT_DOMAIN: Required. CloudFront domain for portal URLs.
        AWS_REGION: AWS region (default: us-east-1)
        BROWSER_ID: Browser ID for AgentCore Browser Tool
        STEP_PAUSE_SECONDS: Pause between steps (default: 5)
        SES_SENDER_EMAIL: Sender email for notifications
        BULK_ORDER_QUANTITY: Quantity for bulk orders (default: 10)
        POLLING_INTERVAL_SECONDS: Email polling interval (default: 30)
        SUBJECT_PATTERN: Email subject pattern to match
    
    Returns:
        OnboardingConfig object with loaded values.
        
    Raises:
        ValueError: If CLOUDFRONT_DOMAIN is not set.
    
    Requirements: 9.1, 9.2, 9.3, 9.7, 9.8
    """
    cloudfront_domain = os.environ.get("CLOUDFRONT_DOMAIN")
    if not cloudfront_domain:
        raise ValueError("CLOUDFRONT_DOMAIN environment variable is required")
    
    return OnboardingConfig(
        cloudfront_domain=cloudfront_domain,
        aws_region=os.environ.get("AWS_REGION", "us-east-1"),
        browser_id=os.environ.get("BROWSER_ID"),
        step_pause_seconds=int(os.environ.get("STEP_PAUSE_SECONDS", "0")),
        ses_sender_email=os.environ.get("SES_SENDER_EMAIL", "it-notifications@anycompany.com"),
        ses_use_recipient_as_sender=os.environ.get("SES_USE_RECIPIENT_AS_SENDER", "true").lower() == "true",
        bulk_order_quantity=int(os.environ.get("BULK_ORDER_QUANTITY", "10")),
        polling_interval_seconds=int(os.environ.get("POLLING_INTERVAL_SECONDS", "30")),
        subject_pattern=os.environ.get("SUBJECT_PATTERN", "NEW EMPLOYEE ORDER"),
    )
