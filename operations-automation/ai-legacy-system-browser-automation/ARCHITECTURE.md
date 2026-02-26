# Legacy System Browser Automation - Architecture

## Overview

This demo automates IT operations workflows on legacy web applications using Amazon Nova Act and AgentCore Browser Tool. An AI agent monitors email for requests, then autonomously navigates legacy web portals to fulfill those requests.

## Architecture Diagram

```
┌────────────────────────────────────────────────────────────────────────────────┐
│                              OPERATIONS AUTOMATION                             │
│                                                                                │
│  ┌─────────────┐     ┌──────────────────────────────────────────────────────┐  │
│  │   Outlook   │     │              AWS Cloud                               │  │
│  │   Inbox     │     │                                                      │  │
│  │             │     │  ┌─────────────┐    ┌─────────────┐                  │  │
│  │  "NEW       │     │  │  Nova Act   │    │  AgentCore  │                  │  │
│  │  EMPLOYEE   │────>│  │  AI Model   │───>│  Browser    │                  │  │
│  │  ORDER"     │     │  │             │    │  Tool       │                  │  │
│  │             │     │  └─────────────┘    └──────┬──────┘                  │  │
│  └─────────────┘     │                            │                         │  │
│                      │                            ▼                         │  │
│  ┌─────────────┐     │  ┌────────────────────────────────────────────────┐  │  │
│  │   Mail      │     │  │           Legacy IT Portals                    │  │  │
│  │   Polling   │────>│  │    ┌──────────┐ ┌──────────┐ ┌──────────────┐  │  │  │
│  │   Service   │     │  │    │   ITSM   │ │Inventory │ │ Procurement  │  │  │  │
│  │  (Python)   │     │  │    │  Portal  │ │  Portal  │ │   Portal     │  │  │  │
│  └─────────────┘     │  │    └──────────┘ └──────────┘ └──────────────┘  │  │  │
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

## Component Architecture

### Mail Polling Service
- Python service monitoring Outlook inbox via COM/MAPI
- Detects emails matching configurable subject pattern
- Parses employee data and equipment requirements from email body
- Triggers onboarding orchestrator with parsed request

### Browser Automation Engine
- Nova Act provides natural language browser control
- AgentCore Browser Tool runs Chrome in AWS cloud (no local browser)
- JSON-driven workflow definitions in `workflows/new_employee_onboarding_actions.json`
- Session recordings stored in S3 for audit trails

### Onboarding Orchestrator
- Coordinates multi-portal workflow execution
- Manages state across ITSM, Inventory, and Procurement portals
- Handles error recovery and step-by-step execution
- Sends SES notifications on completion

## Infrastructure (CDK)

### Resources Deployed
- AgentCore Browser Tool (custom browser with recording)
- S3 bucket for session recordings and workflow data
- IAM execution role with minimal permissions
- CloudWatch Logs integration

### Authentication
- AWS IAM credentials via `@workflow` decorator
- AgentCore Browser uses service-linked role
- S3 access scoped to recordings bucket only

## Data Flow

1. Email arrives in Outlook inbox
2. Mail polling service detects matching subject
3. Email parser extracts employee details and equipment list
4. Orchestrator opens AgentCore Browser session
5. Nova Act navigates ITSM portal → creates ticket
6. Nova Act navigates Inventory portal → checks stock per item
7. Nova Act navigates Procurement portal → creates POs for out-of-stock items
8. Nova Act returns to ITSM portal → resolves ticket
9. SES sends completion notification to requester

## Design Decisions

### JSON-Driven Workflows
All browser actions are defined in JSON rather than hardcoded in Python. This allows adapting the automation to different portals by editing the JSON file without modifying code.

### Hybrid Execution Modes
The system supports both real browser automation (with AgentCore) and simulation mode (without browser). This enables development and testing without AWS infrastructure.

### Environment-Based Configuration
All runtime configuration (CloudFront domain, browser ID, region) comes from environment variables, ensuring no hardcoded values and cross-account compatibility.
