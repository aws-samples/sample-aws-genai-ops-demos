# Shared bricks for AWS DevOps Agent demos

Everything a demo that showcases an **AWS DevOps Agent capability** consumes from this
repository, in one place. These files are used **as is**: a demo imports them and passes
parameters; it never edits or copies them. If a demo cannot do what it needs without
changing one of them, that is a fix to make here, once, for every demo.

```
shared/devops-agent/
├── README.md              this file
├── agent-tools/           fetch a capability from the public Agent Tools repository
│   ├── deploy-skill.ps1 | .sh     skill or custom agent: sparse-fetch at a ref, build the upload zip
│   └── deploy-mcp.ps1 | .sh       MCP server: sparse-fetch at a ref, deploy from its manifest, print the registration step
├── agent-space/           the Agent Space and the Incident RCA trigger chain
│   ├── cdk/agent-space.ts         DevOpsAgentSpace construct: roles, Agent Space, operator app, AWS association, webhook
│   ├── cdk/alarm-trigger.ts       AlarmTrigger construct: SNS -> Lambda -> HMAC-signed incident on the webhook
│   └── lambda/
│       ├── webhook-provisioner/   custom resource: RegisterService + AssociateService, secret straight to Secrets Manager
│       └── alarm-trigger/         alarm notification -> signed incident event
├── lab/                   the Demo Lab mechanism (each demo writes its own Lab on top of it)
│   ├── cdk/lab-engine.ts          LabEngine construct: one bundle (shared + <demo>/lab), engine function + live alias, API function, one role
│   └── lambda/
│       ├── engine.py              the durable engine: inject -> wait for rollback -> revert, and its control plane
│       ├── devops_agent.py        read-only calls to the DevOps Agent data plane (tasks, usage), SigV4-signed
│       └── requirements.txt       aws-durable-execution-sdk-python, PyYAML
└── examples/
    └── scenarios.yaml             a Lab scenario file to adapt (an example, not a schema)
```

Reference implementation of a demo built on these bricks:
`observability/eks-investigation-devops-agent` (folders `lab/`, `cdk/lib/devops-agent-space-stack.ts`,
`cdk/lib/devops-agent-stack.ts`, `cdk/lib/failure-simulator-api-stack.ts`, `services/merchant-portal/src/lab/`).
Second demo, built from the guides below: `resilience/fsx-windows-sla-review-devops-agent`.

## How a demo based on AWS DevOps Agent uses these files

| Brick | The demo… | When | What the demo writes itself |
|---|---|---|---|
| `agent-space/cdk/agent-space.ts` | imports `DevOpsAgentSpace` into its Agent Space stack, gives it a name, exports the id, webhook URL and secret ARN | synth / deploy, first stack | the stack and its outputs (10 lines) |
| `agent-space/cdk/alarm-trigger.ts` | imports `AlarmTrigger`, hands it the webhook URL and secret from `--context`, its alarm topics and the context lines the incident should carry | synth / deploy, Incident RCA demos only | which alarms, which context lines |
| `lab/cdk/lab-engine.ts` | imports `LabEngine`, points it at its `lab/` folder, adds what the handlers need (IAM, VPC, layers, timeouts); fronts `apiFunction` with its own API Gateway or function URL | synth / deploy | `lab/handlers.py` (inject / revert / probe), `lab/api.py`, `lab/engine_main.py`, `lab/scenarios.yaml`, the Lab UI |
| `lab/lambda/*.py`, `agent-space/lambda/*` | never touches them: the constructs bundle them into the demo's functions | deploy | nothing |
| `agent-tools/deploy-skill.*`, `deploy-mcp.*` | calls them from `deploy-all` with the capability name, ref and Agent Space id | deploy, last step | one line in `deploy-all` |
| `examples/scenarios.yaml` | copies and adapts it | while building | its own `lab/scenarios.yaml` |

Import, call, or copy: only the last row is copied. The rest is used as is; a demo that needs
one of them to behave differently changes it here, once, and every demo gets the fix.

Every construct here lives outside the demo's `node_modules`, so the demo's CDK project resolves
`aws-cdk-lib` and `constructs` for it (one copy, or `instanceof` checks fail): `tsconfig.json`
`paths` (no `rootDir`), `cdk.json` app command with `-r tsconfig-paths/register`, jest
`moduleNameMapper`. Copy the three edits from the EKS demo. The Lambda code is plain Python on
what the runtime ships (boto3, botocore): nothing to bundle, no extra dependency in the demo.

## Building a demo: the steering files

Two steering files turn these bricks into a demo; this README is the mechanism reference they
point at.

| File | What it settles |
|---|---|
| `.kiro/steering/native-agent-demo-guide.md` | How to derive a demo from a capability: read the skill or MCP server, deduce the motion, derive discriminating scenarios, then the smallest environment; the bricks to build; the forks where the builder must be asked; the hard rules (auto-revert, live state, one scenario at a time, Agent Space teardown) and the lessons that cost a deploy each |
| `.kiro/steering/demo-lab-ui-guide.md` | How a Lab reads: shell, labelled facts, vocabulary (agent tasks, spend, failure injection), the engine run as steps, the capability panel, Markdown from the agent |

Kiro loads them when a request matches their description (`inclusion: auto`); any other
assistant, or a person, reads them directly. The contract between the three: the guides say
what to derive and why, this folder holds what is never re-derived, the two demos show the
result.

---

## Agent Tools: skills, custom agents, MCP servers

A capability lives in exactly one place, the public
[Agent Tools repository](https://github.com/aws/tools-for-devops-agent). A demo **references**
it and fetches it at deploy time; nothing is copied into this repository (no vendoring, no
submodules). The demo carries the dependency; the Agent Tools repository only has to keep
published paths stable. Declare the path and the ref the demo was tested against in the
demo's README.

### Skills and custom agents

```powershell
& "..\..\shared\devops-agent\agent-tools\deploy-skill.ps1" -Skill eks-upgrade-readiness -Ref main -AgentSpaceId $agentSpaceId -AgentSpaceRegion $agentRegion
& "..\..\shared\devops-agent\agent-tools\deploy-skill.ps1" -CustomAgent aws-health-report -Ref main
```

```bash
../../shared/devops-agent/agent-tools/deploy-skill.sh --skill eks-upgrade-readiness --ref main --agent-space-id "$AGENT_SPACE_ID" --agent-space-region "$AGENT_REGION"
../../shared/devops-agent/agent-tools/deploy-skill.sh --custom-agent aws-health-report --ref main
```

1. Sparse-fetches `skills/<name>` (or `custom-agents/<name>`) at the ref into a temp directory
   (`git clone --depth 1 --filter=blob:none --sparse` + `git sparse-checkout set`).
2. Skills: builds `<name>.zip` per the Agent Tools upload rules (allowed extensions only;
   `README.md`, `CHANGELOG.md`, `evals/`, `.skilleval.*` excluded).
3. With `-AgentSpaceId <id> [-AgentSpaceRegion <region>] [-AgentTypes GENERIC]` (Bash:
   `--agent-space-id`, `--agent-space-region`, `--agent-types GENERIC` or `CHAT,INCIDENT_RCA`):
   **registers the skill in the Agent Space through the Asset API**, `create-asset` from the zip
   (name and description come from the `SKILL.md` front matter) or `update-asset` when a skill of
   that name exists (`list-assets` returns `items`, not `assets`). No console step: a deploy script
   that has the Agent Space id from its stack outputs leaves the demo ready to use. Needs AWS CLI
   2.34.64+ (the release that added the Asset API); pass that floor to `check-prerequisites`.
   Without it: prints the upload step (DevOps Agent console, Agent Space, Skills; pick **All
   agents** when a custom agent will use the skill). Custom agents are created in the web app;
   the script points at `SYSTEM_PROMPT.md` to paste.

Exports: `$global:AGENT_TOOLS_SKILL_ZIP` / `AGENT_TOOLS_SKILL_ZIP`, `AGENT_TOOLS_SKILL_DIR`,
`AGENT_TOOLS_SKILL_ASSET_ID` (when registered).

### MCP servers

```powershell
& "..\..\shared\devops-agent\agent-tools\deploy-mcp.ps1" -Server aws-vpc-dns-diagnostics-mcp -Ref main `
    -Parameters @{ AllowedAccounts = "111111111111" }
```

```bash
../../shared/devops-agent/agent-tools/deploy-mcp.sh --server aws-vpc-dns-diagnostics-mcp --ref main \
    --param AllowedAccounts=111111111111
```

1. Sparse-fetches `mcp/<name>` at the ref.
2. Reads the server's **`mcp-server.yaml`** manifest and runs the deploy command it declares
   (SAM, CDK or CloudFormation; the runner does not care which).
3. Reads the endpoint URL from the stack output the manifest names, applies the `/mcp` path
   rule, prints the DevOps Agent registration step for the manifest's auth method.
4. `-Destroy` / `--destroy` runs the declared teardown and lists what it does not remove.

Exports: `AGENT_TOOLS_MCP_ENDPOINT`, `AGENT_TOOLS_MCP_AUTH_METHOD`,
`AGENT_TOOLS_MCP_SIGNING_SERVICE`, `AGENT_TOOLS_MCP_STACK`. Needs the toolchain the manifest
declares (`sam`, `npx`/CDK or the AWS CLI); credentials and region as every shared script.

#### The manifest, `mcp-server.yaml`

The runner reads only the server's manifest, the consumption boundary: how to deploy, which
output carries the endpoint, how to authenticate, how to register, how to tear down.

```yaml
schemaVersion: 1
name: <server-dir-name>
kind: self-hosted | aws-hosted-reference     # reference = AWS hosts the MCP; only a connection deploys
pattern: gateway | lambda-http               # self-hosted only
iac: sam | cdk | cfn | terraform
deploy:
  workdir: .
  setup: [["sam", "build"]]                  # optional pre-steps, argv lists
  command: ["sam", "deploy", "...", "AllowedAccounts=${AllowedAccounts}"]   # ${Name} <- -Parameters / --param
  parameters: [{ name: AllowedAccounts, required: true, description: "..." }]
  stacks: [<main-stack>]
  auxiliaryStacks: [{ template: scoped-roles.yaml, scope: per-target-account }]   # not deployed by the runner
endpoint:
  output: McpEndpointUrl
  stack: <main-stack>
  includesMcpPath: true                      # false -> the runner appends /mcp exactly
  transport: streamable-http
  session: stateless | stateful
auth:
  method: sigv4 | oauth-client-credentials | oauth-3lo | api-key | multi-headers
  signingService: lambda | execute-api       # sigv4: differs per HTTP mechanism
  callerActions: [ ... ]                     # IAM actions the DevOps Agent role needs
registration:
  mode: cli | console | cloudformation
  tools: { readOnly: [...], mutating: [...] }
smokeTest: ["uv", "run", "pytest", "tests/", "-q"]
teardown:
  command: ["sam", "delete", "..."]
  residuals: ["..."]
```

Boundary facts only, never secrets (auth fields name *where* credentials come from); argv
lists, not shell strings; unknown fields ignored; breaking changes bump `schemaVersion`.

The manifest lives **in the Agent Tools repository**, at `mcp/<server>/mcp-server.yaml`,
maintained there. This repository keeps no copies: a manifest describing someone else's
server silently lies the moment they change a deploy command. `-Manifest` / `--manifest` is
the escape hatch for a server whose manifest is not upstream yet; the file then lives **in
the demo's own folder**, marked temporary, and is deleted once the server ships its own.

---

## The Agent Space and the trigger chain

Two constructs, used as is. Every demo needs the first; only demos whose motion is Incident
RCA need the second (Chat and Evaluation start no investigation from an alarm).

### `DevOpsAgentSpace` (`agent-space/cdk/agent-space.ts`)

```typescript
import { DevOpsAgentSpace } from '../../../../shared/devops-agent/agent-space/cdk/agent-space';

const space = new DevOpsAgentSpace(this, 'Space', { name: projectName, description: '...' });
// space.agentSpaceId, space.agentSpaceArn, space.webhookUrl, space.webhookSecret (Secret), space.agentSpaceRole, space.operatorRole
```

Provisions, in the stack's region (which must be one where AWS DevOps Agent is available):
the monitoring role and the operator-app role (both trusting `aidevops.amazonaws.com` for
`sts:AssumeRole` **and** `sts:TagSession`, scoped by `aws:SourceAccount` and an `ArnLike`
`aws:SourceArn` on `agentspace/*`), the Agent Space with the operator app, the AWS association
(`accountType: monitor`, all regions of the account) and the generic eventChannel webhook.
Options: `monitorAccountId`, `enableOperatorApp`, `enableWebhook`.

The webhook is a Lambda-backed custom resource because CloudFormation cannot do it:
`RegisterService(eventChannel)` has no resource type, `AWS::DevOpsAgent::Association` exposes
no webhook attributes, and `AssociateService` returns the HMAC secret **exactly once**, in its
create response. The provisioner (`lambda/webhook-provisioner/index.py`) registers the
account-level eventChannel service if none exists, associates it, writes the secret straight
into the Secrets Manager secret the construct created and returns only the URL: the value never
enters CloudFormation state, events or outputs. If the write fails it disassociates at once
(a webhook whose secret is lost is unusable). The control-plane calls are SigV4-signed by hand
(`cp.aidevops.<region>.api.aws`, rest-json, signing name `aidevops`), like the data-plane calls
in `devops_agent.py` (`dp.aidevops...`), so the Lambda has no dependency to bundle. Any property change replaces the webhook
(new URL, new secret); that is the only correct behaviour.

The demo's stack exports what the deploy script needs (`AgentSpaceId`, `WebhookUrl`,
`WebhookSecretArn`): the Agent Space stack deploys first, the script reads the outputs with
`describe-stacks` and passes them to the other stacks as `--context`. The secret value never
crosses the script.

### `AlarmTrigger` (`agent-space/cdk/alarm-trigger.ts`)

```typescript
import { AlarmTrigger } from '../../../../shared/devops-agent/agent-space/cdk/alarm-trigger';

const trigger = new AlarmTrigger(this, 'Trigger', {
  webhookUrl,                                              // from the Agent Space stack outputs (context)
  webhookSecret: secretsmanager.Secret.fromSecretCompleteArn(this, 'Secret', webhookSecretArn),
  webhookSecretRegion,                                     // when the Agent Space is in another region
  topics: [alarmsTopic],                                   // the demo's alarm topics
  context: { 'File system': fileSystemId },                // lines added to every incident
});
// trigger.function (name it in the Lab for a console link), trigger.topic (publish to start an investigation)
```

One Python Lambda subscribed to the demo's alarm topics and to a topic of its own. On each
`ALARM` notification (OK is ignored) it builds a generic incident (alarm name, description,
reason, trigger metric, the `context` lines, the region), signs `"<timestamp>:<json>"` with the
HMAC secret (`x-amzn-event-timestamp`, `x-amzn-event-signature`) and POSTs it to the webhook.
The payload prescribes nothing: the agent decides what to investigate. Deploy it in the alarms'
region; the secret may live elsewhere (the Agent Space region), hence `webhookSecretRegion`.

---

## The Lab mechanism

A Lab is a demo's control room: inject a scenario, see what is injected, roll it back, watch
what the agent did. **Each demo writes its own Lab** (API, UI, scenario file, handlers),
adapted from the reference implementation. What is shared here is the part that is the same
for every demo and expensive to get wrong twice: the durable engine, the agent data-plane
calls, and the construct that packages them with the demo's code.

### The engine (Python)

Every injection is one Lambda durable function execution:

```
inject  ──►  await-rollback (wait_for_callback, timeout = the scenario's auto-revert)  ──►  revert
```

Rollback resolves the callback and the revert runs at once; nobody clicks and the wait times
out into the same revert. No state store: the demo reads the live environment for "is it
injected?", the execution history says where the run is. Closing the browser, a cold start or
an API error cannot leave the environment broken.

Durable function side (the demo's `engine_main.py`):

```python
from engine import make_handler

def _resolve(scenario_id):                 # -> (inject_fn, revert_fn, timeout_seconds); raise ValueError if unknown
    ...
handler = make_handler(_resolve)
```

Control plane side (the demo's `api.py`):

```python
from engine import AlreadyRunning, Engine

eng = Engine(os.environ['ENGINE_FUNCTION_ARN'])   # the live alias ARN
eng.start(scenario_id)          # {'executionName', 'executionArn'}; raises AlreadyRunning (one scenario at a time)
eng.running(scenario_id=None)   # the RUNNING execution, if any
eng.recent()                    # recent executions, all statuses
eng.run_view(execution)         # steps {inject, await-rollback, revert: pending|in-progress|success|error|stopped},
                                #   remainingSeconds, revertReason (manual|auto), callbackId while waiting
eng.request_rollback(execution) # resolves the callback -> revert runs now; False while still injecting
Engine.scenario_of(execution)   # '<scenario-id>' from the execution name '<scenario-id>-<epoch>'
```

Durable API quirks the module encodes so no demo rediscovers them: qualified ARN for invoke,
unique execution names (idempotency keys), unqualified name and at most ONE status for
`ListDurableExecutionsByFunction`, the callback id only in the history's `CallbackStarted`
event.

### The agent data plane (Python)

`devops_agent.get_tasks()` (recent tasks of the Agent Space with execution facts and the
Markdown summary), `devops_agent.get_usage()` (monthly hours per motion) and
`devops_agent.get_skill(name)` (is the skill registered: asset id, status, version, agent
types, from the Asset API). Reads `DEVOPS_AGENT_REGION` and `DEVOPS_AGENT_SPACE_ID`; the
construct grants the five read-only `aidevops:*` actions. The Lab shows the skill's *state*
with `get_skill`, and installation instructions only when it is missing: the deploy registers
it (`deploy-skill -AgentSpaceId`), so a missing skill is the exception, and the prerequisite.

### The construct (CDK, TypeScript)

```typescript
import { LabEngine } from '../../../../shared/devops-agent/lab/cdk/lab-engine';

const lab = new LabEngine(this, 'Lab', {
  labDir: path.join(__dirname, '..', '..', 'lab'),      // the demo's *.py and *.yaml, copied flat next to engine.py
  stageDir: path.join(__dirname, '..', '.lab-stage'),   // gitignore it
  engineHandler: 'engine_main.handler',
  apiHandler: 'api.handler',
  namePrefix: `${projectName}-${environment}`,
  roleName: `${projectName}-${environment}-lab-role`,   // optional: when scripts grant the role by name
  environment: { ... },                                 // what the handlers read
  policyStatements: [ ... ],                            // what the handlers touch
  layers, vpc, vpcSubnets, securityGroups,              // when the handlers need them
});
// lab.role, lab.engineFunction, lab.engineAlias, lab.apiFunction
// The demo adds its API Gateway (or whatever fronts apiFunction) and its routes.
```

At synth time the construct copies `lab/lambda/*.py` and the demo's `lab/*.py` and `*.yaml`
into one staging folder, pip-installs the requirements for the Lambda runtime without Docker
(falls back to the CDK image), and that flat folder becomes the code of both functions. Demo
files must not be named `engine.py` or `devops_agent.py`.

### What a demo writes

In `<demo>/lab/`: `scenarios.yaml` (what the presenter sees and what auto-reverts when; start
from `examples/scenarios.yaml`), `handlers.py` (per scenario: inject, revert, probe the live
environment), `engine_main.py` (three lines, above), `api.py` (its routes, its facts, its
labels), and tests that run the real durable handler with recorder handlers. In the frontend:
its own Lab page, adapted from the EKS demo's `services/merchant-portal/src/lab/`.
