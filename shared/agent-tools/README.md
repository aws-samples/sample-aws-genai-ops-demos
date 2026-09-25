# Agent Tools Consumption

How a demo in this repository consumes a capability published in the public
**Agent Tools** repository — [aws/tools-for-devops-agent](https://github.com/aws/tools-for-devops-agent):
skills, custom agents and MCP servers that extend AWS DevOps Agent.

## One artefact, one home

A capability lives in exactly one place: the Agent Tools repository. A demo that needs it
**references** it and fetches it at deploy time. Nothing from that repository is copied
into this one — no vendoring, no submodules. The demo carries the dependency (and the cost
if it breaks); the Agent Tools repository only has to keep published paths stable.

| Material | What it is | How a demo consumes it |
|---|---|---|
| Skill / custom agent | Markdown files | `shared/scripts/deploy-skill.*` — fetch at a ref, package the upload zip |
| MCP server | A server to deploy | `shared/scripts/deploy-mcp.*` — fetch at a ref, deploy from its manifest, wire the endpoint |

## Skills and custom agents

```powershell
& "..\..\shared\scripts\deploy-skill.ps1" -Skill eks-upgrade-readiness -Ref main
& "..\..\shared\scripts\deploy-skill.ps1" -CustomAgent aws-health-report -Ref main
```

```bash
../../shared/scripts/deploy-skill.sh --skill eks-upgrade-readiness --ref main
../../shared/scripts/deploy-skill.sh --custom-agent aws-health-report --ref main
```

What it does:

1. Sparse-fetches just `skills/<name>` (or `custom-agents/<name>`) at the ref into a temp
   directory — `git clone --depth 1 --filter=blob:none --sparse` + `git sparse-checkout set`.
2. Skills: builds `<name>.zip` following the Agent Tools upload rules — allowed file
   extensions only; `README.md`, `CHANGELOG.md`, `evals/` and `.skilleval.*` excluded.
3. Prints the upload step. Uploading is done in the DevOps Agent console (Agent Space →
   Skills). Pick **All agents** when a custom agent will use the skill.

Custom agents are created in the DevOps Agent web app: the script fetches the directory and
points at `SYSTEM_PROMPT.md` to paste.

Exports: `$global:AGENT_TOOLS_SKILL_ZIP` / `AGENT_TOOLS_SKILL_ZIP`, `AGENT_TOOLS_SKILL_DIR`.

## MCP servers

```powershell
& "..\..\shared\scripts\deploy-mcp.ps1" -Server aws-vpc-dns-diagnostics-mcp -Ref main `
    -Parameters @{ AllowedAccounts = "111111111111" }
```

```bash
../../shared/scripts/deploy-mcp.sh --server aws-vpc-dns-diagnostics-mcp --ref main \
    --param AllowedAccounts=111111111111
```

What it does:

1. Sparse-fetches `mcp/<name>` at the ref into a temp directory.
2. Reads the server's **`mcp-server.yaml`** manifest and runs the deploy command it declares
   (SAM, CDK or CloudFormation — the runner does not care which).
3. Reads the endpoint URL from the stack output the manifest names, applies the `/mcp` path
   rule, and prints the exact DevOps Agent registration step for the manifest's auth method.
4. `-Destroy` / `--destroy` runs the declared teardown and lists every residual the
   teardown does not remove.

Exports: `AGENT_TOOLS_MCP_ENDPOINT`, `AGENT_TOOLS_MCP_AUTH_METHOD`,
`AGENT_TOOLS_MCP_SIGNING_SERVICE`, `AGENT_TOOLS_MCP_STACK`.

The runner needs the toolchain the manifest declares (`sam`, `npx`/CDK, or the AWS CLI) and
reads AWS credentials and region the same way every other shared script does.

### The manifest: `mcp-server.yaml`

The runner never reads a server's internals — only its manifest, which describes the
consumption boundary: how to deploy it, which stack output carries the endpoint, how to
authenticate, how to register, how to tear down. Schema (v1):

```yaml
schemaVersion: 1
name: <server-dir-name>
kind: self-hosted | aws-hosted-reference     # reference = AWS hosts the MCP; only a connection deploys
pattern: gateway | lambda-http               # self-hosted only
iac: sam | cdk | cfn | terraform
deploy:
  workdir: .                                 # if the deployable is nested
  setup: [["sam", "build"]]                  # optional pre-steps, argv lists
  command: ["sam", "deploy", "...", "AllowedAccounts=${AllowedAccounts}"]   # ${Name} <- -Parameters / --param
  parameters: [{ name: AllowedAccounts, required: true, description: "..." }]
  stacks: [<main-stack>]
  auxiliaryStacks: [{ template: scoped-roles.yaml, scope: per-target-account }]   # not deployed by the runner
endpoint:
  output: McpEndpointUrl                     # stack output carrying the URL
  stack: <main-stack>
  includesMcpPath: true                      # false -> the runner appends /mcp exactly
  transport: streamable-http
  session: stateless | stateful
auth:
  method: sigv4 | oauth-client-credentials | oauth-3lo | api-key | multi-headers   # = DevOps Agent registration options
  signingService: lambda | execute-api       # sigv4: differs per HTTP mechanism
  callerActions: [ ... ]                     # IAM actions the DevOps Agent role needs
registration:
  mode: cli | console | cloudformation
  tools: { readOnly: [...], mutating: [...] }
smokeTest: ["uv", "run", "pytest", "tests/", "-q"]    # optional
teardown:
  command: ["sam", "delete", "..."]
  residuals: ["..."]                         # what the command does NOT remove
```

Rules: boundary facts only, never secrets (auth fields name *where* credentials come from),
argv lists not shell strings, unknown fields ignored, breaking changes bump `schemaVersion`.

### Where the manifest lives

**In the Agent Tools repository**, at the root of the server's own directory
(`mcp/<server>/mcp-server.yaml`), maintained there and kept honest by that repository's CI.
The description travels with the thing it describes. The runner finds it automatically after
the fetch — no flag, no copy on our side, nothing that can drift.

This repository does **not** keep copies of upstream manifests. A manifest describing someone
else's server, held here, silently lies the moment they change a deploy command or rename an
output — the exact drift the reference model exists to prevent.

`-Manifest` / `--manifest` is an escape hatch for one case: a demo needs a server whose
manifest is not upstream yet. Then the file belongs **in that demo's own folder**, marked
temporary, and is deleted once the server ships its own. Never in `shared/`.

## For demo authors

Call the scripts from `deploy-all.ps1` / `deploy-all.sh` the way you call
`check-prerequisites` today, after the prerequisites check. Declare the dependency in the
demo's README: the Agent Tools path and the ref it was tested against.
