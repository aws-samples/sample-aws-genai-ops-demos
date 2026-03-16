# New Employee Onboarding Scenario

## Overview

This document describes the end-to-end automated workflow for processing a new employee equipment request. An email triggers the mail-polling service, which parses the request and drives Nova Act browser automation across three legacy IT portals (ITSM, Inventory, Procurement) using AgentCore Browser Tool.

---

## Sample Email

Send this email to your Outlook inbox to trigger the workflow:

**Subject:** `NEW EMPLOYEE ORDER - John Doe - CISO Equipment Setup`

**Body:**
```
Dear IT Team,

Please process the following equipment request for our new Chief Information Security Officer.

Employee Details:
- Name: John Doe
- Position: Chief Information Security Officer (CISO)
- Department: Information Security
- Start Date: March 1, 2026
- Manager: Executive Team

Equipment Requested:
1. Professional Laptop 16" - High-performance laptop for executive use
2. Office 365 License - Productivity software suite

Budget Code: EXEC-2026-Q1
Priority: High

Please ensure all equipment is ready before the employee's start date.

Best regards,
Human Resources
```

The email parser extracts:
- Employee name, position, department, start date, manager
- Equipment items with auto-categorized types (Laptops, Peripherals, Mobile Devices, Software)
- Budget code and priority
- Requester email (from the sender field)

---

## Workflow Phases

### Phase 1: Email Detection

The mail-polling service polls Outlook every 30 seconds for emails with subjects containing "NEW EMPLOYEE ORDER".

1. `EmailMonitor.scan_inbox()` finds the email
2. `email_parser.parse_onboarding_email()` extracts structured data
3. `OnboardingOrchestrator.execute()` is called with the parsed request
4. Nova Act opens a browser session via AgentCore Browser Tool

### Phase 2: Create ITSM Ticket

Portal: `https://<cloudfront-domain>/itsm.html`

The AI navigates to the ITSM portal and creates a ticket:
- Title: `Onboarding: John Doe - Chief Information Security Officer (CISO)`
- Description: Full equipment list with employee details
- Category: Hardware Request
- Priority: High
- Captures the generated ticket ID (e.g., `INC-370237`)

### Phase 3: Update Ticket to In Progress

Same portal. The AI locates the ticket and updates its status from Open to In Progress.

### Phases 4-6: Process Each Equipment Item

For each of the 5 items, the workflow runs these steps:

#### Phase 4: Check Inventory

Portal: `https://<cloudfront-domain>/inventory.html`

The AI searches for the item and reads the stock level. Items map to categories and vendors:

| Item | Category | Vendor | Unit Price |
|------|----------|--------|------------|
| Professional Laptop 16" | Laptops | TechCorp Solutions | $2,499.00 |
| Wireless Mouse & Keyboard Combo | Peripherals | PeripheralTech | $199.00 |
| Noise Cancelling Headset | Peripherals | PeripheralTech | $199.00 |
| iPhone 16e | Mobile Devices | TabletCorp | $799.00 |
| Office 365 License | Software | SoftwarePro Inc | $299.00 |

#### Phase 5: Procurement (if stock < 1)

Portal: `https://<cloudfront-domain>/procurement.html`

For out-of-stock items, the AI runs the full procurement cycle:
1. **Create PO** — Selects vendor from dropdown, fills item name, quantity (10 bulk), unit price, budget code
2. **Submit PO** — Selects the PO row checkbox, clicks Submit Selected
3. **Approve PO** — Selects the PO row checkbox, clicks Approve Selected
4. **Receive Delivery** — Clicks Receive Delivery on the approved PO

Then returns to Inventory to add the item (10 units, with manufacturer, model, location).

#### Phase 6: Allocate Item

Portal: `https://<cloudfront-domain>/inventory.html`

The AI searches for the item and allocates 1 unit to the employee (decrements stock by 1).

### Phase 7: Resolve Ticket

Portal: `https://<cloudfront-domain>/itsm.html`

The AI locates the ticket and updates status from In Progress to Resolved.

### Phase 8: Send Email Notification

Uses Amazon SES (API call, no browser). Sends a completion email to the original requester with:
- Employee name and ticket ID
- List of all allocated equipment
- Link to the ITSM portal for reference

---

## Workflow Actions (JSON-Driven)

All browser actions are defined in `workflows/new_employee_onboarding_actions.json`. The orchestrator loads these definitions and executes them dynamically with variable substitution. Key workflows:

| Workflow Name | Portal | Steps |
|---------------|--------|-------|
| `itsm_create_ticket` | ITSM | Navigate, click Create, fill form, submit, capture ticket ID |
| `itsm_update_status` | ITSM | Find ticket, click Update, select status, confirm |
| `inventory_search` | Inventory | Search for item, read stock level |
| `inventory_add_item` | Inventory | Click Add Item, fill form, submit |
| `inventory_allocate` | Inventory | Search item, select, allocate to employee |
| `procurement_create_po` | Procurement | Click Create PO, fill form, submit |
| `procurement_submit_po` | Procurement | Select PO, click Submit Selected |
| `procurement_approve_po` | Procurement | Select PO, click Approve Selected |
| `procurement_receive` | Procurement | Click Receive Delivery on PO |

---

## Execution Summary

A typical run processes 5 items across ~90 Nova Act actions in ~60 minutes:

| Phase | Action | Portal |
|-------|--------|--------|
| 1 | Detect email, parse request | Mail Polling |
| 2 | Create ticket | ITSM |
| 3 | Update ticket → In Progress | ITSM |
| 4-6 | Check stock → Procure if needed → Allocate (×5 items) | Inventory + Procurement |
| 7 | Resolve ticket | ITSM |
| 8 | Send notification email | SES (API) |

### Monitoring

- Live browser view: AWS Console → Bedrock AgentCore → Built-in Tools → select browser → View live session
- Workflow steps: Nova Act console → Workflow Definitions → `onboarding-email-workflow`
- Session recordings: `s3://legacy-automation-recordings-{account-id}/browser-recordings/`
- Workflow data: `s3://legacy-automation-recordings-{account-id}/workflow-data/`

---

## Technical Components

| Component | File | Purpose |
|-----------|------|---------|
| Mail Polling | `ai-legacy-system-browser-automation/mail-polling/src/email_monitor.py` | Monitors Outlook, triggers workflow |
| Email Parser | `ai-legacy-system-browser-automation/ai-browser-automation/email_parser.py` | Extracts structured data from email |
| Orchestrator | `ai-legacy-system-browser-automation/ai-browser-automation/onboarding_orchestrator.py` | Coordinates all phases |
| Browser Actions | `ai-legacy-system-browser-automation/ai-browser-automation/browser_actions.py` | Executes JSON workflow definitions |
| Workflow Definitions | `ai-legacy-system-browser-automation/ai-browser-automation/workflows/new_employee_onboarding_actions.json` | All browser actions as JSON |
| Config | `ai-legacy-system-browser-automation/ai-browser-automation/onboarding_config.py` | Portal URLs, AWS settings |
| SES Notifier | `ai-legacy-system-browser-automation/ai-browser-automation/ses_notifier.py` | Sends completion email |
| Ticket Formatter | `ai-legacy-system-browser-automation/ai-browser-automation/ticket_formatter.py` | Formats ticket title/description |
