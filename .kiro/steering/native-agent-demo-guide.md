---
inclusion: auto
name: native-agent-demo-guide
description: Use when building, scaffolding or reviewing a demo that showcases an AWS DevOps Agent capability — a skill, custom agent or MCP server from the Agent Tools repository. Covers choosing the motion (chat, investigation, evaluation), deriving failure scenarios, the demo bricks (Agent Space, Lab, trigger chain, environment), and the decisions the builder must be asked. Not for standalone solutions.
---
# Native Agent Demo Guide

How to build a demo whose subject is an **AWS DevOps Agent capability**: "troubleshoot /
monitor / prevent challenge X using skill Y or MCP Z".

**Precedence:** `contributor-guide.md` wins on repository layout, deploy scripts, region
handling and tracking. This guide adds only what is specific to demonstrating a capability.

## Vocabulary

| Term | Means |
|---|---|
| **Agent Tools repository** | The public repository of capabilities for AWS DevOps Agent: <https://github.com/aws/tools-for-devops-agent>. |
| **This repository** | The GenAI Ops Demo Library, <https://github.com/aws-samples/sample-aws-genai-ops-demos> — where demos and standalone solutions live. |
| **Capability** | One skill, custom agent or MCP server that extends AWS DevOps Agent. Always lives in the Agent Tools repository. |
| **Native-agent demo** | A demo whose subject *is* a capability: it stands up an environment, breaks or mis-configures something, and lets the agent work on it. |
| **Motion** | How the agent gets invoked: a person asking in Chat, an alarm starting an investigation, or an evaluation run. Determined by the capability's declared agent type — see step 2. |
| **Brick** | One reusable part of a demo (Agent Space, Lab, trigger chain, environment, …). Listed under *The bricks*. |
| **Lab** | The demo's own control surface for injecting a scenario, seeing what is currently injected, rolling it back, and watching the agent's results. Shared implementation in `shared/lab/`. |
| **Engine** | The Lab's injection runtime: one Lambda durable function execution per injection (inject, wait for a rollback, revert). Part of the shared Lab. |
| **Handler** | The demo-specific code behind one scenario: `inject()`, `revert()` and `probe()` in the demo's `lab/handlers.py`. The only Lab code a demo writes. |
| **Scenario** | One injectable-and-reversible condition — an active failure, or a pre-existing mis-configuration that breaks nothing yet. Defined in the demo's `lab/scenarios.yaml`. |
| **Agent Space** | The AWS DevOps Agent construct a demo creates and associates with an account; capabilities are registered into it. |

## Hard rules

Non-negotiable. Each is explained in the section it points to.

1. **Never** add a capability to a demo folder, and never commit a copy of one. It lives in
   the Agent Tools repository; the demo fetches it at deploy time. → *Are you in the right
   guide?*
2. Build the trigger chain (alarm → SNS → HMAC Lambda → webhook) **only** when Incident RCA
   is among the motions you demonstrate. Chat and Evaluation need none of it. → *Step 2*
3. Every scenario **must** carry a stated with-capability / without-capability difference.
   No difference, no scenario. → *Step 3*
4. Every injection **must** revert without anyone present: it is one Lambda durable
   function execution whose wait for a rollback times out into the revert. → *The Lab*
5. "Is this injected?" **must** be derived from the live environment, never from stored
   state. The Lab has no state store. → *The Lab*
6. Scenario definitions **must** be declarative data: `lab/scenarios.yaml`, one file
   driving the engine, the API and the Lab UI. → `shared/templates/demo-scenarios.yaml.example`
7. **Never** fabricate user impact. If the failure is legible without an app, ship no app.
   → *Does the demo need an app?*
8. Teardown **must** remove the Agent Space, or the demo fails validation. → *The bricks*
9. **Ask the builder** at every decision point listed under *Ask the builder*. Never infer
   the motion, the audience or the scenario set silently.

## Are you in the right guide?

- Building a **capability** — a new skill, custom agent or MCP server? It cannot be added to
  this repository. Build and publish it in the Agent Tools repository first, then come back
  here and use this guide to build the demo that shows it working.
- Building a **standalone solution** — GenAI for operations with no native agent involved?
  It belongs here, but follow `contributor-guide.md`; this guide does not apply.
- Building a **native-agent demo**? Continue. You will consume the capability with
  `shared/scripts/deploy-skill.*` or `shared/scripts/deploy-mcp.*` — see
  `shared/agent-tools/README.md`.

## Ask the builder — only at a real fork

Do the analysis first, then ask. A question is justified only when the derivation reaches a
**fork**: several defensible options, and the choice is the builder's to make. If the
capability, its metadata or the derivation already settles the point, do not ask — state
what you concluded and move on. The builder can always object.

The one question always asked up front: **which capability** (and, if they care, which tag
or commit). Everything else is conditional:

| Fork | Ask… | Do **not** ask when… |
|---|---|---|
| The capability declares **several** agent types | which motion(s) to demonstrate — after showing what each would require | it declares one; that *is* the motion |
| `agent-types` is **absent** and had to be inferred | to confirm the inferred motion | it was read from metadata |
| Derivation yields **more candidate scenarios** than a demo should carry | which to build — after presenting the ranked list | the candidates fit comfortably; propose the set and proceed |
| The **app tier** is not obvious from the capability | who the audience is — because that decides it | the failure is plainly infra-facing (a network or database capability): propose "no app" and proceed |
| The derived environment is **expensive** or slow to deploy | for a cost or duration ceiling | it is small; state the estimate and proceed |

Present derived conclusions as statements with a one-line reason, not as questions. Batch
whatever forks exist into a single message rather than a series. Record the answers in the
demo's README so the next presenter knows why the demo is shaped the way it is.

**The analysis ends with a file, not prose.** Before asking, draft the demo's
`lab/scenarios.yaml` from `shared/templates/demo-scenarios.yaml.example` (step 3 writes the
scenarios, step 4 completes them). Questions then point at concrete entries ("keep
`pdb-blocks-eviction`, drop `karpenter-drift`?"), and the builder's answers edit the file
that the Lab will run. Three phases, one interruption: **analyse → ask → build**.

## Finding a capability to showcase

In the Agent Tools repository:

| Where | What you get |
|---|---|
| `llms.txt` at the repository root | Every skill, custom agent and MCP server with a one-line description — the fastest way to enumerate what exists |
| `skills/<name>/SKILL.md` | The capability itself: front matter (agent types, version) and its instructions |
| `skills/<name>/README.md` | Purpose, prerequisites, IAM permissions, agent types, sample prompts |
| `mcp/<name>/README.md` | What the server does, its tools, deployment and registration steps |
| `custom-agents/<name>/` | `SYSTEM_PROMPT.md` plus the skills and tools it composes |

## Work backwards from the capability

Do **not** start from the environment. Starting with "let's build an EKS cluster" produces a
demo in search of a story. The capability is the subject; the environment is a consequence.

```
capability  →  motion  →  scenarios  →  environment  →  bricks
 (given)     (deduced)   (deduced)     (falls out)    (determined)
```

### Step 1 — Read the capability

**For a skill**, start with the `SKILL.md` front matter — the
`metadata.aws-devops-agent-skills.*` fields, when present, answer most questions directly:

| Signal | Tells you |
|---|---|
| `aws-devops-agent-skills.agent-types` | The **motion(s)** the skill supports. Several may be declared — that is a fork, the builder chooses (see *Ask the builder*) |
| `aws-devops-agent-skills.aws-services` | The **environment** you will have to stand up — a head start on step 4 |
| The `description` field | The activation phrases — these become the demo's literal prompts |
| `references/` | The **scenario candidates** — a structured check registry (YAML), or diagnostic queries and checklists in markdown |
| A `compatibility:` key, or an "MCP Server Integration" section in the body | Whether the skill **requires an MCP server** — declared in prose as often as in front matter, so read for it |

**The `aws-devops-agent-skills.*` fields are optional** (roughly one skill in eight omits
them). When `agent-types` is missing, fall back in order: the skill's `README.md` ("Agent
Types" section), then inference from the description and body — "health check", "dig
deeper" or an interactive mode point to Chat; "investigation", "root cause", "incident"
point to Incident RCA. An inferred motion is a fork: confirm it with the builder before building.

**For an MCP server**, the question inverts. Read the tool list and the read-only/mutating
classification, then ask: *what can the agent now see that it could not before?* The demo
must create a situation where that data is decisive. A VPC DNS prober only shines when the
answer is invisible from control-plane APIs alone.

### Step 2 — Derive the motion, because it decides the plumbing

The agent type dictates how the agent is invoked, and therefore which bricks you must build.

| Agent type | How it is triggered | Trigger bricks needed | The environment must be… |
|---|---|---|---|
| **Chat tasks** | A person asks in Chat | **None** — no alarm, no webhook | *inspectable* (it need not be broken) |
| **Incident RCA / triage** | CloudWatch alarm → SNS → HMAC Lambda → webhook | The full trigger chain | *breakable*, with something that fires |
| **Evaluation** | A manual run or the weekly schedule; results land on the Improvements page | None of its own, **but** it consumes prior investigations, so the RCA path must have run first | breakable, and already investigated |
| **Incident mitigation** | The agent acts, not just reports | RCA chain plus explicit approval design | breakable and safely repairable |

A Chat-only skill in a demo built around alarms never loads. An Evaluation demo with no prior
investigations has nothing to evaluate.

**Several agent types declared** (e.g. `"Chat tasks, Evaluation, Incident RCA"`): the demo
need not exercise all of them — that is the builder's fork. Chaining motions is legitimate and often
the strongest demo (inject → RCA investigates → ask in Chat → run an Evaluation), but each
motion is a brick to build and a step the presenter must run.

### Step 3 — Derive scenarios, and make them discriminating

A scenario is only worth building if the capability **changes the outcome**. The Agent Tools
evaluation framework already uses this test: every functional result is a `with_skill` /
`without_skill` pair from the same scenario.

> **The rule:** for every proposed scenario, state what the agent concludes *without* the
> capability. Same answer → reject the scenario; it demonstrates nothing.

Where the capability ships a structured check registry, derivation is close to mechanical:

1. Filter checks by applicability against the environment you intend to build (no Karpenter
   in the demo → every Karpenter check is out of scope).
2. For each remaining check, ask: can I make this fail with a **cheap, reversible** injection?
3. Rank by injection cost × impact. If more candidates survive than a demo should carry,
   present the ranked list to the builder (a fork); otherwise propose the set and proceed.

Two honest constraints:

- **Not every check is demonstrable.** Anything depending on real-world state — AWS Health
  events, Support cases, billing lifecycles, multi-month clocks — cannot be faked. Say so;
  never invent a fake.
- **Pre-existing conditions are as valid as failures.** A prevention capability finds
  problems in a *healthy* environment: a Pod Disruption Budget that blocks eviction breaks
  nothing until drain day. That demo's story is "it stopped you walking into an outage", and
  it needs no alarms at all.

Write the survivors into `lab/scenarios.yaml` now: `demonstrates.withCapability` and
`withoutCapability` are the with/without test made permanent, `incidentChain`,
`customerImpact` and `demoFlow` are what the presenter will read from the card. Leave
`handler` and `alarm` for step 4.

### Step 4 — Derive the environment

The smallest environment that can host the chosen scenarios and produce the telemetry the
agent reads. Nothing more. The environment is the most expensive brick to build, deploy and
maintain, and it is why a demo costs money per day. If the estimate is high or the deploy
slow, ask the builder for a ceiling (a fork); otherwise state the estimate and proceed.

Complete `lab/scenarios.yaml` here: each scenario's `handler` (the function trio you will
write in `lab/handlers.py`), `alarm.envVar` for alarm-driven scenarios, `autoRevertSeconds`.
The analysis is done when this file would pass the Lab's validation.

## The bricks

| Brick | Needed when | Notes |
|---|---|---|
| Agent Space, IAM roles, account association | Always | |
| Capability acquisition + registration | Always | `deploy-skill.*` / `deploy-mcp.*` |
| Mock environment | Always | Smallest that hosts the scenarios |
| **Lab** — inject / rollback / status | Always | Not optional — see below. Shared: `shared/lab/` |
| Engine — one durable execution per injection | Whenever anything is injectable | Part of the shared Lab; no state store |
| Trigger chain — alarm → SNS → HMAC Lambda → webhook | Incident RCA / triage only | Skip entirely for Chat |
| Observation surface — agent tasks, skill, spend | Always | Part of the shared Lab; otherwise the presenter leaves the demo to see results |
| **App** showing user impact | Only when the failure is illegible without one | See the tiers below |
| Teardown | Always | Must remove the Agent Space; a demo that cannot be destroyed fails validation |

### The Lab

A demo delivered in a cadence call cannot require SSH with a key file, a local CLI, or
copy-pasted shell commands. Injection is one click or one command, current state is visible,
rollback is immediate. A demo whose failures can only be injected by hand is not deliverable
by a presenter who did not build it.

The Lab is shared (`shared/lab/`, extracted from the EKS demo). A demo writes exactly two
things for it:

- `lab/scenarios.yaml` — the definitions (template: `shared/templates/demo-scenarios.yaml.example`).
- `lab/handlers.py` — a `HANDLERS` registry mapping each scenario's `handler` name to three
  functions: `inject()` breaks it, `revert()` puts it back, `probe()` reads the **live**
  environment and returns `{"injected": bool, "facts": [...]}`. Facts are labelled values
  (`{"label", "value", "status"?, "link"?}`) the UI renders without knowing what they are:
  pods, replicas, a BGP session, a file system's throughput mode — whatever proves the state.

Everything else (engine, API, UI, bundling, IAM for the durable execution) comes from the
shared Lab; the demo's CDK only adds what its handlers need (network access, a kubectl
layer, permissions on the resources they touch).

Why hard rules 4–6 exist, and how the engine honours them:

- **Auto-revert** (rule 4) — demos get abandoned mid-run. Every injection is one Lambda
  durable function execution: `inject` → `await-rollback` → `revert`. Rollback resolves the
  execution's callback and the revert runs at once; nobody clicks and the wait times out
  after `autoRevertSeconds` into the same revert. Two roads, one revert step, no timer to
  lose, no browser to keep open.
- **Live state over stored state** (rule 5) — a demo that trusts its own database lies the
  moment someone fixes something by hand. `probe()` reads the running environment; the
  execution history says where the run is. There is nothing else to get out of sync.
- **Scenarios as data** (rule 6) — hardcoding them in the UI means three places to keep in
  sync. One file, three consumers.
- **One scenario at a time** — concurrent injections make the agent's findings ambiguous.
  The Lab refuses a second inject (HTTP 409) while an execution is running.

Learned the hard way, so you do not have to: quote YAML list lines containing `: ` (they
parse as mappings and crash the card); never reuse a durable execution name (names are
idempotency keys; the Lab uses `<scenario-id>-<epoch>`); the Lab's own validator
(`python shared/lab/lambda/validate.py <demo>/lab`) checks that every `handler` exists,
every walkthrough line is a string and every scenario states its with/without difference,
so run it before the first deploy. How to wire the construct and the page into a demo:
`shared/lab/README.md`.

### Does the demo need an app?

| Tier | What it is | Choose it when |
|---|---|---|
| None | Impact read from the Lab and the agent's findings | The failure is self-evident to the audience (a dropped BGP session, to a network engineer) |
| Synthetic traffic | A canary, ping or load generator — no UI | You need telemetry, not a human-visible consequence |
| Full app | A real user journey that visibly breaks | The audience is business-facing, or the failure needs a face ("checkout fails" rather than "pods restarting") |

Prefer the cheapest tier that makes the failure legible; when the tier is not obvious from
the capability, the audience decides it, and that is the builder's fork. An invented
user journey that does not exist in the scenario reads as set dressing and costs credibility
with the audience most able to notice.

## Shared infrastructure, when it exists

Where a shared construct covers a brick, use it rather than rebuilding. Today: the Lab
(`shared/lab/`: engine, API, UI). The Agent Space, webhook and alarm bridge are still
per demo (the EKS demo is the reference); they carry undocumented traps (role naming
collisions, the deploy-region versus Agent-Space-region split, cross-region event delivery)
that are expensive to rediscover, so copy from the reference rather than from memory.

- **Two strikes before generalising.** The first demo needing something unusual keeps it
  local. When a *second* demo needs the same thing, promote it to the shared brick.
- **Under-parameterise first.** Adding an optional input later is cheap and backward
  compatible; removing or changing one after demos depend on it is a migration.

## Before you call it done

- The capability is referenced at a stated ref, not copied.
- The builder's answers at every fork (capability, motion, scenario set, app tier, cost
  ceiling) are recorded in the README.
- Each scenario has a recorded with-capability / without-capability difference
  (`demonstrates` in `lab/scenarios.yaml`).
- `lab/scenarios.yaml` passes the Lab's validation; every `handler` has its trio.
- Every injection reverts, automatically as well as on demand (both roads exercised once
  against the deployed demo).
- The motion matches the capability's declared agent type.
- Teardown removes the Agent Space and everything else.
- A presenter who did not build it can deliver it from the README.
