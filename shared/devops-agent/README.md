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
`observability/eks-investigation-devops-agent` (folders `lab/`, `cdk/lib/failure-simulator-api-stack.ts`,
`services/merchant-portal/src/lab/`).

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
& "..\..\shared\devops-agent\agent-tools\deploy-skill.ps1" -Skill eks-upgrade-readiness -Ref main
& "..\..\shared\devops-agent\agent-tools\deploy-skill.ps1" -CustomAgent aws-health-report -Ref main
```

```bash
../../shared/devops-agent/agent-tools/deploy-skill.sh --skill eks-upgrade-readiness --ref main
../../shared/devops-agent/agent-tools/deploy-skill.sh --custom-agent aws-health-report --ref main
```

1. Sparse-fetches `skills/<name>` (or `custom-agents/<name>`) at the ref into a temp directory
   (`git clone --depth 1 --filter=blob:none --sparse` + `git sparse-checkout set`).
2. Skills: builds `<name>.zip` per the Agent Tools upload rules (allowed extensions only;
   `README.md`, `CHANGELOG.md`, `evals/`, `.skilleval.*` excluded).
3. Prints the upload step: DevOps Agent console, Agent Space, Skills. Pick **All agents** when a
   custom agent will use the skill. Custom agents are created in the web app; the script points
   at `SYSTEM_PROMPT.md` to paste.

Exports: `$global:AGENT_TOOLS_SKILL_ZIP` / `AGENT_TOOLS_SKILL_ZIP`, `AGENT_TOOLS_SKILL_DIR`.

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
Markdown summary) and `devops_agent.get_usage()` (monthly hours per motion). Reads
`DEVOPS_AGENT_REGION` and `DEVOPS_AGENT_SPACE_ID`; the construct grants the four read-only
`aidevops:*` actions.

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

Because the construct lives outside the demo's `node_modules`, the demo's CDK project resolves
`aws-cdk-lib` and `constructs` for it (one copy, or `instanceof` checks fail): `tsconfig.json`
`paths` (no `rootDir`), `cdk.json` app command with `-r tsconfig-paths/register`, jest
`moduleNameMapper`. Copy the three edits from the EKS demo.

### What a demo writes

In `<demo>/lab/`: `scenarios.yaml` (what the presenter sees and what auto-reverts when; start
from `examples/scenarios.yaml`), `handlers.py` (per scenario: inject, revert, probe the live
environment), `engine_main.py` (three lines, above), `api.py` (its routes, its facts, its
labels), and tests that run the real durable handler with recorder handlers. In the frontend:
its own Lab page, adapted from the EKS demo's `services/merchant-portal/src/lab/`.
