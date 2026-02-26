"""Email monitor for detecting NEW EMPLOYEE ORDER emails in Outlook.

This module provides the EmailMonitor class that polls an Outlook inbox
for emails matching a target subject pattern and triggers the onboarding
orchestrator to process new employee equipment requests.

Requirements: 1.1, 1.9
"""

import logging
import sys
import time
import threading
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Set, Union

# Add src directory to path for mail_monkey imports
sys.path.insert(0, str(Path(__file__).parent))

# Add ai-browser-automation to path for onboarding imports
_automation_dir = Path(__file__).parent.parent.parent / "ai-browser-automation"
if _automation_dir.exists():
    sys.path.insert(0, str(_automation_dir))

from mail_monkey.mailclient import get_mailclient

try:
    from .config import Config
    from .models import EmailData
except ImportError:
    # Handle case when module is imported directly
    from config import Config
    from models import EmailData

# Import onboarding components (optional - may not be available in all environments)
try:
    from email_parser import parse_onboarding_email, ParseError
    from onboarding_orchestrator import OnboardingOrchestrator
    from onboarding_config import OnboardingConfig, load_onboarding_config
    ONBOARDING_AVAILABLE = True
except ImportError:
    ONBOARDING_AVAILABLE = False
    ParseError = None  # type: ignore

# Import AgentCore Browser and Nova Act (optional - for real browser automation)
try:
    from bedrock_agentcore.tools.browser_client import browser_session
    from nova_act import NovaAct
    from nova_act.types.workflow import workflow
    AGENTCORE_AVAILABLE = True
except ImportError:
    AGENTCORE_AVAILABLE = False
    browser_session = None  # type: ignore
    NovaAct = None  # type: ignore
    workflow = None  # type: ignore

# Workflow name for Nova Act AWS Service
WORKFLOW_NAME = "onboarding-email-workflow"


logger = logging.getLogger(__name__)


class EmailMonitor:
    """Monitors Outlook inbox for target emails and triggers onboarding workflow.
    
    This class connects to Outlook using the mail_monkey library, polls the inbox
    at a configurable interval, and identifies emails matching the target subject
    pattern. When a target email is found, it parses the email content and triggers
    the onboarding orchestrator to process the new employee equipment request.
    
    Supports two modes:
    - Browser Automation Mode: When BROWSER_ID env var is set, uses Nova Act with
      AgentCore Browser for real browser automation
    - Simulation Mode: When BROWSER_ID is not set, runs workflow in simulation mode
    
    Attributes:
        config: Application configuration
        processed_emails: Set of email IDs that have been processed
        browser_automation_script: Path to the create_ticket_agentcore.py script (fallback)
        onboarding_config: Configuration for the onboarding workflow
        orchestrator: OnboardingOrchestrator instance for processing requests
        browser_id: Browser ID for AgentCore Browser (from BROWSER_ID env var)
        aws_region: AWS region for AgentCore Browser
    
    Requirements: 1.1, 1.9
    """
    
    def __init__(
        self, 
        config: Config, 
        browser_automation_script: Optional[str] = None,
        onboarding_config: Optional["OnboardingConfig"] = None
    ):
        """Initialize the email monitor with configuration.
        
        Args:
            config: Application configuration containing subject pattern,
                   polling interval, and other settings.
            browser_automation_script: Path to the create_ticket_agentcore.py script.
                                      If not provided, will look for it in the
                                      ai-legacy-system-browser-automation directory.
            onboarding_config: Optional OnboardingConfig for the orchestrator.
                              If not provided, will attempt to load from environment.
        
        Requirements: 1.1
        """
        self.config = config
        self._processed_emails: Set[str] = set()
        self._mail_client = None
        self._running = False
        self._stop_event = threading.Event()
        
        # Browser automation settings from environment
        self.browser_id = os.environ.get('BROWSER_ID')
        self.aws_region = os.environ.get('AWS_REGION', 'us-east-1')
        
        # Set up browser automation script path (fallback)
        if browser_automation_script:
            self.browser_automation_script = browser_automation_script
        else:
            # Default path relative to the mail-polling directory
            current_dir = Path(__file__).parent.parent
            self.browser_automation_script = (
                current_dir.parent / 
                "ai-browser-automation" / 
                "create_ticket_agentcore.py"
            )
        
        logger.info(f"Browser automation script: {self.browser_automation_script}")
        
        # Log browser automation mode
        if self.browser_id and AGENTCORE_AVAILABLE:
            logger.info(f"Browser automation enabled - Browser ID: {self.browser_id}")
            logger.info(f"AWS Region: {self.aws_region}")
        elif self.browser_id and not AGENTCORE_AVAILABLE:
            logger.warning(
                "BROWSER_ID is set but AgentCore/Nova Act not available. "
                "Install with: pip install bedrock-agentcore nova-act"
            )
        else:
            logger.info("Browser automation disabled - running in simulation mode")
        
        # Initialize onboarding orchestrator if available
        self.onboarding_config: Optional["OnboardingConfig"] = onboarding_config
        self.orchestrator: Optional["OnboardingOrchestrator"] = None
        
        if ONBOARDING_AVAILABLE:
            self._initialize_orchestrator()
        else:
            logger.warning(
                "Onboarding modules not available. "
                "Falling back to generic browser automation."
            )
    
    def _initialize_orchestrator(self) -> None:
        """Initialize the onboarding orchestrator.
        
        Attempts to load configuration and create the orchestrator instance.
        Falls back to generic browser automation if initialization fails.
        """
        try:
            if self.onboarding_config is None:
                # Try to load from environment
                self.onboarding_config = load_onboarding_config()
            
            self.orchestrator = OnboardingOrchestrator(self.onboarding_config)
            logger.info("Onboarding orchestrator initialized successfully")
            logger.info(f"ITSM URL: {self.onboarding_config.itsm_url}")
            logger.info(f"Inventory URL: {self.onboarding_config.inventory_url}")
            logger.info(f"Procurement URL: {self.onboarding_config.procurement_url}")
        except ValueError as e:
            logger.warning(
                f"Could not initialize onboarding orchestrator: {e}. "
                "Falling back to generic browser automation."
            )
            self.orchestrator = None
        except Exception as e:
            logger.error(
                f"Unexpected error initializing orchestrator: {e}. "
                "Falling back to generic browser automation."
            )
            self.orchestrator = None
    
    def _get_mail_client(self):
        """Get or create the mail client connection.
        
        Returns:
            Mail client instance for Outlook access.
            
        Raises:
            Exception: If unable to connect to Outlook.
        """
        if self._mail_client is None:
            try:
                self._mail_client = get_mailclient()
                logger.info("Successfully connected to Outlook")
            except Exception as e:
                logger.warning(f"Cannot connect to Outlook: {e}")
                raise
        return self._mail_client
    
    def is_target_email(self, subject: str) -> bool:
        """Check if email subject matches target pattern.
        
        The match is case-insensitive and checks if the subject contains
        the configured pattern anywhere in the string.
        
        Args:
            subject: Email subject line to check.
            
        Returns:
            True if subject contains the target pattern (case-insensitive),
            False otherwise.
        """
        if subject is None:
            return False
        return self.config.subject_pattern.upper() in subject.upper()
    
    def mark_as_processed(self, email_id: str) -> None:
        """Mark an email as processed to avoid duplicates.
        
        Args:
            email_id: Unique identifier of the email to mark.
        """
        self._processed_emails.add(email_id)
        logger.debug(f"Marked email {email_id} as processed")
    
    def is_processed(self, email_id: str) -> bool:
        """Check if an email has already been processed.
        
        Args:
            email_id: Unique identifier of the email to check.
            
        Returns:
            True if the email has been processed, False otherwise.
        """
        return email_id in self._processed_emails
    
    def scan_inbox(self) -> List[EmailData]:
        """Scan inbox for new target emails.
        
        Scans the Outlook inbox for emails matching the target subject pattern
        that have not yet been processed.
        
        Returns:
            List of EmailData objects for unprocessed target emails.
            
        Raises:
            Exception: If unable to access the inbox.
        """
        target_emails: List[EmailData] = []
        
        try:
            client = self._get_mail_client()
            inbox = client.get_folder("inbox")
            
            # Limit to recent messages to avoid COM iteration issues
            message_count = 0
            max_messages = 50  # Only scan the 50 most recent messages
            
            for message in inbox.get_messages():
                message_count += 1
                if message_count > max_messages:
                    break
                    
                try:
                    subject = message.get_subject()
                    
                    # Skip if not a target email (check early to avoid unnecessary work)
                    if not self.is_target_email(subject):
                        continue
                    
                    # Generate a unique ID for the message
                    # Using subject + sender + received time as a composite key
                    sender = message.get_sender() or ""
                    try:
                        received_time = message.get_time_recieved()
                    except Exception:
                        # Fallback to sent time if received time fails
                        try:
                            received_time = message.get_time_sent()
                        except Exception:
                            received_time = datetime.now(timezone.utc)
                    
                    email_id = f"{subject}_{sender}_{received_time}"
                    
                    # Skip if already processed
                    if self.is_processed(email_id):
                        continue
                    
                    content = message.get_content(plain=True)
                    
                    email_data = EmailData(
                        id=email_id,
                        subject=subject,
                        content=content or "",
                        sender=sender,
                        received_time=received_time,
                        timestamp=datetime.now(timezone.utc).isoformat()
                    )
                    target_emails.append(email_data)
                    logger.info(f"Found target email: {subject}")
                    
                except Exception as e:
                    logger.debug(f"Error processing message: {e}")
                    continue
                    
        except Exception as e:
            logger.error(f"Error scanning inbox: {e}")
            # Return what we found so far instead of raising
        
        return target_emails

    def trigger_browser_automation(self, email_data: EmailData) -> bool:
        """Trigger browser automation to create a ticket for the email.
        
        This method first attempts to use the onboarding orchestrator if available.
        If the orchestrator is not available or fails to parse the email, it falls
        back to the generic browser automation script.
        
        Args:
            email_data: The email data that triggered the automation
            
        Returns:
            True if automation was triggered successfully, False otherwise
        
        Requirements: 1.1, 1.9
        """
        # Try onboarding orchestrator first if available
        if self.orchestrator is not None and ONBOARDING_AVAILABLE:
            result = self._trigger_onboarding_workflow(email_data)
            if result is not None:
                return result
            # If result is None, fall through to generic automation
            logger.info("Falling back to generic browser automation")
        
        # Fallback to generic browser automation
        return self._trigger_generic_browser_automation(email_data)
    
    def _trigger_onboarding_workflow(self, email_data: EmailData) -> Optional[bool]:
        """Trigger the onboarding workflow for a new employee order email.
        
        Parses the email content and executes the onboarding orchestrator.
        When BROWSER_ID is set and AgentCore is available, uses real browser automation.
        Otherwise, runs in simulation mode.
        
        Args:
            email_data: The email data to process
            
        Returns:
            True if workflow completed successfully
            False if workflow failed
            None if email could not be parsed (should fall back to generic automation)
        
        Requirements: 1.1, 1.9
        """
        try:
            logger.info(f"Processing email with onboarding workflow: {email_data.subject}")
            
            # Parse the email content
            parse_result = parse_onboarding_email(
                subject=email_data.subject,
                content=email_data.content
            )
            
            # Check if parsing failed
            if isinstance(parse_result, ParseError):
                logger.warning(
                    f"Failed to parse email as onboarding request: {parse_result.message} "
                    f"(field: {parse_result.field})"
                )
                # Return None to indicate fallback should be used
                return None
            
            # Successfully parsed - set the requester email from the original sender
            onboarding_request = parse_result
            onboarding_request.requester_email = email_data.sender
            
            # Log comprehensive parsed email data
            logger.info("=" * 60)
            logger.info("PARSED EMAIL DATA")
            logger.info("=" * 60)
            logger.info(f"Employee Name: {onboarding_request.employee.name}")
            logger.info(f"Employee Email: {onboarding_request.employee.email}")
            logger.info(f"Employee Position: {onboarding_request.employee.position}")
            logger.info(f"Employee Department: {onboarding_request.employee.department}")
            logger.info(f"Employee Start Date: {onboarding_request.employee.start_date}")
            logger.info(f"Employee Manager: {onboarding_request.employee.manager}")
            logger.info("-" * 40)
            logger.info(f"Budget Code: {onboarding_request.budget_code}")
            logger.info(f"Priority: {onboarding_request.priority}")
            logger.info(f"Requester Email: {onboarding_request.requester_email}")
            logger.info("-" * 40)
            logger.info(f"Equipment Items ({len(onboarding_request.equipment)}):")
            for i, item in enumerate(onboarding_request.equipment, 1):
                logger.info(f"  {i}. {item.name}")
                logger.info(f"     Category: {item.category}")
                logger.info(f"     Description: {item.description or 'N/A'}")
            logger.info("=" * 60)
            
            # Check if we should use real browser automation
            if self.browser_id and AGENTCORE_AVAILABLE:
                return self._execute_with_browser_automation(onboarding_request, email_data)
            else:
                return self._execute_simulation_mode(onboarding_request, email_data)
                
        except Exception as e:
            logger.error(f"Error in onboarding workflow: {e}", exc_info=True)
            return False
    
    def _execute_with_browser_automation(
        self, 
        onboarding_request, 
        email_data: EmailData
    ) -> bool:
        """Execute workflow with real browser automation using Nova Act.
        
        Args:
            onboarding_request: The parsed onboarding request
            email_data: The email data for duplicate prevention
            
        Returns:
            True if workflow completed successfully, False otherwise
        """
        logger.info("Executing workflow with real browser automation")
        logger.info(f"Browser ID: {self.browser_id}")
        logger.info(f"AWS Region: {self.aws_region}")
        
        # Check for step-by-step mode from environment
        step_by_step = os.environ.get('STEP_BY_STEP', '').lower() in ('true', '1', 'yes')
        if step_by_step:
            logger.info("*** STEP-BY-STEP MODE ENABLED ***")
        
        # Store references for the workflow function
        self._current_request = onboarding_request
        self._current_email_data = email_data
        self._workflow_result = False
        self._step_by_step = step_by_step
        
        @workflow(workflow_definition_name=WORKFLOW_NAME, model_id="nova-act-latest")
        def execute_browser_workflow():
            try:
                with browser_session(self.aws_region, identifier=self.browser_id) as client:
                    ws_url, headers = client.generate_ws_headers()
                    logger.info("Browser session started successfully")
                    
                    # Log live view URL for monitoring
                    browser_console_url = (
                        f"https://{self.aws_region}.console.aws.amazon.com/"
                        f"bedrock-agentcore/browser/{self.browser_id}"
                    )
                    logger.info(f"Live view available at: {browser_console_url}")
                    
                    # Start with ITSM portal
                    starting_url = self.onboarding_config.itsm_url
                    logger.info(f"Starting browser at: {starting_url}")
                    
                    with NovaAct(
                        cdp_endpoint_url=ws_url,
                        cdp_headers=headers,
                        starting_page=starting_url,
                    ) as nova:
                        logger.info("Nova Act connected to browser")
                        
                        # Create orchestrator with Nova instance for real browser automation
                        orchestrator = OnboardingOrchestrator(
                            self.onboarding_config, 
                            nova=nova, 
                            skip_ses_init=False,
                            step_by_step=self._step_by_step
                        )
                        
                        # Execute the workflow
                        workflow_state = orchestrator.execute(
                            request=self._current_request,
                            email_id=self._current_email_data.id
                        )
                        
                        self._workflow_result = self._handle_workflow_result(
                            workflow_state, 
                            self._current_request, 
                            self._current_email_data
                        )
                        
            except Exception as e:
                logger.error(f"Browser automation failed inside workflow: {e}", exc_info=True)
                self._workflow_result = False
        
        try:
            execute_browser_workflow()
            return self._workflow_result
        except Exception as e:
            logger.error(f"Browser automation failed: {e}", exc_info=True)
            return False
    
    def _execute_simulation_mode(
        self, 
        onboarding_request, 
        email_data: EmailData
    ) -> bool:
        """Execute workflow in simulation mode (no browser).
        
        Args:
            onboarding_request: The parsed onboarding request
            email_data: The email data for duplicate prevention
            
        Returns:
            True if workflow completed successfully, False otherwise
        """
        logger.info("Executing workflow in simulation mode (no browser)")
        
        # Check for step-by-step mode from environment
        step_by_step = os.environ.get('STEP_BY_STEP', '').lower() in ('true', '1', 'yes')
        if step_by_step:
            logger.info("*** STEP-BY-STEP MODE ENABLED ***")
        
        # Re-initialize orchestrator with step_by_step flag if needed
        if step_by_step and self.orchestrator is not None:
            self.orchestrator = OnboardingOrchestrator(
                self.onboarding_config, 
                skip_ses_init=True,
                step_by_step=True
            )
        
        # Execute the orchestrator with the email ID for duplicate prevention
        workflow_state = self.orchestrator.execute(
            request=onboarding_request,
            email_id=email_data.id
        )
        
        return self._handle_workflow_result(workflow_state, onboarding_request, email_data)
    
    def _handle_workflow_result(
        self, 
        workflow_state, 
        onboarding_request, 
        email_data: EmailData
    ) -> bool:
        """Handle the result of a workflow execution.
        
        Args:
            workflow_state: The workflow state after execution
            onboarding_request: The original request
            email_data: The email data
            
        Returns:
            True if workflow completed successfully, False otherwise
        """
        # Check workflow result
        if workflow_state.status == "completed":
            logger.info(
                f"Onboarding workflow completed successfully for {onboarding_request.employee.name}. "
                f"Ticket ID: {workflow_state.ticket_id}"
            )
            return True
        elif workflow_state.status == "skipped":
            logger.info(
                f"Email already processed (duplicate prevention): {email_data.id}"
            )
            # Return True since this is expected behavior for duplicates
            return True
        else:
            logger.error(
                f"Onboarding workflow failed with status '{workflow_state.status}': "
                f"{workflow_state.error}"
            )
            return False
    
    def _trigger_generic_browser_automation(self, email_data: EmailData) -> bool:
        """Trigger generic browser automation script as fallback.
        
        Imports and runs the browser automation module directly instead of
        spawning a subprocess, avoiding command injection risks entirely.
        
        Args:
            email_data: The email data that triggered the automation
            
        Returns:
            True if automation was triggered successfully, False otherwise
        """
        try:
            # Check if browser automation script exists
            if not os.path.exists(self.browser_automation_script):
                logger.error(f"Browser automation script not found: {self.browser_automation_script}")
                return False
            
            # Get browser ID from environment or config
            browser_id = os.environ.get('BROWSER_ID') or getattr(self.config, 'browser_id', None)
            if not browser_id:
                logger.error("BROWSER_ID not set in environment or config")
                return False
            
            # Get AWS region from config
            region = getattr(self.config, 'aws_region', 'us-east-1')
            
            # Validate inputs
            import re
            if not re.match(r'^[a-zA-Z0-9_\-]+$', browser_id):
                logger.error("Invalid BROWSER_ID format - must be alphanumeric with hyphens/underscores")
                return False
            if not re.match(r'^[a-z]{2}-[a-z]+-\d{1,2}$', region):
                logger.error("Invalid AWS region format")
                return False
            
            logger.info(f"Triggering browser automation for email: {email_data.subject}")
            logger.info(f"Browser ID: {browser_id}, Region: {region}")
            
            # Import and run the automation module directly (no subprocess needed)
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                "create_ticket_agentcore", 
                str(self.browser_automation_script)
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            
            # Call the module's main function if it exists
            if hasattr(module, 'main'):
                module.main(browser_id=browser_id, region=region)
            elif hasattr(module, 'run'):
                module.run(browser_id=browser_id, region=region)
            else:
                logger.error("Browser automation script has no main() or run() function")
                return False
            
            logger.info("Browser automation completed successfully")
            return True
                
        except Exception as e:
            logger.error(f"Error triggering browser automation: {e}")
            return False

    def start(self) -> None:
        """Start the polling loop.
        
        Polls the Outlook inbox at the configured interval (default 30s),
        detects target emails, and triggers the onboarding workflow to process
        new employee equipment requests. Falls back to generic browser automation
        if the orchestrator is not available. This method blocks until stop() is called.
        
        Raises:
            Exception: If unable to connect to Outlook on startup.
        
        Requirements: 1.1, 1.9
        """
        logger.info(
            "Starting email monitor with polling interval of %d seconds",
            self.config.polling_interval_seconds
        )
        
        # Verify Outlook connection on startup
        try:
            self._get_mail_client()
        except Exception as e:
            logger.warning("Cannot connect to Outlook: %s", e)
            raise
        
        # Log which automation mode will be used
        if self.orchestrator is not None:
            logger.info("Using onboarding orchestrator for email processing")
        else:
            # Verify browser automation script exists for fallback
            if not os.path.exists(self.browser_automation_script):
                logger.warning(f"Browser automation script not found: {self.browser_automation_script}")
                raise FileNotFoundError(f"Browser automation script not found: {self.browser_automation_script}")
            logger.info("Using generic browser automation for email processing")
        
        self._running = True
        self._stop_event.clear()
        
        logger.info("Email monitor started, watching for '%s' emails", self.config.subject_pattern)
        if self.orchestrator is None:
            logger.info("Browser automation script: %s", self.browser_automation_script)
        
        while self._running:
            try:
                # Scan for target emails
                target_emails = self.scan_inbox()
                
                # Process each target email
                for email_data in target_emails:
                    if self._stop_event.is_set():
                        break
                    
                    # Trigger automation (orchestrator or fallback)
                    success = self.trigger_browser_automation(email_data)
                    
                    if success:
                        # Mark as processed only on successful automation
                        self.mark_as_processed(email_data.id)
                        logger.info("Successfully processed email: %s", email_data.subject)
                    else:
                        logger.error(
                            "Failed to process email: %s, will retry next poll",
                            email_data.subject
                        )
                        
            except Exception as e:
                logger.warning("Error during polling cycle: %s", e)
                # Continue polling despite errors
            
            # Wait for the configured interval or until stop is called
            if self._stop_event.wait(timeout=self.config.polling_interval_seconds):
                break
        
        logger.info("Email monitor stopped")

    def stop(self) -> None:
        """Stop the polling loop gracefully.
        
        Signals the polling loop to stop after the current iteration completes.
        """
        logger.info("Stopping email monitor...")
        self._running = False
        self._stop_event.set()
