---
inclusion: auto
name: demo-lab-ui-guide
description: Use when building or reviewing the Lab UI of a native-agent demo, or any Cloudscape page in this repository. Captures the layout, vocabulary and feedback rules learned on the EKS demo Lab (full-width shell, labelled facts, engine run as Steps, sticky scope banner, Markdown from the agent, spend not quotas) so every demo Lab reads the same way without sharing UI code.
---
# Demo Lab UI Guide (Cloudscape)

A Lab is the presenter's control room: inject a scenario, see what is injected right
now, roll it back, watch what the agent did. Each demo writes its own Lab UI, derived
from these rules and adapted to what the demo is about. The reference implementation is
`observability/eks-investigation-devops-agent/services/merchant-portal/src/lab/`. Adapt
it; do not copy it blindly and do not turn it into a library.

Cloudscape reference for agents: <https://cloudscape.design/llms.txt> (fetch the `.md`
pages named there when a component's props or slots are in doubt).

## Shell

- The Lab is its own page, not a panel inside the demo application. Route it separately
  (`/lab`), behind the same authentication, outside the application's layout.
- `AppLayout` with `navigationHide`, `toolsHide`, `maxContentWidth={Number.MAX_VALUE}`,
  `stickyNotifications`. Whole width: cards carry three or four columns of facts and
  get scrambled at 1200 px.
- No `TopNavigation`. The page `Header` (variant h1) carries the title, a one-line
  tagline, and the actions: the agent's console link, Refresh. No "back to the app"
  button; the browser does that.
- Title: **AWS DevOps Agent Demo Lab**. Say "AWS DevOps Agent" in full the first time
  on a page.
- Import `@cloudscape-design/global-styles/index.css` in the Lab entry only, so the
  demo application's own look is untouched.

## Every word has a label

A card must never show a bare adjective. "Healthy" is a property of the environment,
not of a scenario; "Critical" alone means nothing. Use `KeyValuePairs` for every claim:

- Card title: `Scenario: <name>` (the noun tells the reader what the card is).
- Header facts: **Failure injection** (Not injected / Injecting / Injected / Reverting /
  Injected outside the Lab), **Category**, **Agent trigger** (CloudWatch alarm,
  automatic investigation / Chat prompt, manual). No severity unless the demo acts on it.
- Under the description: **DevOps Agent capability shown** and **What the agent should
  conclude**, straight from the scenario's with/without statement. Both in the
  presenter's words: "Alarm coverage: does anything watch this file system?", never the
  capability's internal numbering or jargon ("Dimension 7" means nothing on a card).
- Chat-driven scenarios carry their own prompt on the card, as `CopyToClipboard`
  ("Ask in Chat"), right where the demo flow says to ask; the prompt is data
  (`prompt` in `lab/scenarios.yaml`), not something the presenter hunts for in another
  panel. Alarm-driven scenarios show the trigger chain instead (the "Webhook trigger
  Lambda" link belongs to `triggersAlarm` cards only; on a Chat card it is noise).
- Live state as facts with the demo's own labels (Deployment, Pods, DB_PASSWORD,
  Lifecycle, Alarm, …), each with a `StatusIndicator`, an optional detail line and a
  Console link as the pair's `info`. Ratios (replicas ready, storage free) as
  `ProgressBar variant="key-value"` inside the pair.
- "Injected" is derived from the live environment and the engine run, never from a
  stored flag; when the environment is broken with no run owning it, say so:
  "Injected outside the Lab".

## Vocabulary

Use the agent's own words, so the Lab and the console read the same:

- **Agent tasks**, not logs or investigations: the Agent Space backlog holds
  investigations, evaluations and system learning; the type column tells them apart.
  **Chats are not in it**: a chat is a per-user execution (`ListChats` returns the
  caller's own, and the Lab's role is not the presenter), so the Lab cannot list them.
  Say so in the table's description, link to the operator app's chats, and make the
  Chat cards' demo flow read "read the report in the chat", never "it appears under
  Agent tasks".
- **Agent spend**, not usage or quotas: `Estimated spend this month: $X (N agent-seconds
  at $0.0083/s, list price)`, one line total then one per motion. The usage API
  reports `limit: -1` when no quota is set; never render quota bars.
- **Failure injection**, **Rollback**, **auto-revert** (spell out what it means the
  first time: "reverts when you click Rollback, or on its own after N minutes, even
  with this page closed"; "if you walk away" reads as leaving the page).
- Name UI concepts after the data: "tag" not "owner", "task" not "log".

## The engine run

- Show the durable execution as `Steps`: Inject the failure / Wait for a rollback /
  Revert, statuses from the execution history (pending, in-progress, success, error,
  stopped when the run ended before the step). The wait step's detail carries the
  countdown while waiting, "Rollback requested" or "Timed out: reverted automatically"
  after.
- While a scenario is injected, a sticky `Flashbar` banner is the page's scope
  indicator: which scenario, the countdown, a **Rollback now** action. Not stacked (at
  most three messages), so the outcome of an action shows right under the banner
  whose button was clicked.
- Feedback is immediate: the moment the engine accepts a rollback, banner and card show
  **Reverting**; do not wait for the next poll. Clear that optimistic state when the
  history shows the wait step resolved.
- Poll every 10 s idle, 3 s while a run is active. Tick the countdown locally between
  polls. One failed poll is not an outage: CloudFront drops the odd request to the origin
  (a single 504 with a healthy Lambda behind it), so say "Lab API unreachable" only after
  three consecutive failures, and clear it on the next success.
- While a run is active, the other scenarios' Inject buttons are disabled: one scenario
  at a time, and the banner says why.

## The capability panel

The deploy registers the capability (`deploy-skill -AgentSpaceId`, `deploy-mcp`), so the
panel shows its **state**, read live from the Agent Space, not instructions to install
what is already there:

- Registered (the normal case): a compact container at the bottom of the page. Name as
  a link to the skill in the operator app, `StatusIndicator` Active / Inactive, version,
  agent types, source at the stated ref, what it showcases. Nothing to copy, nothing to
  do. The state comes from the shared `devops_agent.get_skill(name)` (Asset API) served
  by the Lab API; never from a flag.
- Missing: the same container becomes a `warning` and moves to the **top of the page**,
  above the scenarios, because it is now the prerequisite to every card. Only then show
  the fix: re-run the deploy script, or the `deploy-skill` command with the Agent Space
  id (PowerShell and Bash, copy buttons), and as a last resort the console upload step.
- Inline skill (defined by the demo, created by copy-paste in the console): the copy
  buttons for name, description and instructions are the registration, so they stay;
  still, when `get_skill` finds it, say so and demote the copy fields to an
  `ExpandableSection`.
- MCP server: registered state (tools, auth method) when the demo can read it; otherwise
  the `deploy-mcp` command and the registration step.
- No prompt in this panel: prompts live on the scenario cards (above).

## Text from the agent

Task summaries and reports arrive as Markdown written as standalone documents (they
open with an H1). Render with `react-markdown` + `remark-gfm` inside `TextContent`,
demote headings two levels (h1 → h3), map links to Cloudscape `Link external`. Raw
Markdown in a card reads as a headline soup.

## Small things that were wrong once

- YAML list items containing `: ` parse as mappings and crash the card (React error
  #31). Quote them, and test that every walkthrough line is a string.
- Console URLs: derive the host from the partition (`console.aws.amazon.com`,
  `console.amazonaws.cn`, `console.amazonaws-us-gov.com`), never hardcode a region.
- Operator app URLs (`https://<agentSpaceId>.aidevops.global.app.aws`): the skills list is
  `/knowledge?tab=skills`, one skill is `/knowledge/skills/<assetId>` (the asset id from
  `get_skill`), an investigation is `/<agentSpaceId>/investigation/<taskId>`. `/skills`
  does not exist; a guessed path is a dead button in front of the audience.
- Lazy-load the Lab route so the demo application does not pay for Cloudscape.
- A `Box` has no `fontStyle`; italics go in an `<i>`.
