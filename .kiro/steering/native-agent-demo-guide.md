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
| **Lab** | The demo's own control surface for injecting a scenario, seeing what is currently injected, rolling it back, and watching the agent's results. Each demo writes its own; the EKS demo is the reference. |
| **Engine** | The Lab's injection runtime: one Lambda durable function execution per injection (inject, wait for a rollback, revert). Shared mechanism in `shared/devops-agent/lab/`, never re-derived. |
| **Handler** | The demo-specific code behind one scenario: `inject()`, `revert()` and a probe of the live environment, in the demo's `lab/`. |
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
   driving the engine, the API and the Lab UI. → `shared/devops-agent/examples/scenarios.yaml`
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
  `shared/devops-agent/agent-tools/deploy-skill.*` or `deploy-mcp.*` — see
  `shared/devops-agent/README.md`.

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
`lab/scenarios.yaml` from `shared/devops-agent/examples/scenarios.yaml` (step 3 writes the
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

Everything in that file is read by a presenter from a card, so write it in their words,
not the capability's: `demonstrates.check` names the check in plain language ("Alarm
coverage: does anything watch this file system?"), never the capability's internal
numbering or section titles ("Dimension 7", "check C-12"). A Chat-driven scenario carries
its own `prompt`, the exact sentence to paste, so the card can offer it where the demo flow
says "ask"; an alarm-driven scenario has none (the alarm asks).

**Then make the with-capability claim true.** `withCapability` is a prediction until the
deployed demo has produced it: inject, ask (or let the alarm fire), and read the agent's
actual output. Only what was observed stays in the file; if the agent did not say it, fix
the environment, the scenario or the claim before calling the scenario done. Mechanics
working (inject, revert, alarm, task created) is not the test; the agent's sentence is. The
FSx demo shipped its two Chat scenarios on mechanics alone; the review that confirmed them
("No native AWS/FSx alarms… the custom alarm doesn't count as coverage") was run by the
builder afterwards, and the demo's yaml now quotes it. Run it yourself, before, and paste the
sentence into `withCapability`: the card then shows what the agent actually says, and the
presenter is never surprised.

While there, check that the environment does not contradict the scenario: any resource that
resembles the thing the scenario removes (an alarm on the same resource, a backup from
another tool) must be visibly different, or the agent will be right to count it.

### Step 4 — Derive the environment

The smallest environment that can host the chosen scenarios and produce the telemetry the
agent reads. Nothing more. The environment is the most expensive brick to build, deploy and
maintain, and it is why a demo costs money per day. If the estimate is high or the deploy
slow, ask the builder for a ceiling (a fork); otherwise state the estimate and proceed.

Complete `lab/scenarios.yaml` here: each scenario's `handler` (the inject / revert / probe
trio you will write in `lab/handlers.py`), the alarm for alarm-driven scenarios, the
auto-revert timeout. The analysis is done when every scenario in the file has its
handler named and its with/without statement written.

**Readiness the deploy script must wait for.** CloudFormation orders resources, not
readiness: an EC2 domain controller exists long before it is a domain, and FSx joins the
domain at creation (and fails after 30 minutes). When a resource needs another one to be
*ready*, split them into two stacks and make the deploy script wait on a signal between them
(the FSx demo's controller writes an SSM parameter at the end of its user data; the script
polls it, with a deadline and a pointer to the log). State the wait and the total deploy
time in the script's banner and in the README. Re-running the script must resume, not redo.

## The bricks

| Brick | Needed when | Notes |
|---|---|---|
| Agent Space, IAM roles, account association | Always | |
| Capability acquisition + registration | Always | `deploy-skill.*` / `deploy-mcp.*`. Give `deploy-skill` the Agent Space id from the stack outputs: it registers the skill through the Asset API and the deploy ends with nothing to upload |
| Mock environment | Always | Smallest that hosts the scenarios |
| **Lab** — inject / rollback / status | Always | Not optional — see below. The demo's own; adapt the reference |
| Engine — one durable execution per injection | Whenever anything is injectable | Shared mechanism (`shared/devops-agent/lab/`); no state store |
| Trigger chain — alarm → SNS → HMAC Lambda → webhook | Incident RCA / triage only | Skip entirely for Chat |
| Observation surface — agent tasks, capability state, spend | Always | In the Lab (data from the shared `devops_agent.py`: tasks, `get_skill`, usage); otherwise the presenter leaves the demo to see results. The capability panel shows state, and instructions only when the capability is missing |
| **App** showing user impact | Only when the failure is illegible without one | See the tiers below |
| Teardown | Always | Must remove the Agent Space; a demo that cannot be destroyed fails validation |

### The Lab

A demo delivered in a cadence call cannot require SSH with a key file, a local CLI, or
copy-pasted shell commands. Injection is one click or one command, current state is visible,
rollback is immediate. A demo whose failures can only be injected by hand is not deliverable
by a presenter who did not build it.

**Each demo writes its own Lab.** Its cards, its facts, its API routes, its scenario file:
a file system's Lab shows a lifecycle and a throughput mode, a cluster's Lab shows pods
and replicas. Derive it from these rules and from `demo-lab-ui-guide.md`, and adapt the
reference implementation (`observability/eks-investigation-devops-agent`, folders `lab/`
and `services/merchant-portal/src/lab/`) rather than copying it or turning it into a
library. Two examples of the scenario file: the EKS demo's `lab/scenarios.yaml` and
`shared/devops-agent/examples/scenarios.yaml`; they are examples, not a schema.

**Only the mechanism is shared** (`shared/devops-agent/lab/`, see `shared/devops-agent/README.md`): the durable engine
(`engine.py`: the inject → wait → revert execution and its control plane), the DevOps
Agent data-plane calls (`devops_agent.py`: tasks, usage), and the `LabEngine` CDK
construct (one bundle from `shared/devops-agent/lab/lambda` plus the demo's `lab/`, the durable
function with its `live` alias, the API function, one role). A demo wires them with three
lines (`engine_main.py`), fronts the API function with its own API Gateway, and adds what
its handlers need (network access, a kubectl layer, permissions). Never re-derive the
engine: it encodes durable-API quirks that have no error message.

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
idempotency keys; the engine uses `<scenario-id>-<epoch>`); write a test that loads the
demo's `scenarios.yaml` and checks every `handler` exists, every walkthrough line is a
string and every scenario states its with/without difference (the EKS demo's
`lab/tests/test_lab.py` is the model, together with the engine tests that run the real
durable handler on both roads with recorder handlers).

- **Slow-settling resources.** When the environment applies a change asynchronously (FSx
  takes minutes to go `UPDATING` → `AVAILABLE` and refuses a second update meanwhile),
  `revert()` waits for the resource to settle before and after its own call, and the engine
  function's timeout is sized for it (`engineTimeout` on `LabEngine`). Otherwise the run
  ends while the environment is still broken and the card flashes "Injected outside the
  Lab". `inject()` may wait a few seconds for the change to become visible, so the probe
  agrees with the card.
- **What the Lab recreates must match what CDK created.** A scenario that deletes something
  (an alarm) recreates it on revert from the Lab's own code: keep the definition identical
  (name, metric, threshold, action), pass the numbers as environment variables from the
  stack, and test the recreated resource against the review's own criterion (the FSx demo
  asserts the recreated alarm is `AWS/FSx`-scoped to the file system, which is what the
  skill counts).
- **No native signal, no alarm.** When the service publishes no metric for the state the
  scenario produces (FSx has no lifecycle metric), a one-minute canary Lambda reads the API
  and publishes one; the alarm sits on the canary metric. Say so in the alarm description
  so the agent, which reads it, is not misled.

### A Lab without an application

When the demo has no app, the Lab is the whole site and still needs authentication (it
mutates the environment). The FSx demo's pattern: the site on S3 behind CloudFront, the Lab
API as a **Lambda function URL with IAM auth that only CloudFront may call** (origin access
control), and one **CloudFront Function doing HTTP Basic authentication** on every request,
site and `/admin/*` alike, then dropping the `Authorization` header so it does not collide
with the SigV4 signature CloudFront adds. Facts that cost a deploy each: the browser's
POST/DELETE to an OAC-protected function URL must carry `x-amz-content-sha256` (the hash of
the empty body is a constant); function URL events are payload format 2.0
(`requestContext.http.method`, `rawPath`); the credentials are generated by the deploy
script, passed as `--context`, printed once, and an empty password must deny everything
rather than fail synth (teardown synthesizes without context). No API Gateway, no user pool.

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

The line between what is shared and what each demo derives is **mechanism versus
expression**. Expression is everything that says what this demo is (environment,
scenarios, Lab UI and API, wording): derive it. Mechanism is what does not change between
demos and was expensive to get right once: reuse it as code, never re-derive it, not by a
human and not by an agent.

Mechanism today, all under `shared/devops-agent/` (its README is the reference): the Lab
engine (`lab/`), the capability fetch (`agent-tools/deploy-skill.*`, `deploy-mcp.*`), the
`DevOpsAgentSpace` construct (`agent-space/cdk/agent-space.ts`) and the `AlarmTrigger`
construct (`agent-space/cdk/alarm-trigger.ts`, the alarm → SNS → signed webhook chain).
The Agent Space code encodes facts no documentation gives you: the webhook HMAC secret is
returned exactly once by `AssociateService` and no API returns it again, hence a custom
resource writing it straight to Secrets Manager; `RegisterService(eventChannel)` has no
CloudFormation type; the roles need `sts:TagSession` and an `aws:SourceArn` condition on
`agentspace/*`; the Agent Space must depend on both roles because the service validates
assumability at creation and IAM is eventually consistent. Use both as is: the Agent Space
takes a name and gives back the space id, the webhook URL and the secret; the trigger takes
the webhook URL, the secret, the alarm topics and the context lines the incident should
carry (which cluster, which file system). The deploy script deploys the Agent Space stack
first and passes its outputs to the others as `--context`; the secret value never crosses.

- **Two strikes before generalising.** The first demo needing something unusual keeps it
  local. When a *second* demo needs the same thing, promote it to the shared brick.
- **Under-parameterise first.** Adding an optional input later is cheap and backward
  compatible; removing or changing one after demos depend on it is a migration.

## Before you call it done

- The capability is referenced at a stated ref, not copied, and the deploy script registers
  it (a skill through the Asset API, an MCP server through its registration step): the
  presenter opens the Lab and the agent already knows the domain.
- The builder's answers at every fork (capability, motion, scenario set, app tier, cost
  ceiling) are recorded in the README.
- Each scenario has a recorded with-capability / without-capability difference
  (`demonstrates` in `lab/scenarios.yaml`), in the presenter's words, and its
  `withCapability` sentence was **observed in the agent's output on the deployed demo**
  (a Chat review or an investigation per scenario), not inferred from the capability's text.
- The Lab shows the capability as registered (state from the Agent Space), and a fresh
  deploy leaves nothing to upload or paste.
- The demo's tests load `lab/scenarios.yaml` and check every `handler` has its trio, every
  walkthrough line is a string, every scenario states its with/without difference.
- Every injection reverts, automatically as well as on demand (both roads exercised once
  against the deployed demo).
- The motion matches the capability's declared agent type.
- Teardown removes the Agent Space and everything else.
- A presenter who did not build it can deliver it from the README.
