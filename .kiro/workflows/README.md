# Workflows

Kiro workflow recipes for this repository. A recipe is a graph of agent steps the Kiro
runtime executes in order, each step in its own session with fresh context; steps that
must happen (a review, a verification) are steps, not reminders.

Workflows are opt-in: Workspace Configuration, Workflows, Enable, then a new chat session
(setting `kiroAgent.workflows.enabled`; CLI `chat.enableWorkflows`). Launch a recipe by
asking Kiro in the parent chat, or `/workflow run <name> --<input> "value"` in the CLI.

> **Launch an existing recipe BY PATH — do not re-synthesize it.** When a committed recipe
> already covers the task (e.g. `devops-agent-demo` below), run THAT recipe with its declared
> inputs. Do not describe the task to the workflow-creator as a free-form prompt: the creator
> builds a different one-off workflow that does not know these agents' permission scopes, and
> can place a scoped agent (e.g. `devops-agent-demo-analyst`, which may only write
> `lab/scenarios.yaml` and `.kiro/workflow-runs/**`) in a step that must write elsewhere or
> commit — which fails at runtime, not validation. Re-synthesizing a recipe that already
> exists is both wasteful and how that failure happens. Reserve free-form creation for tasks
> with no existing recipe.

## devops-agent-demo

Builds a demo of one AWS DevOps Agent capability the way
[`native-agent-demo-guide.md`](../steering/native-agent-demo-guide.md) prescribes, with the
guide's phases as steps and its reviews done by agents that did not write the code.

```
analyse ─► pause: the builder answers the forks in the parent chat
        ─► build-loop (max 3): build ─► steering review ∥ code review ─► aggregate
        ─► deploy (the demo's own deploy-all.ps1, your shell's credentials)
        ─► observe: every withCapability becomes the agent's observed sentence
        ─► final steering review
```

| Step | Agent | Writes |
|---|---|---|
| `analyse` | `devops-agent-demo-analyst` (read-only, fetches the capability from the Agent Tools repository) | `<demo>/lab/scenarios.yaml` draft, `questions.md`; pauses the run with the questions |
| `build` | `devops-agent-demo-builder` (no deploy, no push, no `shared/` edits) | the demo, commits on the current branch, `build-notes.md` |
| `steering-review` | `devops-agent-demo-steering-reviewer` (read-only) | `steering-review.md/.json`: conformance to the two guides, the contributor guide, tracking, the shared-bricks contract |
| `code-review` | `semantic_reviewer` (bundled) | `code-review.md` with a VERDICT line |
| `build-aggregate` | `wf-review-aggregator` (bundled) | `build-review.json`; APPROVED stops the loop |
| `deploy` | `devops-agent-demo-deployer` (runs `deploy-all.ps1`, records outputs, never destroys) | `deployment.json` (Lab URL, demo credentials, Agent Space id) |
| `observe` | `devops-agent-demo-observer` (Lab API + DevOps Agent MCP) | rewrites `withCapability` with the agent's words; `observation.md/.json` |
| `final-review` | `devops-agent-demo-steering-reviewer` | `final-review.md/.json`; NOT OBSERVED is blocking |

Inputs: `capability` (name in the Agent Tools repository), `capability_kind`
(`skill` / `custom-agent` / `mcp`), `capability_ref`, `demo_path` (`<pillar>/<folder>`),
`run_id`. Run artifacts land in `.kiro/workflow-runs/<run_id>/` (gitignored: the
deployment record holds the Lab's demo credentials).

What the workflow does **not** do: push, open a PR, destroy, or edit `shared/` and the
steering. Publishing stays a human decision; when you want a PR, hand the branch to the
bundled `publish-pr` recipe.

Costs: the deploy step takes about an hour and leaves a demo running (~$5/day for the FSx
shape); the observe step spends DevOps Agent time (about $0.10 per chat review, $0.50 per
investigation). Each step is its own agent session, so a run uses more credits than one
long conversation; that is the price of reviews that are never skipped.

## Agents

The five agents live in [`.kiro/agents/`](../agents/) as Markdown with front matter: the
front matter sets tools and permissions (deny lists for `git push`, `gh pr`, `cdk deploy`,
`shared/**` where the phase must not do them), the body is the phase's instructions, and
`resources` load the steering files the phase needs. They are workflow step agents; they
also work as ordinary custom agents from the agent picker when you want to run one phase
by hand.
