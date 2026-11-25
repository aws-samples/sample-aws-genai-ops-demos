---
name: health-event-impact-assessment
description: Evaluates AWS Health event and AWS Security Hub finding impact on
  application workloads using topology knowledge. Determines blast radius,
  affected teams, and notification routing. Use this skill when investigating
  incidents triggered by AWS Health events (scheduled maintenance, operational
  issues, service degradation) or by HIGH/CRITICAL AWS Security Hub findings.
---

# Health Event Impact Assessment

Use this skill when an investigation is triggered by an AWS Health event. Your goal
is to assess the impact on workloads in the topology and determine which teams
need to be notified.

## Which path to run

If the investigation prompt contains the tag `[EVENT_TYPE:securityhub-finding]`,
run the **Security Hub Finding Impact Assessment** path (see the section of that
name below). Otherwise, run the **Health Event** path described in Steps 1–6.
Both paths produce the identical output contract in `## Output Format`.

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

When the same underlying condition recurs across investigations (same event type,
same resource class, same redundancy posture), assign it the **same severity** each
time. Base the severity strictly on the criteria above (service availability impact,
redundancy) — never on investigation-to-investigation wording choices. Do not
upgrade or downgrade a recurring fact pattern based on how you happen to phrase it
this time.

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

## Delegation and Sub-Agent Output

If any part of your analysis, mitigation plan, or recommendations was produced by a
sub-agent (e.g. `propose-mitigation`) and delivered to you as a separate artifact,
you MUST still restate its substance inline in this response's `## Key Findings` and
`## Recommended Actions` sections below. The artifact is a supplement for a human who
wants full detail — it is never a substitute for the structured sections the
callback parser reads.

Never write a sentence that merely refers to the artifact without summarizing it.
Forbidden pattern (do not write anything like this):

> "...reaches you as its own artifact — I won't duplicate it here."

Instead, lift the plan's substance into the structured sections. Template for pulling
1-3 action items out of a delegated plan into `## Recommended Actions`:

| Priority | Action |
|----------|--------|
| **P1**   | <first action item from the sub-agent's plan, restated in your own words> |
| **P2**   | <second action item, if present> |
| **P3**   | <third action item, if present> |

Do the same for findings: restate each affected workload/resource the sub-agent's
plan addressed as its own `## Key Findings` bullet (see the rules below), even
though a fuller version of that resource's mitigation exists in the artifact.

### Final Message Is the Complete Report

The callback reads your journal and takes each section from the most recent message
that contains it, so a partial final message silently overrides or loses content.
When a sub-agent or background task returns after you already wrote the report and
its result changes your assessment (a corrected fact, a new severity, an updated
plan), your final message MUST re-send the complete structured report: all six
headings (`## Summary`, `## Key Findings`, `## Answers to Your Questions`,
`## Recommended Actions`, `## Notification Routing`, `## Jira Tracking`) with the
updated content. Do not send a short addendum or a lone recommendations table, and do
not put a correction in a lead-in paragraph before the headings. Put it inside the
relevant section. (On the "no impact" path below, the report is just `## Summary`.)

Every row of the `## Recommended Actions` table must be self-contained: write the
concrete command or step inline in the row. Never write "the command above", "as
described above" or any other reference to text outside the table. Only the table
rows reach the notification channel.

### Re-notifying on an Existing Finding

When this investigation determines there is no new information since a prior
notification for the same event/finding (for example, a repeat Inspector2 scan sweep
that re-confirms earlier CVEs), still write a normal, factual `## Summary` describing
the *finding* — what the batch is, that it is a repeat, and any delta from last time.
Never write a `## Summary` that is a meta-comment about the act of re-reporting.

Forbidden pattern (do not write anything like this):

> "No new information — these are more CVEs from the same Inspector2 scan sweep..."

Instead write, for example: "Inspector2 re-scan confirms 6 previously reported HIGH
CVEs on `web-tier`; no new findings since the last notification."

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

## Security Hub Finding Impact Assessment

Run this path **only** when the prompt contains `[EVENT_TYPE:securityhub-finding]`.
It assesses the operational impact of an AWS Security Hub finding on the
workloads in the topology, then produces the **same** output as the Health path.

The `Maintenance Window:` line in the prompt is **not applicable** to a security
finding — ignore it. Findings have no maintenance window (the trigger emits
`Not specified` for this line by design).

The defining difference from the Health path: **the impacted resource ARNs are
already known** — they are listed in the prompt's `Affected Resources` block.
A single investigation may now represent multiple CRITICAL findings
aggregated onto the same primary resource (plus any resources those findings
jointly reference) rather than exactly one finding — treat the full `Affected
Resources` list and the finding count implied by the description as the scope
of this investigation. You do **not** need to discover the affected resources.
Instead:

1. **Start from the given finding ARNs** as the impacted resource set.
2. **Correlate each ARN with the application topology and owning teams** using
   the same technique as the Health path's Step 4 (resource tags such as Team,
   Owner, Department, CostCenter; CloudFormation stack ownership; topology
   groupings and service boundaries).
3. **Assess real impact**: is each resource actually part of a monitored
   workload? What is the blast radius through its dependencies? Who owns it?
   A finding on a resource that is not in any monitored workload has no
   operational impact (see "What \"no impact\" looks like" below).
4. **Assign per-workload severity** using the **same** CRITICAL / HIGH / MEDIUM /
   LOW scale and the **same** "overall = highest individual workload severity"
   rule as the Health path's Step 3.
5. **Produce recommendations and notification routing exactly as the Health
   path does** (Steps 5 and 4).

Jira ticket tracking: reuse **Step 6 above verbatim** — the same
`[JIRA_CONFIG:...]` trigger condition, the same MEDIUM+ threshold, the same
search-before-create de-dup JQL, the `[Health]` summary prefix, and the
`["aws-health-event", "auto-created"]` labels. The JQL's `text ~ "<eventArn or
eventTypeCode>"` clause still de-dups correctly because it matches on the
finding's unique correlation id. Do not introduce new labels or prefixes.

Emit your final response using the **existing** `## Output Format`,
`### Heading rules`, and `### What "no impact" looks like` sections below
unchanged. Do not add or rename any section.

## Output Format

Your final response **must** use the markdown structure below verbatim.
The investigation callback Lambda parses these specific section headings;
free-form prose without these headings will result in empty findings.

```
## Summary

<one-paragraph, third-person, factual impact statement, ≤ 500 chars — see rules below>

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
- `## Summary` must contain only factual, third-person impact statements — what is
  affected, how severely, and why. It must **never** contain first-person narration
  about your own process, confidence, delegation, or next steps (e.g. "I now
  have...", "Let me...", "I've confirmed...", "I won't duplicate..."). If unsure what
  to write, default to this shape: `<N> <category> finding(s) affecting
  <workload/resource>; <one-line impact>.`
- Each finding bullet must follow the `- **<title>**: <detail>` shape.
  The parser extracts the bolded title and the detail after the colon.
- `## Key Findings` is **mandatory** whenever there is any impact (the only
  exception is the "no impact" path below, which omits it entirely). Every bullet's
  bold label MUST be the name of an affected workload or resource — never
  `Mitigation`, `Jira`, `Tracking`, `Impact`, `Severity`, `Root Cause`, a date, or
  any other meta-label. Mitigation status, Jira ticket status, and tracking info
  belong only in `## Notification Routing` / `## Jira Tracking`, never here. If,
  after full analysis, there are genuinely no affected resources but impact still
  exists in some other sense, include a single explicit bullet such as
  `- **No affected resources identified**: impact assessed as <severity> based on
  <reason>.` rather than omitting the section — the notifier depends on `##
  Key Findings` always being present when there is impact.
- Recommendations must be in a markdown table whose first column is
  `**P1**` / `**P2**` / `**P3**` (or higher). The parser maps these
  priority labels onto OpsItem priority levels. Each row must be self-contained (see
  "Final Message Is the Complete Report").
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
