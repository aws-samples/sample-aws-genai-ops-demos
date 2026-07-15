# Jira integration

The Proactive Health Event Impact Analyzer can create and comment on Jira
tickets when the AWS DevOps Agent confirms a Health event has impact on your
workloads. This document covers setup, runtime behavior, and troubleshooting.

## How it works

1. The setup wizard registers the [Atlassian Rovo MCP Server](https://support.atlassian.com/atlassian-rovo-mcp-server/docs/getting-started-with-the-atlassian-remote-mcp-server/)
   as a custom MCP server in AWS DevOps Agent (account-level), authenticated
   via API token (HTTP Basic auth).
2. The wizard probes the Atlassian server via the MCP `tools/list` handshake
   to discover the actual tool names exposed to your token, then associates
   the MCP server with your Agent Space and allow-lists exactly those tools.
3. The wizard writes routing config (project key, issue type, site URL) to
   SSM Parameter Store under `/health-analyzer/jira/*`.
4. At investigation time, the **investigation-trigger Lambda** reads those
   SSM parameters (its IAM role grants it that permission) and inlines the
   values as a `[JIRA_CONFIG:{...}]` tag at the top of the prompt sent to
   AWS DevOps Agent.
5. The agent's skill (`devops-agent-skill/SKILL.md`) reads the JIRA_CONFIG
   tag from the prompt and decides whether to:
   - search for an existing ticket and **comment** on it, or
   - **create** a new ticket, or
   - **skip** Jira entirely (when severity is LOW/NONE or the JIRA_CONFIG
     tag is absent).

> **Why inline the config in the prompt instead of having the agent read
> SSM directly?** The agent runs under a session policy that strips its
> role's `ssm:GetParameter*` permissions. Even with an inline policy on
> the agent role, the agent reports `AccessDeniedException` at runtime.
> Reading SSM in the trigger Lambda (whose IAM is unaffected by session
> policies) and passing the config in-band avoids that limitation.

Tickets are only filed when overall severity is **MEDIUM, HIGH, or CRITICAL**.
The OpsItem remains the system of record; Jira is the team-facing tracker.

---

## Step 1 — Create a Jira Cloud site (if you don't already have one)

1. Sign up at [atlassian.com/try/cloud/signup](https://www.atlassian.com/try/cloud/signup).
   Free tier covers up to 10 users — plenty for this integration.
2. Pick a site name. You'll get a URL like `https://<your-site>.atlassian.net`.
3. Create a Jira project. Note the **project key** (e.g. `OPS`, `KAN`,
   `HEALTH`) — the wizard prompts for it. Any template (Kanban is simplest)
   works.

---

## Step 2 — Configure the Atlassian admin side

These steps require **organization admin** rights. On a fresh free-tier
site, you are the org admin automatically.

Open [admin.atlassian.com](https://admin.atlassian.com), pick your
organization, then navigate to **Rovo → Rovo MCP server**.

### 2a — Authentication tab → enable API token auth

This is **off by default**. With it disabled, the API-token flow returns
401 immediately and the wizard cannot register or probe the server.

1. Click the **Authentication** tab.
2. Tick **Allow API token authentication**.

The change takes effect immediately, no save required.

### 2b — Permissions tab → allow Read, Write, Search for Jira

This is **the most-missed step**. Even with authentication enabled, if the
Permissions tab doesn't allow the Read/Write/Search permission groups for
Jira, the MCP server returns **only the Teamwork Graph tools** to your
token — none of the Jira tools the wizard needs.

1. Click the **Permissions** tab.
2. For each row — **Read**, **Write**, **Search** — set the status to
   **Allowed** (use the row checkboxes, or **Select all permissions**).
3. (Optional, for tighter scoping) Click **Edit details** on each row and
   tick only the **Jira** app, untick Confluence/Bitbucket/JSM/Compass if
   you don't want them.

The minimum needed for this integration:

| Permission row | What we need |
|---|---|
| Read   | Allowed (or Edit details → Jira ticked) |
| Write  | Allowed (or Edit details → Jira ticked) |
| Search | Allowed (or Edit details → Jira ticked) |

### 2c — (Optional) Domains tab

Leave this empty. Domain allowlisting only applies to OAuth 2.1 clients;
API-token clients bypass it. Reference: [Control Atlassian Rovo MCP server settings](https://support.atlassian.com/security-and-access-policies/docs/control-atlassian-rovo-mcp-server-settings/).

---

## Step 3 — Create a scoped Atlassian API token

> **Critical**: You must use **"Create API token with scopes"**, not the
> plain "Create API token" button. An unscoped token works for legacy REST
> APIs but exposes **only the Teamwork Graph tools** through the Rovo MCP
> server. The wizard will then fail with "no matching tools discovered."

1. Open [id.atlassian.com/manage-profile/security/api-tokens](https://id.atlassian.com/manage-profile/security/api-tokens).
2. Click **Create API token with scopes** (NOT plain "Create API token").
3. **Name**: anything descriptive, e.g. `health-event-analyzer-mcp`.
4. **Expires**: pick the longest available (typically 1 year).
5. **App**: select **Rovo MCP**. The page also shows other apps — Rovo MCP
   is the one we want.
6. **Scopes**: tick at least the three below. The wizard's tool-discovery
   step matches advertised tools against intent, so any extra Atlassian
   scopes you add (Confluence, Bitbucket, etc.) are simply ignored.

   **Required minimum** (corresponds to `read_jira` / `write_jira` /
   `search_jira` permission groups):

   - `read:jira-work`
   - `write:jira-work`
   - `search:jira-work`

   **Recommended additions** for richer behavior:

   - `read:me` — for `atlassianUserInfo`
   - `read:account` — for `getAccessibleAtlassianResources`
   - `read:jira-user` — for `lookupJiraAccountId` (assignee mapping)
   - `search:rovo:mcp` — for the unified `searchAtlassian` tool

   Atlassian's "Create API token with scopes" wizard usually pre-selects
   `Rovo MCP` defaults — those are typically broader than the strict
   minimum. That's fine; the wizard's allow-list ensures only the
   create/comment/search Jira tools end up in the agent's hands.

7. Click **Create token** and **copy it immediately**. Atlassian shows the
   token only once. Stash it in a password manager.

### What happens if you skip the scopes

If you reuse a token created via plain "Create API token", or you remove the
Jira scopes, the wizard will run, discover the server, and report:

```
✅ Discovered 2 tool(s) from the server.
❌ The MCP server returned tools, but none matched our intent
   (read/search/create/comment Jira). Tools advertised:
     - getTeamworkGraphContext
     - getTeamworkGraphObject
```

That's the symptom. The fix is to create a scoped token as above.

---

## Step 4 — Run the wizard

If this is your **first** wizard run (no deployment yet), run the full flow:

```bash
npx ts-node scripts/setup-wizard.ts
```

Step 7 ("Atlassian Jira integration") is the new opt-in step.

If you've **already deployed** and just want to add the Jira integration
to an existing Agent Space, use the focused flag — it skips IAM, webhook,
operator app, notification channels, and the CDK deploy:

```bash
npx ts-node scripts/setup-wizard.ts --jira-only

# or with everything pre-filled
npx ts-node scripts/setup-wizard.ts \
  --jira-only \
  --region eu-central-1 \
  --agent-space-id <your-agent-space-id>
```

You'll be asked for:

| Prompt | Example | Notes |
|---|---|---|
| Atlassian site URL | `https://acme.atlassian.net` | Validates against the `*.atlassian.net` pattern |
| Jira project key | `OPS` | The key you noted in Step 1; uppercased automatically |
| Default issue type | `Task` | Must match an issue type that exists in the chosen project |
| Atlassian email | `you@acme.com` | The email of the Atlassian account that owns the token |
| Atlassian API token | `ATATT...` | The token from Step 3. Treat as a secret — your terminal scrollback retains the paste |

The wizard then:

1. Probes the MCP server (`https://mcp.atlassian.com/v1/mcp/authv2`) using
   the MCP `initialize` → `notifications/initialized` → `tools/list`
   handshake to discover the real tool names your token can see.
2. Filters the discovered list against the desired intent
   (read/search/create/comment Jira), keeping the shortest matching name
   per intent.
3. Calls `aws devops-agent register-service --service mcpserver` with
   `authorizationConfig.apiKey.apiKeyValue = "Basic <base64(email:token)>"`,
   header `Authorization`. (Atlassian's MCP API-token flow uses HTTP Basic,
   not Bearer.)
4. Calls `aws devops-agent associate-service` against your Agent Space
   with the discovered tool list.
5. Writes three SSM parameters under `/health-analyzer/jira/*`. The
   investigation-trigger Lambda's IAM role is granted `ssm:GetParameter*`
   for this path (added by the CDK construct, no manual step needed).
   The agent's role is **not** modified — it can't usefully read SSM
   anyway because its session policy denies that.

A successful run looks like this:

```
ℹ️  Discovering available tools from the Atlassian MCP server...
✅ Discovered 44 tool(s) from the server.
ℹ️  Selected 9 matching tool(s): getAccessibleAtlassianResources,
    atlassianUserInfo, getVisibleJiraProjects,
    getJiraProjectIssueTypesMetadata, lookupJiraAccountId, getJiraIssue,
    searchJiraIssuesUsingJql, createJiraIssue, addCommentToJiraIssue
ℹ️  Registering Atlassian Jira MCP server with DevOps Agent...
✅ MCP server registered (id: 838defd1-...)
ℹ️  Associating MCP server with Agent Space and allow-listing 9 tool(s)...
✅ MCP server associated with Agent Space
ℹ️  Writing Jira routing config to SSM Parameter Store...
✅ SSM params written: /health-analyzer/jira/projectKey,
    /health-analyzer/jira/issueType, /health-analyzer/jira/siteUrl
```

---

## Step 5 — Re-upload the agent skill

The wizard wires AWS DevOps Agent to call Jira. The decision logic for
*when* to call Jira lives in the agent skill at `devops-agent-skill/SKILL.md`,
specifically Step 6 ("Jira ticket tracking"). After running the wizard for
the first time, you must re-upload the skill so the agent picks up Step 6:

1. Build the upload zip (run from the repo root):
   ```bash
   ( cd devops-agent-skill && \
     rm -f health-event-impact-assessment.zip && \
     zip -r health-event-impact-assessment.zip SKILL.md references/ )
   ```
   This produces `devops-agent-skill/health-event-impact-assessment.zip`
   (~5 KB, well under the 6 MB limit). The zip is gitignored.

2. Open the Operator Web App skills page:
   ```
   https://<agent-space-id>.aidevops.global.app.aws/skills
   ```
   Sign in with the same AWS identity you used for the wizard.

3. If the skill exists from a previous run:
   - **UI-created skill** (just SKILL.md content): click → **Edit** →
     paste the new SKILL.md contents → **Save**.
   - **Zip-uploaded skill** (file-tree view): click ⋮ → **Delete** →
     confirm. Then continue to step 4.

4. Click **Add skill** → **Upload skill** → drag-and-drop the zip from
   step 1 → keep **Generic** ticked under Agent Type → **Upload**.

5. Confirm the skill shows status **Active**.

> **Note**: AWS DevOps Agent does not currently expose a CLI or public API
> for skill upload — this step requires the Operator Web App. The CLI's
> `aidevops:GetKnowledgeItem` and `ListKnowledgeItems` actions are
> read-only. Source: [AWS DevOps Agent — DevOps Agent Skills](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-devops-agent-skills.html).

Without this step, the agent will run Steps 1–5 of the skill and silently
skip Step 6 even though the MCP integration and SSM params are correctly
set up. The investigation will return free-form prose, the callback parser
will see no `## Key Findings` section, and findings/recommendations come
back empty.

---

## Allow-listed tools

By design the agent has **read + search + create + comment** only. No edit,
no transition, no delete. This matches the project's principle of least
privilege and keeps the blast radius small if a prompt-injection attempt
slips through.

| Tool | Purpose | Required scope |
|---|---|---|
| `getAccessibleAtlassianResources` | Discover the cloudId for the site | `read:account`, `read:me` |
| `atlassianUserInfo` | Confirm the authenticated identity | `read:me` |
| `getVisibleJiraProjects` | One-time bootstrap on first run | `read:jira-work` |
| `getJiraProjectIssueTypesMetadata` | Resolve the issue type id | `read:jira-work` |
| `lookupJiraAccountId` | Map team owners to Jira accounts | `read:jira-work` |
| `getJiraIssue` | Read context on linked tickets | `read:jira-work` |
| `searchJiraIssuesUsingJql` | De-dup before creating a new ticket | `search:jira-work` |
| `createJiraIssue` | File a new ticket on confirmed impact | `write:jira-work` |
| `addCommentToJiraIssue` | Update an existing ticket on follow-up | `write:jira-work` |

Source: [Atlassian Rovo MCP Server — supported tools](https://support.atlassian.com/atlassian-rovo-mcp-server/docs/supported-tools/).

---

## Runtime behavior

The agent's skill at `devops-agent-skill/SKILL.md` Step 6 controls the
ticketing logic. Re-upload the skill to your Agent Space whenever you change
the routing rules.

The agent's investigation output includes a `JIRA TRACKING` block telling
the notifier whether a ticket was created, commented on, or skipped, and
which issue key (if any) to surface in team notifications.

---

## Re-running the wizard

The wizard is idempotent. On a re-run with Jira already configured it will:

- Detect the existing `atlassian-jira` MCP registration.
- Default to **reusing** it (recommended).
- Offer to **rotate** the API token instead — this disassociates the old
  registration from every Agent Space, deregisters it, and registers a new
  one. Pick this if you suspect the token is leaked or its expiry is
  approaching.
- Re-discover tools from the server on every run, so any newly-allowed
  tools are picked up automatically.
- Re-write the SSM parameters with whatever you re-enter at the prompts.

---

## Cleanup

`npx ts-node scripts/cleanup.ts` removes the Jira integration as part of
the standard teardown:

- **Step 2** disassociates all services from the Agent Space (including
  the `atlassian-jira` MCP association).
- **Step 3** deregisters all account-level services (including the
  `atlassian-jira` MCP server).
- **Step 5** deletes the three `/health-analyzer/jira/*` SSM parameters
  AND removes the `AllowReadHealthAnalyzerJiraSsmParams` inline policy
  from the `DevOpsAgentRole-AgentSpace` role.

The Atlassian API token is **not** revoked by cleanup. Revoke it manually
at [id.atlassian.com/manage-profile/security/api-tokens](https://id.atlassian.com/manage-profile/security/api-tokens)
when you're done.

---

## Troubleshooting

### Wizard reports "No matching tools discovered" with only 2 Teamwork Graph tools

The MCP server is reachable and your token is accepted, but neither
authentication nor permission-group exposure is set up correctly.

```
✅ Discovered 2 tool(s) from the server.
❌ The MCP server returned tools, but none matched our intent
   (read/search/create/comment Jira). Tools advertised:
     - getTeamworkGraphContext
     - getTeamworkGraphObject
```

Fix path:

1. **Check Step 3.** Was the token created with **"Create API token with
   scopes"**? Does it carry `read:jira-work`, `write:jira-work`,
   `search:jira-work`? An unscoped token only ever exposes the Teamwork
   Graph tools.
2. **Check Step 2b.** Are the **Read**, **Write**, **Search** rows on the
   Permissions tab set to **Allowed** for Jira?

After fixing either, re-run the wizard. The reuse path will skip
re-registering and discover the new tool surface.

### Wizard reports "could not probe the Atlassian MCP server"

Network or auth handshake failed. Common causes:

- Wrong email or token. The email must be the Atlassian account that
  owns the token, not an alias.
- The token was created without scopes (a plain non-scoped token returns
  401 / "Invalid token format" against the MCP endpoint).
- Step 2a is off — API token authentication is disabled at the org level.

### Investigation finishes but no Jira ticket appears

1. Confirm overall severity is MEDIUM or higher in the agent's output.
   LOW/NONE intentionally skips Jira.
2. **Confirm the skill has been re-uploaded.** This is the most common
   reason. The MCP integration can be perfect but if the agent is still
   running an older skill, it won't execute Step 6.
3. **Confirm the trigger Lambda is reading the SSM params.** Check its
   logs for `Jira config incomplete in SSM` or `Failed to read Jira
   config from SSM` warnings:
   ```bash
   aws logs tail /aws/lambda/<your-investigation-trigger-fn-name> \
     --since 30m --region $AWS_REGION --no-cli-pager \
     --filter-pattern "Jira"
   ```
   If you see those warnings, the SSM params are missing or the trigger
   Lambda's IAM role doesn't have the SSM permissions. Re-run the
   wizard — both the SSM writes and the IAM grant are idempotent.
4. Confirm the three SSM parameters exist in the correct region:
   ```bash
   aws ssm get-parameters --names \
     /health-analyzer/jira/projectKey \
     /health-analyzer/jira/issueType \
     /health-analyzer/jira/siteUrl \
     --region $AWS_REGION
   ```
5. Confirm the MCP association is present:
   ```bash
   aws devops-agent list-associations \
     --agent-space-id <your-space-id> \
     --region $AWS_REGION \
     --query "associations[?configuration.mcpserver]"
   ```
   Then inspect the discovered tools:
   ```bash
   aws devops-agent get-association \
     --agent-space-id <your-space-id> \
     --association-id <id-from-above> \
     --region $AWS_REGION
   ```
6. Open the operator app investigation link from the Step Functions output
   to see the agent's tool-call trace. If it shows zero tool calls or no
   reference to the Jira step, re-upload the skill.

### `register-service` fails with auth error

The most common cause is the API token format. The MCP server requires
**HTTP Basic auth**, not Bearer. The wizard handles this — it stores
`apiKeyValue = "Basic <base64(email:token)>"`. If you registered the MCP
server manually, double-check the value.

Source: [Atlassian Rovo MCP Server — authentication and authorization](https://support.atlassian.com/rovo/docs/authentication-and-authorization/).

### `register-service` rejects the name

The DevOps Agent constraint is `^[a-zA-Z0-9_-]+$` and ≤ 64 chars. The
wizard uses `atlassian-jira`, which fits. If you customize this, keep
those rules in mind.

### Agent says it does not have access to Jira tools

Re-run the wizard. The "associate" step is what gives the Agent Space
access to the allow-listed tools — registration alone is not enough.
Confirm the allow-listed tools list under your Agent Space's
**Capabilities → MCP Servers** view in the AWS console.

### IP allowlisting blocks the agent

If your Atlassian organization enforces IP allowlisting on Jira, you'll
need to allow the AWS DevOps Agent service's egress IPs. See
[Available Atlassian Rovo MCP server domains](https://support.atlassian.com/security-and-access-policies/docs/available-atlassian-rovo-mcp-server-domains/).
For a free personal tier this is rarely a concern.

---

## Security notes

- The API token is a **personal access token**. Tickets and comments
  created by the agent will be attributed to the user that owns the
  token. Treat it as you would any other privileged credential.
- Always create the token via **"Create API token with scopes"** and
  grant only the Jira scopes (and the small read/account scopes used by
  the bootstrap tools). Avoid reusing a broad-scope token across
  unrelated automations.
- The token never lands in CDK parameters, environment variables, or
  CloudFormation outputs. It only lives inside the AWS DevOps Agent
  service registration in your AWS account.
- AWS DevOps Agent does not currently expose a way to read back a stored
  API token after registration. Rotating the token requires the wizard's
  rotate flow (deregister + re-register).
- If a token leaks (e.g. accidentally pasted into chat or a log),
  **revoke it immediately** at
  [id.atlassian.com/manage-profile/security/api-tokens](https://id.atlassian.com/manage-profile/security/api-tokens),
  create a new one, and re-run the wizard with the rotate option.

---

## References

- [AWS DevOps Agent — Connecting MCP Servers](https://docs.aws.amazon.com/devopsagent/latest/userguide/configuring-capabilities-for-aws-devops-agent-connecting-mcp-servers.html)
- [Atlassian Rovo MCP Server — getting started](https://support.atlassian.com/atlassian-rovo-mcp-server/docs/getting-started-with-the-atlassian-remote-mcp-server/)
- [Atlassian Rovo MCP Server — supported tools](https://support.atlassian.com/atlassian-rovo-mcp-server/docs/supported-tools/)
- [Atlassian Rovo MCP Server — API token auth](https://support.atlassian.com/atlassian-rovo-mcp-server/docs/configuring-authentication-via-api-token/)
- [Atlassian Rovo MCP Server — Configure permissions](https://support.atlassian.com/security-and-access-policies/docs/Configure-Atlassian-Rovo-MCP-server-permission/)
- [Atlassian Rovo MCP Server — Control settings (Authentication tab)](https://support.atlassian.com/security-and-access-policies/docs/control-atlassian-rovo-mcp-server-settings/)
- [Atlassian — Manage API tokens (with scopes)](https://support.atlassian.com/atlassian-account/docs/manage-api-tokens-for-your-atlassian-account/)
- [`aws devops-agent register-service` reference](https://docs.aws.amazon.com/cli/v1/reference/devops-agent/register-service.html)
- [`aws devops-agent associate-service` reference](https://docs.aws.amazon.com/cli/latest/reference/devops-agent/associate-service.html)

Content rephrased for compliance with licensing restrictions.
