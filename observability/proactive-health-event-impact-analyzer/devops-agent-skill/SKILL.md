---
name: health-event-impact-assessment
description: Evaluates AWS Health event impact on application workloads using
  topology knowledge. Determines blast radius, affected teams, and notification
  routing. Use this skill when investigating incidents triggered by AWS Health
  events including scheduled maintenance, operational issues, and service
  degradation notifications.
---

# Health Event Impact Assessment

Use this skill when an investigation is triggered by an AWS Health event. Your goal
is to assess the impact on workloads in the topology and determine which teams
need to be notified.

## Step 1: Identify Affected Resources

From the incident data, extract:
- The AWS service affected (EC2, RDS, Lambda, etc.)
- The region and availability zone
- The maintenance window or event timeline
- Specific resource IDs listed as affected

Cross-reference these with the application topology to find which applications
use the affected resources directly or transitively through dependency chains.

## Step 2: Assess Blast Radius

For each affected application in the topology:

1. **Direct impact**: Resources explicitly listed in the Health event
2. **Transitive impact**: Downstream services that depend on affected resources
   (e.g., an ALB routing to affected EC2 instances, an application reading from
   an affected RDS instance)
3. **Redundancy evaluation**: Check if the application has:
   - Multi-AZ deployment (resources in other AZs can absorb traffic)
   - Auto Scaling groups that can replace affected instances
   - Read replicas or failover configurations
   - Cross-region redundancy

## Step 3: Determine Impact Severity

Assign severity per affected workload:

- **CRITICAL**: Service will be unavailable, no redundancy, customer-facing
- **HIGH**: Significant degradation expected, partial redundancy insufficient
- **MEDIUM**: Some impact but redundancy mechanisms should handle it
- **LOW**: Minimal impact, full redundancy in place, automatic recovery expected

Overall investigation severity = highest individual workload severity.

## Step 4: Identify Responsible Teams

For each affected workload, determine the owning team by checking:
1. Resource tags (Team, Owner, Department, CostCenter)
2. CloudFormation stack ownership (stack tags, stack name patterns)
3. Application topology groupings and service boundaries

Build a notification routing list with:
- Team name/identifier
- Contact method (email, Slack channel, MS Teams channel)
- Severity of impact on their workload
- Specific resources they own that are affected

## Step 5: Generate Recommendations

For each affected workload, provide actionable recommendations:
- Pre-event mitigations (migrate instances, failover databases, scale out)
- During-event monitoring (what metrics to watch, what alarms to set)
- Post-event validation (health checks, data integrity verification)

Prioritize recommendations by:
1. Time sensitivity (maintenance window approaching)
2. Impact severity (CRITICAL workloads first)
3. Effort required (quick wins before complex changes)

## Step 6: Jira ticket tracking (when Jira is configured)

This step is **optional** and runs only when the investigation prompt
contains a `[JIRA_CONFIG:...]` tag. If the tag is missing or empty, skip
this step entirely — ticket creation is not configured for this
deployment, or the trigger Lambda couldn't read the routing config.

### Where to find the Jira config

The trigger Lambda inlines a JSON tag at the top of the first user
message, right after `[CORRELATION_ID:...]`. The shape is:

```
[JIRA_CONFIG:{"projectKey":"OPS","issueType":"Task","siteUrl":"https://acme.atlassian.net"}]
```

**Do NOT** call `ssm:GetParameter` or `ssm:GetParameters` for these
values — your session policy will deny those calls. The tag in the prompt
is the canonical source of truth.

### Trigger conditions

Run this step only when:
1. `[JIRA_CONFIG:...]` is present in the prompt, AND
2. Overall severity is **MEDIUM, HIGH, or CRITICAL**.

Skip silently for LOW and NONE — the OpsItem already provides sufficient
tracking.

### Search before create (de-dup)

Use `searchJiraIssuesUsingJql` first to avoid creating duplicate tickets
for the same Health event. Match on the Health event ARN (or the
`eventTypeCode` plus the AWS account, when ARN is unavailable):

```
project = "<projectKey from tag>"
  AND labels = "aws-health-event"
  AND text ~ "<eventArn or eventTypeCode>"
  AND statusCategory != Done
```

If a matching ticket exists, use `addCommentToJiraIssue` to append the
new findings. **Do not edit fields, transition the ticket, or create a
duplicate.** The agent has only read, search, and `createJiraIssue` /
`addCommentToJiraIssue` tools available — by design.

### Create new ticket

If no match exists, call `createJiraIssue` with:

- **project**: `projectKey` from the JIRA_CONFIG tag
- **issuetype**: `issueType` from the tag
- **summary**: `[Health] <eventTypeCode> — <severity>` (≤ 200 chars)
- **labels**: `["aws-health-event", "auto-created"]`
- **description**: a structured block containing
  - The Health event ARN, region, AZ, start/end times
  - The full `## Summary` section verbatim
  - The full `## Key Findings` section
  - A pointer to the OpsItem ID (from the same investigation)

## Output Format

Your final response **must** use the markdown structure below verbatim.
The investigation callback Lambda parses these specific section headings;
free-form prose without these headings will result in empty findings.

```
## Summary

<one-paragraph executive summary, ≤ 500 chars>

## Key Findings

- **<workload-name>**: <impact severity> — <one-line impact statement>.
  Affected resources: <comma-separated IDs>. Redundancy: <yes/no/partial>.
- **<workload-name>**: ...
- **<workload-name>**: ...

## Answers to Your Questions

<freeform analysis, references the investigation prompts>

## Recommended Actions

| Priority | Action |
|----------|--------|
| **P1**   | <highest-priority action> |
| **P2**   | <next action> |
| **P3**   | <next action> |

## Notification Routing

For each owning team:
- **<team-id>**: <severity> impact on <workloads>. Contact via <channel>.

## Jira Tracking

- **Action**: created | commented | skipped
- **Issue key**: <PROJECTKEY-NNNN>  (when created or commented)
- **Reason for skip**: <severity below threshold | SSM not configured>
```

### Heading rules

- Use `## Summary`, `## Key Findings`, `## Answers to Your Questions`,
  `## Recommended Actions`, `## Notification Routing`, and
  `## Jira Tracking` exactly. The callback parser pattern-matches on these.
- Each finding bullet must follow the `- **<title>**: <detail>` shape.
  The parser extracts the bolded title and the detail after the colon.
- Recommendations must be in a markdown table whose first column is
  `**P1**` / `**P2**` / `**P3**` (or higher). The parser maps these
  priority labels onto OpsItem priority levels.
- If there is genuinely no impact, still emit the `## Summary` section
  with one paragraph explaining why, and skip the rest. Do not fabricate
  empty sections — the parser interprets emptiness correctly.

### What "no impact" looks like

When the affected resources do not appear in any workload topology, or
they are fully redundant such that the event causes no service disruption,
emit:

```
## Summary

<one paragraph explaining no operational impact, why redundancy
absorbs the event, and that no action is required>
```

…and stop. Do not include `## Key Findings`, `## Recommended Actions`,
or `## Jira Tracking` sections. The callback will mark the investigation
as `NO_IMPACT` based on the absence of findings/recommendations and the
keywords "no operational impact" / "no immediate operational" /
"no workloads".
