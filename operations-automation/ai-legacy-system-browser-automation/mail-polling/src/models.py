"""Data models for the New Employee Order EventBridge Integration."""

from dataclasses import dataclass
from datetime import datetime
from typing import Union


@dataclass
class EmailData:
    """Data extracted from a target email.
    
    Attributes:
        id: Unique identifier for deduplication
        subject: Email subject line
        content: Email body (plain text)
        sender: Sender email address
        received_time: When the email was received (datetime or string)
        timestamp: ISO format timestamp for the event
    """
    id: str
    subject: str
    content: str
    sender: str
    received_time: Union[datetime, str]
    timestamp: str
