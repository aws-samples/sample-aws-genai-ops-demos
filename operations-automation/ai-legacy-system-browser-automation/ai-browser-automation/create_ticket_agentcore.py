#!/usr/bin/env python3
"""
AI-Powered Legacy System Automation with AgentCore Browser Tool

Demonstrates browser automation using Amazon Nova Act with AgentCore Browser Tool.
Creates a ticket in a legacy booking system to showcase automated form filling.

Key features:
- Browser executes in AWS cloud (AgentCore Browser Tool)
- Session recording to S3 for audit trails
- Live view available via AWS Console
- No local browser installation required

Authentication: Uses AWS IAM credentials via @workflow decorator

Target: Nova Act Gym - Legacy Ticketing System Demo

Usage:
    python create_ticket_agentcore.py --browser-id YOUR_BROWSER_ID
    python create_ticket_agentcore.py --browser-id YOUR_BROWSER_ID --region us-west-2
"""

import argparse
import os
import sys
from datetime import datetime

from rich.console import Console
from rich.panel import Panel

console = Console()

# AnyCompany IT Demo Portal - ITSM System (use CLOUDFRONT_DOMAIN env var or --url argument)
ITSM_PORTAL_URL = None  # Set via command line or environment
DEFAULT_REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
WORKFLOW_NAME = "onboarding-email-workflow"

try:
    from bedrock_agentcore.tools.browser_client import browser_session
    from nova_act import NovaAct
    from nova_act.types.workflow import workflow
    AGENTCORE_AVAILABLE = True
except ImportError as e:
    AGENTCORE_AVAILABLE = False
    IMPORT_ERROR = str(e)


def create_ticket(
    region: str = DEFAULT_REGION,
    browser_id: str = None
) -> dict:
    """
    Create a ticket in the AnyCompany ITSM portal using Nova Act with AgentCore Browser Tool.
    
    Demonstrates legacy system automation by navigating the ITSM interface,
    filling forms, and completing a ticket creation process.
    
    Args:
        region: AWS region for AgentCore Browser
        browser_id: Browser ID from CDK stack (required for session tracking)
    
    Returns:
        dict with execution results including ticket summary
    """
    result = {
        "target_url": ITSM_PORTAL_URL,
        "region": region,
        "browser_id": browser_id,
        "browser_type": "AgentCore Browser Tool (Cloud)",
        "start_time": datetime.now().isoformat(),
        "steps": [],
        "ticket_summary": None,
        "status": "pending"
    }
    
    @workflow(workflow_definition_name=WORKFLOW_NAME, model_id="nova-act-latest")
    def run_automation():
        nonlocal result
        
        console.print(f"\n[cyan]Starting AgentCore Browser session in {region}...[/cyan]")
        if browser_id:
            console.print(f"  Using browser: {browser_id}")
        
        with browser_session(region, identifier=browser_id) as client:
            ws_url, headers = client.generate_ws_headers()
            
            result["session_id"] = getattr(client, 'session_id', 'unknown')
            console.print(f"[green]✓ Browser session started[/green]")
            console.print(f"  Session ID: {result['session_id']}")
            
            # Show live view link
            if browser_id:
                browser_console_url = f"https://{region}.console.aws.amazon.com/bedrock-agentcore/browser/{browser_id}"
            else:
                browser_console_url = f"https://{region}.console.aws.amazon.com/bedrock-agentcore/builtInTools"
            
            console.print(Panel(
                f"[bold yellow]👁️  WATCH BROWSER LIVE[/bold yellow]\n\n"
                f"[bold white]{browser_console_url}[/bold white]",
                title="🔴 Live View Available",
                border_style="red",
                width=120
            ))
            
            console.print(f"\n[cyan]Connecting Nova Act to cloud browser...[/cyan]")
            
            with NovaAct(
                cdp_endpoint_url=ws_url,
                cdp_headers=headers,
                starting_page=ITSM_PORTAL_URL,
            ) as nova:
                console.print(f"[green]✓ Nova Act connected[/green]")
                console.print(f"  Target: {ITSM_PORTAL_URL}")
                
                # Act ID 1: Click Create Ticket button
                console.print(f"\n[yellow]Act 1:[/yellow] Click Create Ticket button...")
                nova.act("Click the 'Create Ticket' button.")
                result["steps"].append({"act_id": 1, "name": "click_create_ticket", "status": "success"})
                console.print(f"[green]✓ Done[/green]")
                
                # Act ID 2: Fill in the ticket title
                console.print(f"\n[yellow]Act 2:[/yellow] Fill in ticket title...")
                nova.act("In the Title field, type 'Hardware Request - John Smith - New Employee Laptop Setup'")
                result["steps"].append({"act_id": 2, "name": "fill_title", "status": "success"})
                console.print(f"[green]✓ Done[/green]")
                
                # Act ID 3: Fill in the description
                console.print(f"\n[yellow]Act 3:[/yellow] Fill in description...")
                nova.act(
                    "In the Description field, type: 'New employee John Smith (Engineering, Software Developer) "
                    "requires complete hardware setup including Professional Laptop 15\", external monitor, "
                    "keyboard and mouse. Start date: Next Monday. Manager: Jane Doe. Budget code: ENG-2024-Q1.'"
                )
                result["steps"].append({"act_id": 3, "name": "fill_description", "status": "success"})
                console.print(f"[green]✓ Done[/green]")
                
                # Act ID 4: Select category
                console.print(f"\n[yellow]Act 4:[/yellow] Select Hardware Request category...")
                nova.act("Select 'Hardware Request' from the Category dropdown.")
                result["steps"].append({"act_id": 4, "name": "select_category", "status": "success"})
                console.print(f"[green]✓ Done[/green]")
                
                # Act ID 5: Select priority
                console.print(f"\n[yellow]Act 5:[/yellow] Select High priority...")
                nova.act("Select 'High' from the Priority dropdown.")
                result["steps"].append({"act_id": 5, "name": "select_priority", "status": "success"})
                console.print(f"[green]✓ Done[/green]")
                
                # Act ID 6: Fill requester name
                console.print(f"\n[yellow]Act 6:[/yellow] Fill requester name...")
                nova.act("In the Requester field, type 'Jane Doe'")
                result["steps"].append({"act_id": 6, "name": "fill_requester", "status": "success"})
                console.print(f"[green]✓ Done[/green]")
                
                # Act ID 7: Submit the ticket
                console.print(f"\n[yellow]Act 7:[/yellow] Submit the ticket...")
                nova.act("Click the 'Create Ticket' button to submit the form.")
                result["steps"].append({"act_id": 7, "name": "submit_ticket", "status": "success"})
                console.print(f"[green]✓ Done[/green]")
                
                # Act ID 8: Get confirmation
                console.print(f"\n[yellow]Act 8:[/yellow] Get ticket confirmation...")
                summary = nova.act("Read and return the success message or ticket ID shown on screen.")
                if hasattr(summary, 'parsed_response') and summary.parsed_response:
                    result["ticket_summary"] = str(summary.parsed_response)
                else:
                    result["ticket_summary"] = str(summary)
                result["steps"].append({"act_id": 8, "name": "get_confirmation", "status": "success"})
                result["status"] = "success"
                console.print(f"[green]✓ Done[/green]")
        
        console.print(f"\n[green]✓ Browser session terminated[/green]")
        result["end_time"] = datetime.now().isoformat()
        return result
    
    return run_automation()



def main():
    global ITSM_PORTAL_URL
    
    parser = argparse.ArgumentParser(
        description="Create a ticket in a legacy system using Nova Act with AgentCore Browser Tool"
    )
    parser.add_argument(
        "--region",
        default=DEFAULT_REGION,
        help=f"AWS region for AgentCore Browser (default: {DEFAULT_REGION})"
    )
    parser.add_argument(
        "--browser-id",
        required=True,
        help="Browser ID from CDK stack (required for session tracking)"
    )
    parser.add_argument(
        "--url",
        default=None,
        help="ITSM portal URL (default: built from CLOUDFRONT_DOMAIN env var)"
    )
    
    args = parser.parse_args()
    
    # Resolve ITSM portal URL from args or environment
    if args.url:
        ITSM_PORTAL_URL = args.url
    else:
        import os
        cloudfront_domain = os.environ.get("CLOUDFRONT_DOMAIN")
        if cloudfront_domain:
            ITSM_PORTAL_URL = f"https://{cloudfront_domain}/itsm.html"
        else:
            console.print("[bold red]Error: Set CLOUDFRONT_DOMAIN env var or pass --url[/bold red]")
            sys.exit(1)
    
    args = parser.parse_args()
    
    console.print(Panel(
        f"[bold cyan]Legacy System Ticket Creation[/bold cyan]\n"
        f"[bold cyan]with AgentCore Browser Tool[/bold cyan]\n\n"
        f"Target: {ITSM_PORTAL_URL}\n"
        f"Region: {args.region}\n"
        f"Browser ID: {args.browser_id}\n"
        f"Browser: Cloud (AgentCore)\n"
        f"Auth: AWS IAM\n\n"
        f"[dim]Creating ticket: Hardware Request for new employee[/dim]",
        title="Demo Configuration",
        border_style="cyan"
    ))
    
    if not AGENTCORE_AVAILABLE:
        console.print(Panel(
            f"[bold red]Missing Dependencies[/bold red]\n\n"
            f"Error: {IMPORT_ERROR}\n\n"
            f"Install with:\n"
            f"  pip install bedrock-agentcore nova-act rich",
            title="Setup Required",
            border_style="red"
        ))
        sys.exit(1)
    
    result = create_ticket(
        region=args.region,
        browser_id=args.browser_id
    )
    
    # Summary
    success_steps = len([s for s in result['steps'] if s['status'] == 'success'])
    total_steps = len(result['steps'])
    
    status_color = "green" if result["status"] == "success" else "red"
    
    summary = f"Status: [{status_color}]{result['status']}[/{status_color}]\n"
    summary += f"Steps: {success_steps}/{total_steps} completed\n"
    summary += f"Browser: AgentCore (Cloud)\n"
    summary += f"Region: {args.region}\n"
    summary += f"Auth: AWS IAM"
    
    console.print(Panel(summary, title="Result", border_style=status_color))
    
    # Show ticket summary if available
    if result.get("ticket_summary"):
        console.print(Panel(
            f"[bold white]{result['ticket_summary']}[/bold white]",
            title="🎫 Ticket Summary",
            border_style="cyan"
        ))
    
    # Show AWS Console links
    console.print("")
    workflow_url = f"https://{args.region}.console.aws.amazon.com/nova-act/home#/workflow-definitions/{WORKFLOW_NAME}"
    browser_url = f"https://{args.region}.console.aws.amazon.com/bedrock-agentcore/browser/{args.browser_id}"
    console.print(Panel(
        f"[bold cyan]📊 WORKFLOW RUNS:[/bold cyan] {workflow_url}\n\n"
        f"[bold cyan]🎥 BROWSER SESSIONS:[/bold cyan] {browser_url}",
        title="🔗 AWS Console Links",
        border_style="blue",
        width=120
    ))


if __name__ == "__main__":
    main()
