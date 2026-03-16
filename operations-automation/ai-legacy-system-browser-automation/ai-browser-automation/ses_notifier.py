"""SES Notifier module for sending email notifications via Amazon SES.

This module provides functionality to send equipment ready notifications
to new employees as part of the onboarding automation workflow.

Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8
"""

import logging
from dataclasses import dataclass
from typing import List

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


@dataclass
class NotificationData:
    """Data for email notification.
    
    Attributes:
        recipient_email: Email address of the new employee
        employee_name: Full name of the new employee
        ticket_id: ITSM ticket ID for reference
        allocated_items: List of equipment items allocated to the employee
        cloudfront_domain: CloudFront domain for portal links
    """
    recipient_email: str
    employee_name: str
    ticket_id: str
    allocated_items: List[str]
    cloudfront_domain: str


class SESNotifier:
    """Sends email notifications via Amazon SES.
    
    This class handles composing and sending equipment ready notifications
    to new employees when their onboarding equipment has been allocated.
    
    Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8
    """
    
    # Default pickup location and contact information
    PICKUP_LOCATION = "IT Equipment Room, Building A, Floor 2"
    CONTACT_INFO = "IT Help Desk: helpdesk@anycompany.com | Phone: (555) 123-4567"
    
    def __init__(self, region: str, sender_email: str, use_recipient_as_sender: bool = False):
        """Initialize the SES notifier.
        
        Args:
            region: AWS region for SES (e.g., 'us-east-1')
            sender_email: Email address to send notifications from
            use_recipient_as_sender: If True, use recipient email as sender (for SES sandbox testing)
        """
        self.ses_client = boto3.client('ses', region_name=region)
        self.sender_email = sender_email
        self.region = region
        self.use_recipient_as_sender = use_recipient_as_sender

    def send_equipment_ready_notification(self, data: NotificationData) -> bool:
        """Send equipment ready email notification to the new employee.
        
        Composes and sends an HTML email via Amazon SES notifying the employee
        that their equipment is ready for pickup.
        
        Args:
            data: NotificationData containing recipient info and equipment list
            
        Returns:
            True if email was sent successfully, False otherwise
            
        Requirements: 8.1, 8.7, 8.8
        """
        subject = self._compose_subject(data)
        html_body = self._compose_email_body(data)
        text_body = self._compose_text_body(data)
        
        # In SES sandbox mode, use recipient as sender if configured
        sender = data.recipient_email if self.use_recipient_as_sender else self.sender_email
        
        try:
            response = self.ses_client.send_email(
                Source=sender,
                Destination={
                    'ToAddresses': [data.recipient_email]
                },
                Message={
                    'Subject': {
                        'Data': subject,
                        'Charset': 'UTF-8'
                    },
                    'Body': {
                        'Text': {
                            'Data': text_body,
                            'Charset': 'UTF-8'
                        },
                        'Html': {
                            'Data': html_body,
                            'Charset': 'UTF-8'
                        }
                    }
                }
            )
            logger.info(
                f"Equipment ready notification sent to {data.recipient_email}. "
                f"Message ID: {response['MessageId']}"
            )
            return True
            
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            logger.error(
                f"Failed to send equipment ready notification to {data.recipient_email}. "
                f"Error: {error_code} - {error_message}"
            )
            return False
        except Exception as e:
            logger.error(
                f"Unexpected error sending notification to {data.recipient_email}: {e}"
            )
            return False
    
    def _compose_subject(self, data: NotificationData) -> str:
        """Compose the email subject line.
        
        Args:
            data: NotificationData containing employee info
            
        Returns:
            Subject line string indicating equipment is ready
            
        Requirements: 8.2
        """
        return f"Your Equipment is Ready - Ticket #{data.ticket_id}"

    def _compose_email_body(self, data: NotificationData) -> str:
        """Compose HTML email body with equipment summary.
        
        Creates an HTML formatted email containing:
        - Welcome message with employee name
        - List of allocated equipment items
        - Pickup location and contact information
        - Ticket ID for reference
        
        Args:
            data: NotificationData containing all notification details
            
        Returns:
            HTML formatted email body string
            
        Requirements: 8.3, 8.4, 8.5, 8.6
        """
        # Build equipment list HTML
        equipment_list_html = ""
        for item in data.allocated_items:
            equipment_list_html += f"<li>{item}</li>\n"
        
        html_body = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>
        body {{
            font-family: Arial, sans-serif;
            line-height: 1.6;
            color: #333;
            max-width: 600px;
            margin: 0 auto;
            padding: 20px;
        }}
        .header {{
            background-color: #0066cc;
            color: white;
            padding: 20px;
            text-align: center;
            border-radius: 5px 5px 0 0;
        }}
        .content {{
            background-color: #f9f9f9;
            padding: 20px;
            border: 1px solid #ddd;
            border-top: none;
        }}
        .equipment-list {{
            background-color: white;
            padding: 15px;
            border-radius: 5px;
            margin: 15px 0;
        }}
        .equipment-list ul {{
            margin: 10px 0;
            padding-left: 20px;
        }}
        .equipment-list li {{
            margin: 5px 0;
        }}
        .info-box {{
            background-color: #e8f4fd;
            padding: 15px;
            border-radius: 5px;
            margin: 15px 0;
            border-left: 4px solid #0066cc;
        }}
        .ticket-ref {{
            font-size: 0.9em;
            color: #666;
            margin-top: 20px;
            padding-top: 15px;
            border-top: 1px solid #ddd;
        }}
        .footer {{
            text-align: center;
            font-size: 0.8em;
            color: #666;
            margin-top: 20px;
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>Welcome to AnyCompany!</h1>
    </div>
    <div class="content">
        <p>Dear <strong>{data.employee_name}</strong>,</p>
        
        <p>Great news! Your IT equipment has been prepared and is ready for pickup.</p>
        
        <div class="equipment-list">
            <h3>Your Equipment:</h3>
            <ul>
{equipment_list_html}            </ul>
        </div>
        
        <div class="info-box">
            <h3>Pickup Information</h3>
            <p><strong>Location:</strong> {self.PICKUP_LOCATION}</p>
            <p><strong>Contact:</strong> {self.CONTACT_INFO}</p>
        </div>
        
        <p>Please bring your employee ID when picking up your equipment. If you have any questions, 
        don't hesitate to reach out to the IT Help Desk.</p>
        
        <div class="ticket-ref">
            <p><strong>Reference:</strong> Ticket #{data.ticket_id}</p>
        </div>
    </div>
    <div class="footer">
        <p>This is an automated message from the AnyCompany IT Department.</p>
    </div>
</body>
</html>"""
        
        return html_body

    def _compose_text_body(self, data: NotificationData) -> str:
        """Compose plain text email body as fallback.
        
        Creates a plain text version of the email for clients that
        don't support HTML.
        
        Args:
            data: NotificationData containing all notification details
            
        Returns:
            Plain text formatted email body string
        """
        equipment_list = "\n".join(f"  - {item}" for item in data.allocated_items)
        
        text_body = f"""Welcome to AnyCompany!

Dear {data.employee_name},

Great news! Your IT equipment has been prepared and is ready for pickup.

Your Equipment:
{equipment_list}

Pickup Information:
Location: {self.PICKUP_LOCATION}
Contact: {self.CONTACT_INFO}

Please bring your employee ID when picking up your equipment. If you have any questions, 
don't hesitate to reach out to the IT Help Desk.

Reference: Ticket #{data.ticket_id}

---
This is an automated message from the AnyCompany IT Department.
"""
        
        return text_body
