"""CLI entry point for the New Employee Order Email Monitor.

This module provides a command-line interface for running the email monitor
that watches for NEW EMPLOYEE ORDER emails and triggers browser automation
to create tickets in the legacy system.
"""

import logging
import signal
import sys
from pathlib import Path
from typing import Optional

import click

from .config import Config, load_config
from .email_monitor import EmailMonitor


# Global reference for signal handling
_monitor: Optional[EmailMonitor] = None


def setup_logging(log_level: str) -> None:
    """Configure logging for the application.
    
    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    """
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def signal_handler(signum: int, frame) -> None:
    """Handle shutdown signals gracefully.
    
    Args:
        signum: Signal number
        frame: Current stack frame
    """
    global _monitor
    if _monitor is not None:
        _monitor.stop()


@click.command()
@click.option(
    "--config",
    "-c",
    "config_file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Path to YAML configuration file. Uses defaults if not specified.",
)
@click.option(
    "--log-level",
    "-l",
    type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], case_sensitive=False),
    default="INFO",
    help="Logging level (default: INFO)",
)
@click.option(
    "--browser-automation-script",
    "-b",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Path to browser automation script (default: auto-detect create_ticket_agentcore.py)",
)
def main(config_file: Optional[Path], log_level: str, browser_automation_script: Optional[Path]) -> None:
    """Monitor Outlook inbox for NEW EMPLOYEE ORDER emails.
    
    This application polls the Outlook inbox for emails containing
    "NEW EMPLOYEE ORDER" in the subject and triggers browser automation
    to create tickets in the legacy system.
    
    Press Ctrl+C to stop the monitor gracefully.
    """
    global _monitor
    
    # Setup logging
    setup_logging(log_level)
    logger = logging.getLogger(__name__)
    
    # Load configuration
    config_path = str(config_file) if config_file else None
    try:
        config = load_config(config_path)
        logger.info("Configuration loaded successfully")
        logger.debug(
            "Config: subject_pattern=%s, polling_interval=%ds, browser_id=%s",
            config.subject_pattern,
            config.polling_interval_seconds,
            config.browser_id or "from environment",
        )
    except Exception as e:
        logger.error("Failed to load configuration: %s", e)
        sys.exit(1)
    
    # Create email monitor
    browser_script_path = str(browser_automation_script) if browser_automation_script else None
    _monitor = EmailMonitor(config, browser_automation_script=browser_script_path)
    
    # Setup signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Start monitoring
    try:
        logger.info("Starting email monitor...")
        _monitor.start()
    except Exception as e:
        logger.error("Email monitor failed: %s", e)
        sys.exit(1)
    
    logger.info("Email monitor shutdown complete")


if __name__ == "__main__":
    main()
