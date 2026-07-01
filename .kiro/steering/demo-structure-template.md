---
inclusion: always
---

# Demo Structure Template

Use this template when creating new GenAI Ops demos.

## Required Demo Page Structure

```markdown
# [Demo Name]
*[Problem Subtitle - A short, user-centric statement describing the pain point this demo addresses using [Main AWS Technology/Service]]*

## Overview
Brief description of what the demo shows and the problem it solves.

## At a Glance
- **Duration**: [15/20/25/30] minutes
- **Difficulty**: [Beginner/Intermediate/Advanced]
- **Target Audience**: [List roles]
- **Key Technologies**: [List AWS services and tools]
- **Estimated Cost**: [Cost range per month/hour with breakdown]

## Business Value
Why should AWS customers care? What operational pain does this address?

## What You'll See
Step-by-step outline of the demo flow 

## Prerequisites
- AWS account requirements
- Required services/permissions
- Any setup needed before the demo

## Estimated Cost Breakdown
Detailed monthly cost estimate including:
- Bedrock model inference costs (based on typical usage patterns)
- Storage costs (S3, DynamoDB, etc.)
- Compute costs (Lambda, ECS, etc.)
- Data transfer costs
- Other service costs
- Cost optimization tips

**Format**: Provide both demo cost (one-time for testing) and production cost (monthly ongoing)

## Code Repository
Link to GitHub/GitLab repository with:
- Source code
- Deployment guide
- Architecture diagram

## Available Extensions
- Workshop version (if available)
- Custom integration options
- Advanced configurations
```

## Repository Structure Template

```
demos/[demo-name]/
├── README.md                   # Demo overview, setup, usage
├── ARCHITECTURE.md             # Architecture diagram and explanation
├── src/                        # Source code
├── infrastructure/             # AWS CDK infrastructure (TypeScript or Python)
├── sample-data/                # Test data or scenarios (if applicable)
└── docs/                       # Additional documentation (optional)
```

## Example Implementations

See these live examples in the repository:

### Complete Demo Examples
- **Password Reset Chatbot**: #[[file:operations-automation/ai-password-reset-chatbot/README.md]]
- **Lifecycle Tracker**: #[[file:operations-automation/aws-services-lifecycle-tracker/README.md]]
- **IT Portal Demo**: #[[file:operations-automation/anycompany-it-demo-portal/README.md]]
- **Chaos Engineering**: #[[file:resilience/ai-chaos-engineering-with-fis/README.md]]

### CDK App File Examples
- **TypeScript App**: #[[file:operations-automation/ai-password-reset-chatbot/cdk/bin/app.ts]]
- **Python App**: #[[file:operations-automation/anycompany-it-demo-portal/infrastructure/cdk/app.py]]

### Architecture Documentation Examples
- **Detailed Architecture**: #[[file:operations-automation/anycompany-it-demo-portal/ARCHITECTURE.md]]
- **Technical Design**: #[[file:operations-automation/aws-services-lifecycle-tracker/ARCHITECTURE.md]]

### Deployment Script Examples
- **PowerShell**: #[[file:operations-automation/anycompany-it-demo-portal/deploy-all.ps1]]
- **Bash**: #[[file:operations-automation/anycompany-it-demo-portal/deploy-all.sh]]

