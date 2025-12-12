# AI-Powered Legacy System Automation with AgentCore Browser Tool

Automate workflows on legacy systems using Amazon Nova Act with AgentCore Browser Tool for cloud-based browser execution.

## Overview

This demo showcases how to automate ticket creation in a legacy IT system that only has a web interface. We use the **"Next-Dot" space travel booking site** ([Nova Act Gym](https://nova.amazon.com/act/gym/next-dot)) as a stand-in for a legacy ticketing system - imagine it's an old internal IT portal where employees submit support tickets, travel requests, or procurement forms.

**The Scenario**: Your organization has a legacy web application with no API. Employees must manually fill out multi-step forms to create tickets. This demo automates that entire workflow using AI-powered browser automation running in AWS cloud.

AgentCore Browser Tool provides:

- **Cloud execution**: No local browser installation required
- **Session recording**: Automatic capture to S3 for audit trails
- **Live view**: Watch automation in real-time via AWS Console
- **VPC connectivity**: Access internal legacy systems without VPN
- **Scalability**: Run multiple browser sessions in parallel

**Authentication**: Uses AWS IAM credentials (no API key needed)

## Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Legacy System  │◀───▶│  AgentCore       │◀───▶│  Python Script  │
│  (Next-Dot)     │     │  Browser Tool    │     │  + Nova Act     │
└─────────────────┘     │  (Cloud Chrome)  │     │  (Local)        │
                        └────────┬─────────┘     └─────────────────┘
                                 │
                        ┌────────▼─────────┐
                        │  AWS Console     │
                        │  (Live View)     │
                        └──────────────────┘
                                 │
                        ┌────────▼─────────┐
                        │  S3 Bucket       │
                        │  (Recordings)    │
                        └──────────────────┘
```

**Components:**
- **Nova Act SDK**: AI-powered browser automation via natural language (IAM auth)
- **AgentCore Browser Tool**: Managed Chrome browser running in AWS (deployed via CDK)
- **Python Orchestrator**: Local script coordinating the automation
- **AWS Console**: Live view of browser session
- **S3**: Session recordings for audit
- **Next-Dot Demo Site**: Nova Act Gym test site simulating a legacy ticketing system

## Prerequisites

- Python 3.10+
- Node.js 18+ (for CDK)
- AWS CLI v2 configured with credentials
- AWS Region: `us-east-1`

Prerequisites are automatically validated using the shared scripts in `shared/scripts/`. All demos in this repository use CDK for infrastructure deployment.

### Required IAM Permissions

Your IAM user/role needs permissions for CDK deployment, Nova Act, and AgentCore Browser:

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "CDKDeployment",
            "Effect": "Allow",
            "Action": [
                "cloudformation:*",
                "s3:*",
                "iam:*",
                "ecr:*"
            ],
            "Resource": "*"
        },
        {
            "Sid": "NovaActAccess",
            "Effect": "Allow",
            "Action": [
                "nova-act:*"
            ],
            "Resource": "*"
        },
        {
            "Sid": "BedrockAgentCoreBrowserAccess",
            "Effect": "Allow",
            "Action": [
                "bedrock-agentcore:*"
            ],
            "Resource": "*"
        }
    ]
}
```

## Quick Start

### One-Command Demo

```powershell
# PowerShell (Windows)
cd operations-automation/legacy-system-automation-agentcore
.\run-demo.ps1
```

```bash
# Bash (Linux/macOS)
cd operations-automation/legacy-system-automation-agentcore
./run-demo.sh
```

This will:
1. Check prerequisites (Python, Node.js, AWS credentials)
2. Deploy CDK infrastructure (Browser Tool, S3 bucket, IAM roles)
3. Create Nova Act workflow definition
4. Display live view instructions
5. Create a ticket in the legacy system (Next-Dot demo site)

### What the Demo Does

The automation performs a 9-step ticket creation workflow:
1. Navigate to the legacy ticketing portal
2. Select destination category (Wolf 1061c)
3. Click the booking button
4. Fill origin and date fields
5. Search available options
6. Select ticket tier (Premium)
7. Enter requester details (name, date of birth)
8. Complete clearance questions
9. Select accommodation and retrieve confirmation

### Demo Options

```powershell
# Different region
.\run-demo.ps1 -Region "us-west-2"

# Skip setup (if already configured)
.\run-demo.ps1 -SkipSetup

# Destroy infrastructure when done
.\run-demo.ps1 -DestroyInfra
```

```bash
# Bash equivalents
./run-demo.sh --region "us-west-2"
./run-demo.sh --skip-setup
./run-demo.sh --destroy-infra
```

## Infrastructure

This demo deploys resources using two methods:

### Deployed via CDK (CloudFormation)

Resources with CloudFormation support are deployed via CDK stack `LegacySystemAutomationAgentCore`:

- **AgentCore Browser Tool** (`AWS::BedrockAgentCore::BrowserCustom`)
  - Custom browser with session recording enabled
  - Public network mode for accessing external sites

- **S3 Bucket** for session recordings
  - Versioned, encrypted, auto-delete on stack removal

- **IAM Role** for browser execution
  - Permissions for browser operations and S3 recording

### Deployed via AWS CLI

Nova Act does not have CloudFormation support yet, so the workflow definition is created via CLI:

- **Nova Act Workflow Definition** (`legacy-system-automation-agentcore`)
  - Created with S3 bucket configuration for step data
  - Required for `@workflow` decorator authentication

### Manual CDK Deployment

If you prefer to deploy infrastructure manually:

```powershell
# PowerShell (Windows)
cd infrastructure/cdk
pip install -r requirements.txt
npx -y cdk bootstrap --no-cli-pager
npx -y cdk deploy --require-approval never --no-cli-pager
```

```bash
# Bash (Linux/macOS)
cd infrastructure/cdk
pip3 install -r requirements.txt
npx -y cdk bootstrap --no-cli-pager
npx -y cdk deploy --require-approval never --no-cli-pager
```

### CDK Outputs

After deployment, get stack outputs:

```bash
aws cloudformation describe-stacks --stack-name LegacySystemAutomationAgentCore --no-cli-pager \
  --query "Stacks[0].Outputs"
```

Outputs include:
- `BrowserId` - Use this with `--browser-id` parameter
- `RecordingsBucketName` - S3 bucket for recordings
- `LiveViewConsoleUrl` - AWS Console URL for live view

## Live View

Watch the browser automation in real-time via AWS Console:

1. Open the AgentCore Browser Console:
   `https://us-east-1.console.aws.amazon.com/bedrock-agentcore/builtInTools`

2. Navigate to **Built-in tools** in the left navigation

3. Select your custom browser (`legacy_system_automation_browser`)

4. Find your active session with status **Ready**

5. Click **View live session** to watch in real-time

## Session Recordings

Session recordings are automatically saved to S3:

```
s3://legacy-automation-recordings-{account-id}/browser-recordings/
  └── {session-id}/
      ├── batch_1.ndjson.gz
      ├── batch_2.ndjson.gz
      └── ...
```

### Replay Recordings

1. Go to AgentCore Browser Console
2. Select your browser tool
3. Find completed session (status: Terminated)
4. Click **View Recording**

## Cost Estimates

### CDK Infrastructure
- S3 bucket: ~$0.023/GB/month
- IAM roles: Free

### AgentCore Browser Tool
- Browser session time: Varies by region and duration
- See [AgentCore pricing](https://aws.amazon.com/bedrock/agentcore/pricing/)

### Nova Act
- Based on browser session time and actions
- See [Nova Act pricing](https://aws.amazon.com/nova/act/)

### Estimated Demo Cost
- Single demo run: < $1
- Development (100 sessions/month): ~$50-100

## Troubleshooting

### CDK Deployment Failed
- Ensure CDK is bootstrapped: `npx cdk bootstrap`
- Check IAM permissions for CloudFormation, S3, IAM

### Browser Session Failed
- Check browser ID is correct in CDK outputs
- Verify IAM role has correct trust policy
- Check CloudWatch logs: `/aws/bedrock-agentcore/browser/`

### Workflow Definition Error
- Run manually: `aws nova-act create-workflow-definition --name legacy-system-automation-agentcore --region us-east-1 --no-cli-pager`

### Live View Not Loading
- Check browser session status is "Ready" in Console
- Ensure you have `ConnectBrowserLiveViewStream` permission

## Files

```
legacy-system-automation-agentcore/
├── run-demo.ps1                    # One-click demo runner (Windows)
├── run-demo.sh                     # One-click demo runner (Linux/macOS)
├── create_ticket_agentcore.py      # Main demo script
├── requirements.txt                # Python dependencies
├── README.md                       # This file
├── ARCHITECTURE.md                 # Technical architecture
└── infrastructure/
    └── cdk/
        ├── app.py                  # CDK app entry point
        ├── stack.py                # CDK stack definition
        ├── cdk.json                # CDK configuration
        └── requirements.txt        # CDK Python dependencies
```

### Shared Scripts

This demo uses the shared scripts for prerequisite validation and CDK deployment:

```
shared/
└── scripts/
    ├── check-prerequisites.ps1     # Shared prereq validation (Windows)
    ├── check-prerequisites.sh      # Shared prereq validation (Linux/macOS)
    ├── deploy-cdk.ps1              # Shared CDK deployment (Windows)
    └── deploy-cdk.sh               # Shared CDK deployment (Linux/macOS)
```

## Deployment Standard

All demos in this repository follow the same deployment pattern:
1. **Shared prerequisites check** via `shared/scripts/check-prerequisites.*`
2. **Infrastructure deployment** via AWS CDK (Python)
3. **Demo execution** via Python scripts

This ensures consistent experience across all demos and proper infrastructure management.

## Cleanup

To destroy CDK infrastructure:

```powershell
.\run-demo.ps1 -DestroyInfra
```

```bash
./run-demo.sh --destroy-infra
```

Or manually:

```bash
cd infrastructure/cdk
npx -y cdk destroy --force --no-cli-pager
```

To delete the Nova Act workflow definition (created via CLI):

```bash
aws nova-act delete-workflow-definition --name legacy-system-automation-agentcore --region us-east-1 --no-cli-pager
```

## Resources

- [AgentCore Browser Documentation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/browser-building-agents.html)
- [AgentCore CloudFormation Resources](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/AWS_BedrockAgentCore.html)
- [Nova Act Documentation](https://aws.amazon.com/nova/act/)
- [Nova Act Gym (Next-Dot Demo Site)](https://nova.amazon.com/act/gym/next-dot)
