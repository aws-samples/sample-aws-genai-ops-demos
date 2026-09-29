# Demo Lab: the shared mechanism

A Lab is a demo's control room: inject a scenario, see what is injected, roll it back,
watch what the agent did. **Each demo writes its own Lab** (API, UI, scenario file,
handlers), derived from the steering and adapted from the reference implementation in
`observability/eks-investigation-devops-agent/lab` and `.../services/merchant-portal/src/lab`.

What is shared here is only the part that is the same for every demo and expensive to get
wrong twice:

```
shared/lab/
├── lambda/engine.py         the durable engine: inject -> wait for rollback -> revert, and its control plane
├── lambda/devops_agent.py   read-only calls to the DevOps Agent data plane (tasks, usage), SigV4-signed
├── lambda/requirements.txt  aws-durable-execution-sdk-python, PyYAML
└── cdk/lab-engine.ts        LabEngine construct: one bundle (shared + <demo>/lab), engine function + live alias, API function, one role
```

No template, no schema, no validator: the demo's `scenarios.yaml` is the demo's own
contract with its own code (an example to adapt: `shared/templates/demo-scenarios.yaml.example`).

## The engine (Python)

Every injection is one Lambda durable function execution:

```
inject  ──►  await-rollback (wait_for_callback, timeout = the scenario's auto-revert)  ──►  revert
```

Rollback resolves the callback and the revert runs at once; nobody clicks and the wait
times out into the same revert. No state store: the demo reads the live environment for
"is it injected?", the execution history says where the run is. Closing the browser, a
cold start or an API error cannot leave the environment broken.

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

Durable API quirks the module encodes so no demo rediscovers them: qualified ARN for
invoke, unique execution names (idempotency keys), unqualified name and at most ONE status
for `ListDurableExecutionsByFunction`, the callback id only in the history's
`CallbackStarted` event.

## The construct (CDK, TypeScript)

```typescript
import { LabEngine } from '../../../../shared/lab/cdk/lab-engine';

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

The bundle is built without Docker (pip resolves manylinux wheels for the Lambda
runtime; falls back to the CDK image). Demo files must not be named `engine.py` or
`devops_agent.py`.

Because the construct lives outside the demo's `node_modules`, the demo's CDK project
resolves `aws-cdk-lib` and `constructs` for it (one copy, or `instanceof` checks fail):
`tsconfig.json` `paths` (no `rootDir`), `cdk.json` app command with `-r
tsconfig-paths/register`, jest `moduleNameMapper`. Copy the three edits from the EKS demo.

## The agent data plane (Python)

`devops_agent.get_tasks()` (recent tasks of the Agent Space with execution facts and the
Markdown summary) and `devops_agent.get_usage()` (monthly hours per motion). Reads
`DEVOPS_AGENT_REGION` and `DEVOPS_AGENT_SPACE_ID`; the construct grants the four
read-only `aidevops:*` actions.

## What a demo writes

In `<demo>/lab/`: `scenarios.yaml` (what the presenter sees and what auto-reverts when),
`handlers.py` (per scenario: inject, revert, probe the live environment), `engine_main.py`
(three lines, above), `api.py` (its routes, its facts, its labels), and tests that run the
real durable handler with recorder handlers. In the frontend: its own Lab page, adapted
from the EKS demo's (`services/merchant-portal/src/lab/`).
