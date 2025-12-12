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
import sys
from datetime import datetime

from rich.console import Console
from rich.panel import Panel

console = Console()

# Nova Act Gym - Legacy Ticketing System Demo
NOVA_ACT_GYM_URL = "https://nova.amazon.com/act/gym/next-dot"
DEFAULT_REGION = "us-east-1"
WORKFLOW_NAME = "legacy-system-automation-agentcore"

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
    Create a ticket in a legacy system using Nova Act with AgentCore Browser Tool.
    
    Demonstrates legacy system automation by navigating a booking interface,
    filling forms, and completing a multi-step ticket creation process.
    
    Args:
        region: AWS region for AgentCore Browser
        browser_id: Browser ID from CDK stack (required for session tracking)
    
    Returns:
        dict with execution results including ticket summary
    """
    result = {
        "target_url": NOVA_ACT_GYM_URL,
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
                starting_page=NOVA_ACT_GYM_URL,
            ) as nova:
                console.print(f"[green]✓ Nova Act connected[/green]")
                console.print(f"  Target: {NOVA_ACT_GYM_URL}")
                
                # Act ID 1: Click Destinations
                console.print(f"\n[yellow]Act 1:[/yellow] Click Destinations...")
                nova.act("Click on 'Destinations'.")
                result["steps"].append({"act_id": 1, "name": "click_destinations", "status": "success"})
                console.print(f"[green]✓ Done[/green]")
                
                # Act ID 2: Select Wolf 1061c and click Book departure
                console.print(f"\n[yellow]Act 2:[/yellow] Select Wolf 1061c...")
                nova.act(
                    "Click on 'Wolf 1061c'. Then scroll down and click the large button "
                    "'BOOK DEPARTURE TO WOLF 1061C' at the bottom of the page."
                )
                result["steps"].append({"act_id": 2, "name": "select_wolf1061c_and_book", "status": "success"})
                console.print(f"[green]✓ Done[/green]")
                
                # Act ID 3: Select origin and date
                console.print(f"\n[yellow]Act 3:[/yellow] Select Boston and date...")
                nova.act("Select 'Boston' as origin. Set departure date to next week.")
                result["steps"].append({"act_id": 3, "name": "select_origin_date", "status": "success"})
                console.print(f"[green]✓ Done[/green]")
                
                # Act ID 4: Search flights
                console.print(f"\n[yellow]Act 4:[/yellow] Search flights...")
                nova.act("Click 'Search Flights'.")
                result["steps"].append({"act_id": 4, "name": "search_flights", "status": "success"})
                console.print(f"[green]✓ Done[/green]")
                
                # Act ID 5: Select Premium ticket
                console.print(f"\n[yellow]Act 5:[/yellow] Select Premium ticket...")
                nova.act("Find 'Starlight Express' and select the 'Premium' ticket.")
                result["steps"].append({"act_id": 5, "name": "select_premium", "status": "success"})
                console.print(f"[green]✓ Done[/green]")
                
                # Act ID 6: Enter passenger details
                console.print(f"\n[yellow]Act 6:[/yellow] Enter passenger details...")
                nova.act("Enter 'Jeff' as Full Legal Name. Set date of birth to January 12, 1964. Click Continue.")
                result["steps"].append({"act_id": 6, "name": "passenger_details", "status": "success"})
                console.print(f"[green]✓ Done[/green]")
                
                # Act ID 7: Medical clearance
                console.print(f"\n[yellow]Act 7:[/yellow] Medical clearance...")
                nova.act("Answer 'No' to all medical questions. Click Continue.")
                result["steps"].append({"act_id": 7, "name": "medical", "status": "success"})
                console.print(f"[green]✓ Done[/green]")
                
                # Act ID 8: Select cabin
                console.print(f"\n[yellow]Act 8:[/yellow] Select Premium Pod...")
                nova.act("Select 'Premium Pod' cabin.")
                result["steps"].append({"act_id": 8, "name": "cabin", "status": "success"})
                console.print(f"[green]✓ Done[/green]")
                
                # Act ID 9: Get summary
                console.print(f"\n[yellow]Act 9:[/yellow] Get ticket summary...")
                summary = nova.act("Return the journey summary details shown on screen.")
                # Extract parsed response if available
                if hasattr(summary, 'parsed_response') and summary.parsed_response:
                    result["ticket_summary"] = str(summary.parsed_response)
                else:
                    result["ticket_summary"] = str(summary)
                result["steps"].append({"act_id": 9, "name": "summary", "status": "success"})
                result["status"] = "success"
                console.print(f"[green]✓ Done[/green]")
        
        console.print(f"\n[green]✓ Browser session terminated[/green]")
        result["end_time"] = datetime.now().isoformat()
        return result
    
    return run_automation()



def main():
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
    
    args = parser.parse_args()
    
    console.print(Panel(
        f"[bold cyan]Legacy System Ticket Creation[/bold cyan]\n"
        f"[bold cyan]with AgentCore Browser Tool[/bold cyan]\n\n"
        f"Target: {NOVA_ACT_GYM_URL}\n"
        f"Region: {args.region}\n"
        f"Browser ID: {args.browser_id}\n"
        f"Browser: Cloud (AgentCore)\n"
        f"Auth: AWS IAM\n\n"
        f"[dim]Creating ticket: Wolf 1061c → Boston → Next week[/dim]",
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
