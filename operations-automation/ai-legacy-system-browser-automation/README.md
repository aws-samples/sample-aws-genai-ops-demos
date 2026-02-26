# Operations Automation with AI Browser Agents

End-to-end automation of IT operations workflows using Amazon Nova Act and AgentCore Browser Tool. This demo showcases how AI agents can automate complex multi-system workflows on legacy web applications that lack modern APIs.

## Overview

Many enterprises have legacy IT systems that only expose web interfaces - no APIs, no integrations. This creates operational bottlenecks where employees must manually navigate multiple portals to complete routine tasks. This demo solves that problem using AI-powered browser automation.

**The Solution**: An AI agent monitors email for requests, then autonomously navigates legacy web portals to fulfill those requests - creating tickets, checking inventory, placing orders, and sending notifications.

## Architecture

```
┌────────────────────────────────────────────────────────────────────────────────┐
│                              OPERATIONS AUTOMATION                             │
│                                                                                │
│  ┌─────────────┐     ┌──────────────────────────────────────────────────────┐  │
│  │   Outlook   │     │              AWS Cloud                               │  │
│  │   Inbox     │     │                                                      │  │
│  │             │     │  ┌─────────────┐    ┌─────────────┐                  │  │
│  │  "NEW       │     │  │  Nova Act   │    │  AgentCore  │                  │  │
│  │  EMPLOYEE   │───▶│   │  AI Model   │───▶│  Browser    │                 │  │
│  │  ORDER"     │     │  │             │    │  Tool       │                  │  │
│  │             │     │  └─────────────┘    └──────┬──────┘                  │  │
│  └─────────────┘     │                           │                          │  │
│                      │                           ▼                          │  │
│  ┌─────────────┐     │  ┌────────────────────────────────────────────────┐  │  │
│  │   Mail      │     │  │           Legacy IT Portals                    │  │  │
│  │   Polling   │───▶│  │    ┌──────────┐ ┌──────────┐ ┌──────────────┐   │  │  │
│  │   Service   │     │  │   │   ITSM   │ │Inventory │ │ Procurement  │   │  │  │
│  │  (Python)   │     │  │   │  Portal  │ │  Portal  │ │   Portal     │   │  │  │
│  └─────────────┘     │  │   └──────────┘ └──────────┘ └──────────────┘   │  │  │
│                      │  └────────────────────────────────────────────────┘  │  │
│                      │                                                      │  │
│                      │  ┌─────────────┐    ┌─────────────┐                  │  │
│                      │  │  Amazon     │    │     S3      │                  │  │
│                      │  │    SES      │    │  Recordings │                  │  │
│                      │  │ (Notify)    │    │  & Workflow │                  │  │
│                      │  └─────────────┘    └─────────────┘                  │  │
│                      └──────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────────────────────┘
```

## Demo Scenario: New Employee Onboarding

When HR sends an email requesting equipment for a new employee, the AI agent:

1. **Detects** the email in Outlook (mail-polling service)
2. **Creates** an ITSM ticket in the legacy ticketing system
3. **Checks** inventory availability for each requested item
4. **Creates** purchase orders for out-of-stock items
5. **Receives** deliveries and updates inventory
6. **Allocates** equipment to the employee
7. **Resolves** the ITSM ticket
8. **Sends** email notification to the requester

All of this happens autonomously, with the AI navigating web interfaces just like a human would.

## Components

| Component | Description |
|-----------|-------------|
| [ai-legacy-system-browser-automation/mail-polling](./ai-legacy-system-browser-automation/mail-polling/) | Python service that monitors Outlook for trigger emails |
| [ai-legacy-system-browser-automation/ai-browser-automation](./ai-legacy-system-browser-automation/ai-browser-automation/) | Nova Act browser automation and workflow orchestration |
| [anycompany-it-demo-portal](./anycompany-it-demo-portal/) | Demo legacy IT portals (ITSM, Inventory, Procurement) |

## Prerequisites

### Software Requirements
- [**Python 3.11+**](https://www.python.org/downloads/) with pip
- [**Node.js 20+**](https://nodejs.org/) with npm (for CDK)
- [**AWS CLI v2**](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) configured with credentials
- **Microsoft Outlook** desktop app (Windows or macOS)

### AWS Requirements
- AWS account with appropriate permissions
- Region: `us-east-1` (recommended)
- Services used: Nova Act, AgentCore Browser, S3, CloudFront, DynamoDB, Lambda, API Gateway, SES

### IAM Permissions

Your IAM user/role needs permissions for:
- CloudFormation (CDK deployment)
- Nova Act (AI browser automation)
- AgentCore Browser Tool (cloud browser)
- S3 (recordings and static hosting)
- DynamoDB (portal data)
- Lambda & API Gateway (portal backend)
- SES (email notifications)

See individual component documentation for detailed IAM policies.

### AWS Credentials Setup

Before deploying, configure your AWS credentials. Choose the method that matches your setup:

**Option 1: AWS SSO (recommended for organizations)**

```bash
# Configure SSO profile (one-time)
aws configure sso

# Login before each session
aws sso login --profile YOUR-PROFILE-NAME
```

Then set the profile for all subsequent commands:

**macOS / Linux:**
```bash
export AWS_PROFILE="YOUR-PROFILE-NAME"
```

**Windows (PowerShell):**
```powershell
$env:AWS_PROFILE="YOUR-PROFILE-NAME"
```

**Option 2: IAM access keys**

```bash
aws configure
# Enter your Access Key ID, Secret Access Key, and default region (us-east-1)
```

**Option 3: Environment variables**

**macOS / Linux:**
```bash
export AWS_ACCESS_KEY_ID="your-access-key"
export AWS_SECRET_ACCESS_KEY="your-secret-key"
export AWS_DEFAULT_REGION="us-east-1"
```

**Windows (PowerShell):**
```powershell
$env:AWS_ACCESS_KEY_ID="your-access-key"
$env:AWS_SECRET_ACCESS_KEY="your-secret-key"
$env:AWS_DEFAULT_REGION="us-east-1"
```

Verify your credentials are working:

```bash
aws sts get-caller-identity
```

You should see your account ID and ARN. If you get an error, your credentials are not configured correctly.

## Quick Start

### Step 1: Deploy the Demo Portal

**macOS / Linux:**
```bash
cd operations-automation/anycompany-it-demo-portal
./deploy-all.sh --populate-data
```

**Windows (PowerShell):**
```powershell
cd operations-automation/anycompany-it-demo-portal
.\deploy-all.ps1 -PopulateData
```

Note the CloudFront domain from the output (e.g., `d32hac5jwq110e.cloudfront.net`).

### Step 2: Deploy Browser Automation Infrastructure

**macOS / Linux:**
```bash
cd operations-automation/ai-legacy-system-browser-automation
./deploy-all.sh
```

**Windows (PowerShell):**
```powershell
cd operations-automation/ai-legacy-system-browser-automation
.\deploy-all.ps1
```

> **Note:** Always use the deploy scripts above rather than running `npx cdk deploy` directly. The deploy scripts set `PYTHONPATH` for shared utility imports.

Note the Browser ID from the output (e.g., `legacy_system_automation_browser-WKb1NAhhMQ`).

### Step 3: Configure Nova Act Workflow (Optional - for step visualization)

```bash
cd operations-automation/ai-legacy-system-browser-automation/ai-browser-automation
python update_workflow_s3.py --bucket legacy-automation-recordings-YOUR-ACCOUNT-ID
```

### Step 4: Set Environment Variables

Set the following environment variables before running the mail-polling service:

**macOS / Linux (bash/zsh):**
```bash
export BROWSER_ID="legacy_system_automation_browser-YOUR-BROWSER-ID"
export AWS_REGION="us-east-1"
export CLOUDFRONT_DOMAIN="YOUR-CLOUDFRONT-DOMAIN.cloudfront.net"
```

**Windows (PowerShell):**
```powershell
$env:BROWSER_ID="legacy_system_automation_browser-YOUR-BROWSER-ID"
$env:AWS_REGION="us-east-1"
$env:CLOUDFRONT_DOMAIN="YOUR-CLOUDFRONT-DOMAIN.cloudfront.net"
```

> **Note**: `CLOUDFRONT_DOMAIN` should be the domain only (e.g., `d32hac5jwq110e.cloudfront.net`), without `https://`.

### Step 5: Run the Mail Polling Service

```bash
cd operations-automation/ai-legacy-system-browser-automation/mail-polling
pip install -r requirements.txt
python -m src.cli --config src/config.yaml
```

### Step 6: Send a Test Email

Send an email to yourself with:
- **Subject**: `NEW EMPLOYEE ORDER - John Doe - Equipment Setup`
- **Body**: See [NEW_EMPLOYEE_ONBOARDING_SCENARIO.md](./NEW_EMPLOYEE_ONBOARDING_SCENARIO.md) for sample content

The automation will detect the email and process it automatically.

## Monitoring

### Live Browser View
Watch the AI navigate in real-time:
1. Open AWS Console → Bedrock AgentCore → Built-in Tools
2. Select your browser tool
3. Click "View live session" on the active session

### Workflow Visualization
View step-by-step execution in Nova Act console:
1. Open AWS Console → Nova Act → Workflow Definitions
2. Select `onboarding-email-workflow`
3. View workflow runs and step data

### Session Recordings
Review completed sessions:
- S3 bucket: `legacy-automation-recordings-{account-id}`
- Prefix: `browser-recordings/` for session recordings
- Prefix: `workflow-data/` for workflow step data

## Cost Estimates

### Monthly Costs (Development/Demo)

| Service | Estimated Cost |
|---------|---------------|
| Nova Act | $50-100 (100 sessions) |
| AgentCore Browser | $20-50 (browser time) |
| S3 | $1-5 (recordings) |
| CloudFront | $1-5 (portal hosting) |
| DynamoDB | $1-5 (on-demand) |
| Lambda | $0-1 (API calls) |
| SES | $0-1 (notifications) |
| **Total** | **~$75-170/month** |

### Per-Workflow Cost
- Single workflow execution: ~$0.50-2.00
- Depends on complexity and browser session duration

### Cost Optimization Tips
- Use shorter step pauses for faster execution
- Clean up old recordings with S3 lifecycle policies
- Use on-demand DynamoDB pricing for variable workloads

## Troubleshooting

### Email Not Detected
- Ensure Outlook is running and connected
- Check email subject contains "NEW EMPLOYEE ORDER"
- Verify mail-polling service is running

### Browser Automation Fails
- Check Browser ID is correct
- Verify AWS credentials are configured
- Check CloudWatch logs for errors

### Workflow Definition Not Found
- Run `update_workflow_s3.py` to create the workflow
- Or let the first run create it automatically

### Portal Not Loading
- Verify CloudFront domain is correct
- Check portal deployment completed successfully
- Try accessing portal directly in browser

## Project Structure

```
operations-automation/
│
├── ai-legacy-system-browser-automation/ # This demo
│   ├── README.md                       # This file
│   ├── NEW_EMPLOYEE_ONBOARDING_SCENARIO.md # Detailed workflow documentation
│   ├── operations-automation-architecture.drawio # Architecture diagram
│   ├── deploy-all.sh                   # Bash deployment script
│   ├── deploy-all.ps1                  # PowerShell deployment script
│   │
│   ├── ai-browser-automation/          # Nova Act browser automation
│   │   ├── browser_actions.py          # JSON workflow executor
│   │   ├── onboarding_orchestrator.py  # Workflow orchestration
│   │   ├── onboarding_config.py        # Workflow configuration
│   │   ├── email_parser.py             # Email content parsing
│   │   ├── models.py                   # Data models
│   │   ├── ses_notifier.py             # Email notifications
│   │   ├── ticket_formatter.py         # Ticket title/description formatting
│   │   ├── update_workflow_s3.py       # Workflow S3 configuration
│   │   ├── create_ticket_agentcore.py  # Standalone single-ticket automation
│   │   ├── requirements.txt            # Python dependencies
│   │   ├── workflows/
│   │   │   └── new_employee_onboarding_actions.json  # All browser actions (JSON)
│   │   └── infrastructure/cdk/         # CDK infrastructure
│   │
│   └── mail-polling/                   # Email monitoring service
│       ├── src/
│       │   ├── cli.py                  # CLI entry point
│       │   ├── email_monitor.py        # Main monitoring logic
│       │   ├── config.py               # Configuration
│       │   ├── config.yaml             # Default config values
│       │   └── models.py               # Data models
│       └── requirements.txt
│
└── anycompany-it-demo-portal/          # Demo legacy portals
    ├── frontend/
    │   ├── index.html                  # Portal selector
    │   ├── itsm.html                   # IT Service Management
    │   ├── inventory.html              # Inventory Management
    │   └── procurement.html            # Procurement Management
    ├── infrastructure/cdk/             # CDK infrastructure
    └── scripts/                        # Deployment scripts
```

## Customizing Workflow Actions

All browser automation steps are defined in a single JSON file:

```
ai-legacy-system-browser-automation/ai-browser-automation/workflows/new_employee_onboarding_actions.json
```

This file contains every Nova Act instruction the AI executes — no browser actions are hardcoded in Python. To adapt the automation to a different scenario or portal, you edit this JSON file instead of modifying code.

### JSON Structure

```json
{
  "workflows": {
    "itsm_create_ticket": {
      "workflow_name": "itsm_create_ticket",
      "description": "Create a new service ticket in the ITSM portal",
      "portal": "itsm",
      "steps": [
        {
          "act_id": 1,
          "name": "navigate_to_itsm",
          "instruction": "Navigate to {{itsm_url}}",
          "description": "Open the ITSM portal"
        },
        {
          "act_id": 2,
          "name": "fill_ticket_form",
          "instruction": "Fill out the form with Title: {{title}}, Category: {{category}}...",
          "description": "Fill all form fields"
        },
        {
          "act_id": 3,
          "name": "read_ticket_id",
          "instruction": "Find the ticket ID starting with INC- and return it.",
          "capture_output": true,
          "output_variable": "ticket_id",
          "output_pattern": "INC-\\d{6}"
        }
      ]
    }
  }
}
```

### Key Concepts

- `instruction` — Natural language prompt sent to Nova Act. Supports `{{variable}}` placeholders resolved at runtime from employee data, ticket info, and config.
- `capture_output` / `output_variable` — Captures a value from the page (e.g., ticket ID, stock level) and stores it for use in later workflows.
- `output_pattern` — Regex pattern to extract the value from Nova Act's response.

### Available Variables

Variables are populated from the email parser and config:

| Variable Path | Example Value |
|---------------|---------------|
| `{{config.itsm_url}}` | `https://d1qles...cloudfront.net/itsm.html` |
| `{{employee.name}}` | `John Doe` |
| `{{employee.position}}` | `Chief Information Security Officer (CISO)` |
| `{{ticket.title}}` | `Onboarding: John Doe - CISO` |
| `{{ticket.description}}` | Full equipment list |
| `{{params.item_name}}` | `Professional Laptop 16"` |
| `{{params.vendor}}` | `TechCorp Solutions` |
| `{{params.unit_price}}` | `2499.0` |
| `{{state.ticket_id}}` | `INC-370237` |

### Adapting to a Different Portal

To point the automation at a different web application:

1. Update the `instruction` text in each step to match your portal's UI (button labels, field names, form layout)
2. Update `output_pattern` if your system uses different ID formats
3. Add or remove steps as needed — the orchestrator executes them sequentially
4. Vendor/price mappings are in `onboarding_orchestrator.py` (`VENDOR_MAPPING` and `DEFAULT_PRICES` dicts)

See [NEW_EMPLOYEE_ONBOARDING_SCENARIO.md](./NEW_EMPLOYEE_ONBOARDING_SCENARIO.md) for the complete list of workflows and what each one does.

## Key Technologies

| Technology | Purpose |
|------------|---------|
| Amazon Nova Act | AI model for natural language browser control |
| AgentCore Browser Tool | Cloud-based Chrome browser execution |
| AWS CDK | Infrastructure as code |
| Amazon SES | Email notifications |
| Amazon CloudFront | Portal hosting |
| Amazon DynamoDB | Portal data storage |
| Python | Automation orchestration |

## Cleanup

To remove all deployed resources:

**macOS / Linux:**
```bash
# Destroy browser automation infrastructure
cd operations-automation/ai-legacy-system-browser-automation
./deploy-all.sh --destroy

# Destroy demo portal infrastructure
cd operations-automation/anycompany-it-demo-portal
./deploy-all.sh --destroy-infra
```

**Windows (PowerShell):**
```powershell
# Destroy browser automation infrastructure
cd operations-automation/ai-legacy-system-browser-automation
.\deploy-all.ps1 -Destroy

# Destroy demo portal infrastructure
cd operations-automation/anycompany-it-demo-portal
.\deploy-all.ps1 -DestroyInfra
```

## Resources

- [Amazon Nova Act Documentation](https://docs.aws.amazon.com/nova-act/)
- [AgentCore Browser Tool Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/browser-building-agents.html)
- [AWS CDK Documentation](https://docs.aws.amazon.com/cdk/)
- [Nova Act Pricing](https://aws.amazon.com/nova/act/pricing/)

## Contributing

We welcome community contributions! Please see [CONTRIBUTING.md](../../CONTRIBUTING.md) for guidelines.

## Security

See [CONTRIBUTING](../../CONTRIBUTING.md#security-issue-notifications) for more information.

## License

This library is licensed under the MIT-0 License. See the [LICENSE](../../LICENSE) file.
