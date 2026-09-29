# The Lab: shared control room for native-agent demos

The Lab is the presenter's control surface of a demo that showcases an AWS DevOps Agent
capability: inject a scenario, see what is injected right now, roll it back, watch the
agent's tasks, spend and skill. It is one brick, shared by every such demo; the EKS demo
(`observability/eks-investigation-devops-agent`) is the reference it was extracted from.

Why it is shaped the way it is: `.kiro/steering/native-agent-demo-guide.md`, section *The Lab*.

## What a demo writes

Two files, both in the demo's `lab/` folder:

| File | Contents |
|---|---|
| `lab/scenarios.yaml` | The scenario definitions, the skill and the presenter notes. Shape: `shared/templates/demo-scenarios.yaml.example`. Served as-is to the UI. |
| `lab/handlers.py` | `HANDLERS = {"<handler name>": Handler(inject, revert, probe)}` for every `handler` named in the YAML, plus an optional `environment()` returning header facts. Import the contract with `from handler_contract import Handler` and build facts with `from facts import fact, item, console_url, console_link`. |

`probe()` reads the **live** environment and returns `{"injected": bool, "facts": [...]}`.
A fact is a labelled value the UI renders without knowing the domain:

```python
fact('Deployment', 'payment-processor', status='success', detail='payment-demo namespace',
     progress={'percent': 100, 'text': '1/1 replicas ready'}, link=console_link('Console', url))
fact('Pods', items=[item('payment-processor-7d9f', status='success', detail='Running')])
fact('DB_PASSWORD', 'from secret db-credentials', status='success')
```

`status` is a Cloudscape StatusIndicator type (`success`, `error`, `warning`, `pending`,
`stopped`, `in-progress`, `info`, `loading`). Helper modules next to `handlers.py` are
bundled too; they must not be named like a shared module (`engine`, `index`, `scenarios`,
`facts`, `devops_agent`, `handler_contract`, `validate`).

Validate before the first deploy:

```powershell
python shared/lab/lambda/validate.py <demo>/lab
```

## What the Lab provides

```
shared/lab/
├── lambda/          engine.py (durable function), index.py (API), scenarios.py, facts.py,
│                    devops_agent.py, handler_contract.py, validate.py, requirements.txt, tests/
├── cdk/             lab-backend.ts: the LabBackend construct (bundle, two functions, alias, IAM, API Gateway)
└── frontend/        LabPage.tsx (Cloudscape shell), ScenarioCards.tsx, AgentPanels.tsx, api.ts
```

### Engine

Every injection is one Lambda durable function execution: `inject` → `await-rollback`
(`wait_for_callback`, timeout = the scenario's `autoRevertSeconds`) → `revert`. Rollback
resolves the callback and the revert runs at once; nobody clicks and the wait times out
into the same revert. No state store: the environment says what is injected
(`probe()`), the execution history says where the run is. One scenario at a time: a second
inject while an execution is running is refused (HTTP 409).

### API (`/admin/*`)

| Route | Returns |
|---|---|
| `GET /admin/scenarios` | `scenarios.yaml` plus `alarmName` per scenario and `environment` (region, partition, Agent Space, header facts) |
| `GET /admin/status` | per scenario: `injected`, `facts` (probe facts, then an `Alarm` fact for alarm-driven scenarios), `run` or `lastRun` (phases, countdown) |
| `POST /admin/scenarios/{id}/inject` | starts an execution named `<id>-<epoch>`; 409 while one runs |
| `DELETE /admin/scenarios/{id}/inject` | resolves the running execution's callback (or reverts directly if the failure has no owning run) |
| `GET /admin/tasks` | recent tasks of the Agent Space with execution facts and Markdown summaries |
| `GET /admin/usage` | monthly agent hours per motion (rendered as spend) |

### CDK: `LabBackend`

```typescript
import { LabBackend } from '../../../../shared/lab/cdk/lab-backend';

const lab = new LabBackend(this, 'Lab', {
  labDir: path.join(__dirname, '..', '..', 'lab'),
  stageDir: path.join(__dirname, '..', '.lab-stage'),        // gitignore it
  namePrefix: `${projectName}-${environment}`,
  roleName: `${projectName}-${environment}-lab-role`,       // optional; when scripts grant the role by name
  devOpsAgentRegion, devOpsAgentSpaceId,
  triggerLambdaName: `${projectName}-${environment}-devops-trigger`,   // RCA demos only
  environment: { EKS_CLUSTER_NAME: clusterName, ALARM_NAME: alarmName },  // what handlers.py reads
  policyStatements: [/* what the handlers touch */],
  layers: [/* kubectl, ... */], vpc, vpcSubnets, securityGroups,        // when handlers need them
});
// lab.api (RestApi), lab.apiStageName, lab.role, lab.engineAlias, lab.engineFunction, lab.apiFunction
```

The construct lives outside the demo's `node_modules`, so the demo's CDK project must
resolve `aws-cdk-lib` and `constructs` for it (one copy, otherwise `instanceof` checks
across two copies fail). Three one-time edits, copied from the EKS demo:

- `tsconfig.json`: `"baseUrl": "."` and `"paths": { "aws-cdk-lib": ["node_modules/aws-cdk-lib"], "aws-cdk-lib/*": ["node_modules/aws-cdk-lib/*"], "constructs": ["node_modules/constructs"] }`; no `rootDir`.
- `cdk.json`: `"app": "npx ts-node -r tsconfig-paths/register --prefer-ts-exts bin/app.ts"` with `tsconfig-paths` as a dev dependency.
- `jest.config.js`: the same three mappings under `moduleNameMapper`.

Bundling is Docker-free (pip resolves manylinux wheels for the Lambda runtime) and falls
back to the CDK Docker image if `python`/`python3` is unavailable.

### Frontend: `LabPage`

```tsx
import LabPage from '../../../../../../shared/lab/frontend/LabPage'

<LabPage
  tagline="Break the Helios platform on purpose, watch the AWS DevOps Agent investigate, put it back."
  homeHref="/lab" onHome={() => navigate('/lab')}
  utilities={[{ type: 'button', text: 'Back to the app', iconName: 'arrow-left', onClick: () => navigate('/') }]}
/>
```

The page is its own Cloudscape shell (TopNavigation + full-width AppLayout with sticky
notifications); mount it on its own route, outside the demo application's layout, behind
the same authentication. It fetches `/admin/*` on the same origin (route it through the
demo's CloudFront or reverse proxy). The demo's Vite project must resolve the page's
dependencies to its own copies: `resolve.dedupe` for `react`, `react-dom`,
`react-markdown`, `remark-gfm`, `@cloudscape-design/components`,
`@cloudscape-design/global-styles`, and matching `paths` in `tsconfig.json` (react types
map to `node_modules/@types/react`). See the EKS portal's `vite.config.ts` and
`tsconfig.json`.

## Tests

```powershell
cd shared/lab/lambda
python -m pytest tests -q          # engine (both roads), API rules, YAML validation, on fixtures
```

Requires `aws-durable-execution-sdk-python`, `aws-durable-execution-sdk-python-testing`,
`PyYAML`, `boto3`, `pytest`.
