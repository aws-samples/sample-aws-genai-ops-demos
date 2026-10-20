# v1.1 UI Design Proposal — AI Lambda Runtime Migration

> **Superseded (2026).** This is the historical design proposal. The shipped flow has no wizard,
> no rehearsal/real run mode and opens no pull request: **Migrate** is a direct action on the
> selected rows, and the migration ends with a pushed upgrade branch (`Branch ready`). Opening the
> pull request, reviewing and merging happen in the repository host. Wherever a section below
> mentions a wizard, a rehearsal mode, an "Open pull request" step or a merge-request link, read
> it as superseded by the **How it works** page of the deployed UI.

Read-only design pass. No code was changed by this document except its own creation.
Decisions here are grounded in the files cited; anything not verified is marked
**[UNVERIFIED]**.

---

## 1. Summary answer (the proposal in one screen)

> **Updated after alignment (see plan doc §9): the Refresh/Migrate split is the backbone of the
> UX.** Two distinct user triggers:
> - **Refresh ("Rescan my scope")** = the bulk fleet operation. Runs **Discover + Assess** over the
>   operator's scope as ONE durable Lambda execution (modeled on the lifecycle tracker's refresh).
>   This is what POPULATES the inventory with risk/priority/assessment data. Discover + Assess are
>   NOT wizard steps.
> - **Migrate (the Wizard)** = a per-function (or batch) action the operator triggers AFTER reading
>   the refreshed data. It **reads the assessment data Refresh already produced** — it does not
>   re-run Discover/Assess. Submit kicks off AWS Transform.
>
> So the loop is: *scope → Refresh (durable Discover+Assess) → read the inventory → pick → Migrate
> wizard → AWS Transform → status back in the inventory.* The three surfaces below sit inside that
> loop.

The v1.1 UI is **three surfaces, not four pages**:

1. **Home = a scoped inventory table** of *my* deprecated-runtime functions, filtered by
   **tag / account / region** exactly like `aws-services-lifecycle-tracker`, with a **Refresh
   ("Rescan my scope")** action that starts/polls the durable **Discover + Assess** run and shows
   it as Cloudscape `Steps` from the execution history. It leads with a small **labelled summary
   strip** (how many deprecated in my scope, by runtime, by urgency), then the triage table with
   **multi-select** for batch migrate. **Recommended lead: inventory-first** (table is the hero),
   with a thin KeyValuePairs summary above it — not a funnel/dashboard.
2. **Migrate action = a Cloudscape Wizard** launched from one selected row (or a batch), three
   steps — **Assess** (read-only review of the data Refresh ALREADY produced — the wizard does not
   re-run Discover/Assess) → **Decide** (the form) → **Review and start migration** (review page, no
   interactions, submit kicks off AWS Transform via the durable workflow). The transformation picked
   in Decide maps to the managed transformation for the runtime family (plan §9.3:
   `AWS/lambda-nodejs-runtime-upgrade`, `AWS/dotnet-lambda-upgrade`, `AWS/python-version-upgrade`),
   and the Discover/Assess findings are fed into AWS Transform's `additionalPlanContext`.
3. **Validation / progress = back in the inventory**, out of the wizard. The row's status moves
   `Migrating → PR open → Merged`, and a **detail drawer / split panel** on the row shows the
   durable run as Cloudscape `Steps` plus the merge-request link.

> **Phasing note:** in **Phase 1** only the Refresh path (Discover + Assess + the scoped inventory)
> is wired; the Migrate wizard shell exists but its submit/AWS-Transform action is DISABLED
> ("coming soon"). **Phase 2** wires the wizard to AWS Transform (CodeBuild `atx` → open a PR).

Navigation collapses from v1's four pages (`Dashboard / Functions / FunctionDetail /
MigrationPlan`) to **one primary page** ("My functions" inventory) plus the wizard overlay and
the row drawer. The Dashboard board-of-widgets, both charts, the per-function 4-source grid, and
the whole Transform tab are **cut**. The AgentCore removal shows up as `agentcore.ts` (SigV4
browser invokes) being replaced by `api.ts` (HTTPS to API Gateway with a Cognito JWT) — this is
already the pattern in the sibling demo's `frontend/src/api.ts`.

---

## 2. Evidence: what exists today, what it costs us

### 2.1 Current navigation (to be replaced)
`frontend/src/App.tsx` registers three nav links and four routes:
- `App.tsx` `NAV_ITEMS`: `Dashboard` (`/`), `Functions` (`/functions`), `Migration Plan`
  (`/migration-plan`).
- Routes: `/` → `Dashboard`, `/functions` → `Functions`, `/functions/:arn` → `FunctionDetail`,
  `/migration-plan` → `MigrationPlan`.
- Shell uses `TopNavigation` + a `SideNavigation`, `AppLayout` with `toolsHide`, a global
  `SplitPanelProvider`/`SplitPanelContext`. There is **no tag/account/region scope** anywhere —
  the whole app shows every function it finds.

### 2.2 `Dashboard.tsx` — the board + chart soup (CUT)
- Imports `@cloudscape-design/board-components` (`Board`, `BoardItem`) and renders **draggable,
  resizable widgets** (`buildBoardItems()` returns `total`, `ta-status`, `migration-status`,
  `complexity`, `runtime-chart`, `complexity-chart`).
- Imports `PieChart` + `CartesianChart` + `highcharts`: a **donut of runtimes**, a **column
  chart of complexity**, a **scatter "deprecation timeline"**.
- Header description still says "...using Amazon Bedrock and AgentCore" and the primary action is
  a global **Scan** button that calls `invokeDiscover()`.
- Conflicts with `demo-lab-ui-guide.md` → **"no chart soup"**, **"every word has a label"**, and
  the whole page is *fleet-level aggregate* with no scoping, which §8.1-8.2 of the plan rejects
  (there is no omniscient fleet owner).

### 2.3 `Functions.tsx` — closest to what we keep, but under-scoped
- A `Table` with columns `function_name / region / runtime / alert_status / migration_status /
  priority_score`, a single `TextFilter`, client-side `Pagination`, `selectionType="single"`,
  and a `FunctionDetailPanel` opened in the split panel showing the **4-source grid** (Trusted
  Advisor / Lambda / CloudWatch / Bedrock) via `ColumnLayout columns={4}`.
- This is the **seed of the new home**, but: filter is free-text only (no tag/account/region
  properties), selection is `single` (no batch), there is no scoped summary, and the 4-column
  source grid is more than a triage operator needs at a glance.

### 2.4 `FunctionDetail.tsx` — per-function 4-source soup + the old Transform (mostly CUT)

> **Superseded (2026):** the shipped flow has no rehearsal/real mode and opens no pull request;
> the migration ends with a pushed branch — see the How it works page.
- Three tabs: **Overview** (the same 4-source `ColumnLayout columns={4}`: Trusted Advisor /
  Lambda / CloudWatch / Bedrock), **Assessment** (Nova code analysis), **Transformation**.
- The **Transformation tab** drives the old Nova-per-file rewrite: `invokeTransform()`, a
  `transform_progress` heartbeat poll, `ProgressBar` of "file X/N", a changelog, and an
  **AgentCore Code Interpreter** validation card. This is the §8.3 "gadget" being **replaced by
  AWS Transform**, so the entire tab goes.
- Buttons `Assess` / `Transform` call `invokeAnalyze` / `invokeTransform` from `agentcore.ts`.

### 2.5 `MigrationPlan.tsx` — client-side waves (CUT as a page)
- `generatePlanLocally()` buckets functions into "Wave 1 Quick Wins … Wave 4 Low Priority" and
  renders KPI cards + a markdown report. Useful idea, but it is a *second aggregate view* that a
  scoped operator does not need as a standalone page. The urgency it encodes is folded into the
  inventory's **urgency column + default sort** instead.

### 2.6 `agentcore.ts` — the AgentCore coupling (REPLACED)
- Imports `@aws-sdk/client-bedrock-agentcore`, `@aws-sdk/client-cognito-identity`,
  `fromCognitoIdentityPool`. `invokeRuntime()` builds **SigV4 credentials in the browser** from a
  Cognito Identity Pool and calls `InvokeAgentRuntimeCommand` against three runtime ARNs
  (`VITE_DISCOVER_RUNTIME_ARN`, `VITE_ANALYZE_RUNTIME_ARN`, `VITE_TRANSFORM_RUNTIME_ARN`).
- Public functions: `invokeDiscover`, `readInventory`, `invokeAnalyze`, `invokeTransform`.
- Plan §5.2: *"Delete `frontend/src/agentcore.ts`; add `frontend/src/api.ts`."* Confirmed as the
  intended backend-contract change — this doc does **not** redesign the backend.

### 2.7 The sibling pattern we replicate — `aws-services-lifecycle-tracker/frontend/src`
Read directly; these are the concrete patterns to lift:

- **Scope lives in `TopNavigation`, applied once in the data layer.** `App.tsx` puts a **Tag
  filter** utility button in `TopNavigation` (`tagFilter.filters.length ? 'Tag filter: …' :
  'Tag filter'`, icon `filter`), opens `TagFilterModal`, and a `TagFilterProvider` holds the
  global filter. A change bumps `tagFilter.version`, which **keys the `<Routes>`** so pages
  re-mount already scoped. A sticky `Flashbar` shows the active scope with a **Clear** action and
  "N of M resources". — `App.tsx`.
- **The scope model** (`tag-filter.ts`): `TagFilter = { key, values[] }`, OR-widening semantics,
  serialized to URL (`?tag=BU:LOB1`) and `localStorage`. `scopeInventory()` narrows rows and
  recomputes counts; `tagKeys()` / `tagValues()` suggest from what the last scan saw.
- **The tag picker** (`TagFilterModal.tsx`): `Autosuggest` key + optional value + Add,
  chosen pairs as a `TokenGroup`, a live "Scope: … → matched of total" preview. Modeled on the
  Resource Groups Tag Editor console.
- **Account + region are first-class row fields and filter properties.** `api.ts` `DeprecationItem`
  carries `region?`, `account_id?`, `account_name?`. `MyResources.tsx` uses **`PropertyFilter`**
  (not a bare `TextFilter`) with `service / region / account` properties **plus `tag:` property
  keys**, `useCollection` from `@cloudscape-design/collection-hooks`, `CollectionPreferences`
  (page size + visible columns persisted), `Pagination`, and a `Select` **scope dropdown**
  ("Needs attention / Everything found / By horizon / By status").
- **The durable run is Cloudscape `Steps`.** `components/RefreshSteps.tsx` maps pipeline phases
  from the execution history to `Steps` (`pending / in-progress → loading / success / warning /
  error / stopped`), a `ProgressBar` on the in-flight phase when its size is known, a gen-AI
  sparkle on the Bedrock phase, and collapses to an `ExpandableSection` ("Last refresh: N of M
  steps done") once finished. The header says *"Runs server-side: you can navigate away, the page
  re-attaches when you come back."*
- **The API contract** (`api.ts` header): `POST /actions` (router), **`POST /refresh`** (start/adopt
  the durable pipeline), **`GET /refresh/{arn}`** (poll execution status). Every call is HTTPS to
  the HTTP API with the **Cognito ID token in `Authorization`**; *"The browser holds no AWS
  credentials."* This is exactly the AgentCore→API move the Lambda demo makes.

### 2.8 Cloudscape Wizard — the authoritative rules
Fetched from `cloudscape.design/components/wizard/index.html.md`
([Wizard](https://cloudscape.design/components/wizard/index.html.md); content rephrased for
licensing compliance):
- It is a **multi-page form**. Embed it in a `<form>` / `role="form"`; use client-side (form
  field) or server-side (page-level `Alert`) validation.
- Set **`contentType="wizard"` on `AppLayout`**; the wizard **must be the first component in the
  `content` slot**; **do not** wrap it in `ContentLayout`.
- **≤ 7 steps.** Each **nav step title must match its form header title** and **begin with an
  active verb**.
- The **last step is a review page**: *"Review and \[verb\]"*. **No interactions on the review
  page** (no inline edit, no edit modals) — edits happen by going back to a step. Show choices in
  the same order they were entered.
- **Exit guard:** if the form has user input and the user tries to leave, prompt a **modal** to
  confirm exit; if there is no input, exit without a prompt.
- Buttons: first step `Cancel`/`Next`; middle `Cancel`/`Previous`/`Next`; last
  `Cancel`/`Previous`/`Submit`. The submit button is `[Active verb][resource]` and can show a
  loading state while submitting.
- State: controllable via `activeStepIndex` + `onNavigate`; `onSubmit` fires on the last step.
  (Component props/wrappers per the fetched API table.)

### 2.9 The decisions this honors (plan doc)
`docs/v2-lessons-and-v1.1-plan.md`:
- §8.1-8.2 scoped operator; tag / multi-account (hub-and-spoke) / multi-region; single-account
  default; data keyed on `account+region`.
- §8.3 keep Discover + Analyze (cheap Nova triage/assess), **replace Transform with AWS Transform**
  (CodeBuild `atx`) → open a merge request, never merge.
- §8.5 three-part layout: scoped inventory home + wizard (Assess / Decide / Review and start
  migration) + post-submit progress in the inventory.
- §2.6 "source co-located with IaC" is a first-class flag; the PR covers **app code only**; repo
  is an explicit input (CodeCommit default stand-in).
- §3.0 / §5.3 scope the Transform transformations to **Python and Node.js** (≈98% of the real
  deprecated surface): `AWS/python-version-upgrade`, `AWS/nodejs-version-upgrade`.
- §3.3 AWS Transform custom runs only in **us-east-1 / eu-central-1** — a region gate the wizard
  must surface.
- §2.4 **rehearsal (dry-run) vs real** is a wizard choice.

---

## 3. Navigation structure (v1.1)

```
TopNavigation
  identity: "AWS Lambda Runtime Migration"
  utilities (left→right):
    [ Tag filter: BU: payments ]   <- global scope button (icon "filter"), opens Tag filter modal
    [ Rescan my scope ]            <- starts/adopts the durable discover+analyze run for current scope
    [ user@example.com ]           <- Cognito identity / sign out

SideNavigation
  section "My functions"
    - My functions        /          <- THE inventory home (default)
    - Timeline            /timeline   <- OPTIONAL, see §7; deprecation-date view of my scope
  divider
  - Settings              /settings   <- scope defaults, multi-account preflight status [UNVERIFIED backend]
  - Documentation (external)
```

Pages removed vs v1:
- **`Dashboard` (board+charts) → deleted.** Its only keepers (counts) become the inventory's
  summary strip (§4.1).
- **`Functions` → becomes `My functions`** (the home), upgraded with scope + batch + summary.
- **`FunctionDetail` → becomes a trimmed row drawer** (§6), not a full routed page with tabs.
- **`MigrationPlan` → deleted as a page;** its urgency/wave logic collapses into the inventory's
  **urgency column + default sort** (and optionally the Timeline page).

Routes become: `/` → `MyFunctions`; `/timeline` → `Timeline` (optional); `/settings` → `Settings`.
The wizard is **not a nav item** — it is launched from the inventory and takes over `content` with
`contentType="wizard"` while active (either a `/migrate` route rendered without the side nav, or an
in-place overlay). Recommended: a `/migrate?arns=…` route so the exit guard and browser back behave
naturally.

---

## 4. Surface 1 — Home: the scoped inventory ("My functions")

### 4.1 Recommended lead: **inventory-first** (one alternative below)
**Recommendation — inventory-first.** The hero is the table; above it sits a thin, labelled
summary strip (`KeyValuePairs`, no charts). Rationale: the persona triages *many* functions in
*their* scope (§8.1); the fastest path is "see my list, sort by urgency, select, migrate." The
lifecycle tracker proves this exact shape works at fleet scale (`MyResources.tsx` is a table-first
page). It satisfies `demo-lab-ui-guide` "no chart soup / every word labelled" by construction.

**Alternative (not recommended) — funnel-first.** Lead with a `Steps`-style funnel "Discovered N →
Assessed N → Migrating N → PR open N → Merged N" as the hero, table below. Rejected: it reintroduces
an aggregate-first dashboard the operator must scroll past to do their job, and the same funnel
counts live more cheaply as a one-line summary + the status column. Keep the funnel idea only as the
five summary facts in §4.2, not as a hero.

### 4.2 The summary strip (above the table)
A single `KeyValuePairs columns={4}` (full width), every value labelled, no charts. All numbers are
**within the active scope** and say so:

- **In my scope** — `N functions` (detail: `across A accounts, R regions`).
- **Deprecated / EOL** — `StatusIndicator` + count (from Trusted Advisor alert_status red/yellow).
- **By runtime** — top 2-3 as text: `python3.9 ×12 · nodejs16.x ×7 · other ×3` (the real
  concentration, §3.0). Not a donut.
- **Urgency (AI)** — `Critical N · High N · Medium N · Low N` with a gen-AI label, from Nova
  priority (`priority_label`). Mirrors `Functions.tsx` priority but as labelled facts.
- **Migration status** — `Not started N · Migrating N · PR open N · Merged N` (`StatusIndicator`s),
  derived from the durable execution history, not a stored flag (per `demo-lab-ui-guide` "injected
  is derived, never from a stored flag" discipline applied to migration status).

```
┌─ My functions ───────────────────────────────── [ Migrate (2) ] [ Rescan my scope ] ─┐
│ In my scope: 18 functions          Deprecated/EOL: ● 15      By runtime: py3.9 ×9 ·    │
│   across 1 account, 2 regions                               node16 ×6 · py3.8 ×3       │
│ Urgency (AI): Crit 3 · High 5 · Med 7 · Low 3   Status: Not started 14 · Migrating 1 · │
│                                                          PR open 2 · Merged 1          │
└───────────────────────────────────────────────────────────────────────────────────────┘
```

### 4.3 The scope controls (replicate the lifecycle tracker)
- **Tag filter** — a `TopNavigation` utility button + a **Tag filter modal** (lift
  `TagFilterModal.tsx` / `tag-filter.ts`: Autosuggest key + optional value + Add, TokenGroup of
  chosen pairs, OR-widening, URL + localStorage, live scope preview). This is the primary "my area
  of duty" control.
- **Account + region** — as **`PropertyFilter` properties** on the table (`account`, `region`),
  plus free-text, exactly like `MyResources.tsx`. Account appears only when multi-account is
  deployed; single-account users see region but a trivial/absent account filter (single-account is
  the default and must not expose multi-account — §8.2). **[UNVERIFIED]** whether multi-account
  is wired in the v1.1 backend yet; the UI should render the account column/property conditionally
  on the data carrying `account_id`.
- **Sticky scope banner** — a `Flashbar notifications` strip (as in `App.tsx`) showing the active
  tag scope + "N of M functions" + **Clear**, so the operator always knows what they are looking
  at.
- **Scope dropdown (optional)** — a `Select` like `MyResources.tsx` ("Needs attention / Everything
  / By urgency / By status") as a quick lens on top of the tag/account/region scope.

### 4.4 Table columns
`Table` with `useCollection`, `PropertyFilter`, `Pagination`, `CollectionPreferences`,
`selectionType="multi"`, `trackBy` = the `account#region#arn` key (§8.2 keying), split-panel on
row click. Columns:

| Column | Source | Notes |
|---|---|---|
| **Function** | TA/Lambda | name (from ARN), `variant="awsui-value-large"` or link that opens the drawer |
| **Account** | discovery | `account_name` (id as secondary); hidden when single-account |
| **Region** | discovery | plain |
| **Runtime** | Lambda | e.g. `python3.9`; this is the migration subject |
| **Urgency** | **Nova (AI)** | `Badge` Critical/High/Medium/Low + gen-AI icon in the header (as `Functions.tsx` does); **default sort key, descending** |
| **Deprecation** | TA | date + `days_to_deprecation` as `StatusIndicator` (red past EOL, warning soon) |
| **Assessment** | Analyze phase | `StatusIndicator`: Not assessed / Assessed (complexity Low/Med/High as detail) |
| **Migration status** | **durable history** | `Not started / Migrating / PR open / Merged / Failed` via `StatusIndicator`; **the live beat** |
| **Merge request** | deliver step | `Link external` to the opened PR when present, else `—` |

- **Default sort:** Urgency descending, tiebreak days-to-deprecation ascending (soonest first).
  This replaces the `MigrationPlan` "waves" — the operator reads the queue top-down.
- **Bulk selection:** `selectionType="multi"`; a primary **Migrate (N)** button in the table
  header is enabled when ≥1 row is selected and launches the wizard in batch mode (§5.6).
- **Empty state:** "No deprecated-runtime functions in this scope. Rescan my scope, or widen the
  tag filter." (follow `Functions.tsx` empty-state tone, scoped wording).

```
Filter: [ search · property: account = … · region = … · tag:BU = payments ]      ⚙ prefs
┌───┬───────────────────┬─────────┬──────────┬───────────┬──────────┬────────────┬──────────────┬───────────────┐
│ ☑ │ Function          │ Account │ Region   │ Runtime   │ Urgency▼ │ Deprecation│ Assessment   │ Migration     │
├───┼───────────────────┼─────────┼──────────┼───────────┼──────────┼────────────┼──────────────┼───────────────┤
│ ☑ │ orders-api        │ prod    │ us-east-1│ python3.9 │ 🔴 Crit  │ ● past EOL │ High         │ ● PR open ↗   │
│ ☑ │ invoice-worker    │ prod    │ us-east-1│ nodejs16.x│ 🟠 High  │ ⚠ 42 days  │ Medium       │ ○ Not started │
│ ☐ │ nightly-report    │ dev     │ eu-west-1│ python3.8 │ 🟡 Med   │ ⚠ 120 days │ Not assessed │ ○ Not started │
└───┴───────────────────┴─────────┴──────────┴───────────┴──────────┴────────────┴──────────────┴───────────────┘
                                                                               ‹ 1 2 3 › 50 / page
```

---

## 5. Surface 2 — the Migrate wizard

> **Superseded (2026).** The shipped flow has no wizard, no rehearsal/real run mode and opens no
> pull request: **Migrate** is a direct action on the selected rows, and the migration ends with
> a pushed upgrade branch (`Branch ready`). Opening the pull request, reviewing and merging happen
> in the repository host. See the **How it works** page. The section below is kept as the
> historical proposal.

Launched from a selected row's **Migrate** action or the header **Migrate (N)** batch button. The
wizard owns `content` with `contentType="wizard"` and is the first component in the slot (§2.8). It
is wrapped in a `<form>`. Three steps; nav titles = form headers, active-verb:

1. **Assess** 2. **Decide** 3. **Review and start migration**

> **Alignment note (plan §9.1):** the wizard's **Assess** step is **read-only review of the data the
> Refresh run already produced** (Discover + Assess) — the wizard does NOT run Discover/Assess. It
> only displays the stored findings so the operator can confirm what they are migrating, then make
> the Decide choices. The Decide/Review choices + the stored findings feed AWS Transform's
> `additionalPlanContext`. **Phase 1:** the wizard shell exists but the final submit (AWS Transform)
> is DISABLED ("coming soon"); Phase 2 wires it.

Exit guard: once the user changes anything in **Decide**, Cancel / browser-back prompts a confirm
`Modal` ("Leave migration setup? Your choices will not be saved."). On the pristine Assess step, no
prompt.

### 5.1 Step 1 — "Assess" (read-only review of Discover/Analyze findings)
No form controls; a read-only summary so the operator confirms *what* they are migrating. Content as
`Container`s with `KeyValuePairs` / `StatusIndicator` (no charts, every value labelled). A gen-AI
label on the Nova-derived facts (as the current `FunctionDetail` does).

- **Function** — name, account, region, ARN (code), current runtime. (TA + Lambda sources.)
- **Complexity (AI)** — `Badge` Low/Med/High + the Nova assessment summary; deprecated APIs /
  breaking changes / dependency issues as short `ExpandableSection`s (reuse the Analyze result
  shape already in `FunctionDetail.tsx`, trimmed).
- **Recommended target runtime (AI)** — e.g. `python3.9 → python3.13` **[UNVERIFIED target
  values]** (`target_runtime` from Analyze).
- **Caveats** — any refusal/skip reason (container-image package type → not source-transformable,
  §4 of plan): if present, this step shows a `warning` `Alert` and the wizard's submit is blocked
  for that function.
- **Source co-located with IaC flag** (§2.6) — a `StatusIndicator`/`Alert`:
  *"This function's source appears co-located with its IaC. The migration opens a pull request
  covering **application code only** — infrastructure definitions are not changed."* This sets
  expectations before the repo choice in Decide. **[UNVERIFIED]** how the backend detects/stores
  this flag.

```
Step 1 of 3   ·   Assess
┌ Function ───────────────────────────────────────────────────────────────┐
│ Name: orders-api   Account: prod   Region: us-east-1   Runtime: python3.9 │
│ ARN:  arn:aws:lambda:us-east-1:…:function:orders-api                      │
└───────────────────────────────────────────────────────────────────────────┘
┌ Assessment  ✦ Generated by AI ──────────────────────────────────────────┐
│ Complexity: [ Medium ]   Recommended target: python3.9 → python3.13      │
│ Summary: Uses deprecated `imp` module; 2 dependency pins need bumping.    │
│ ▸ Deprecated APIs (2)   ▸ Breaking changes (1)   ▸ Dependency issues (2)  │
└───────────────────────────────────────────────────────────────────────────┘
┌ ⚠ Source co-located with IaC ───────────────────────────────────────────┐
│ The PR will change application code only; infrastructure is not touched.  │
└───────────────────────────────────────────────────────────────────────────┘
[ Cancel ]                                                          [ Next ]
```

### 5.2 Step 2 — "Decide" (the form)
Real `FormField` controls, client-side validation. The choices (plan §8.5 / §5.3):

- **Target runtime** — `Select`, defaulted to the AI recommendation, constrained to the valid
  upgrade targets for the source family. **[UNVERIFIED]** exact catalog of selectable targets.
- **Source repository** — the §2.6 first-class input:
  - `RadioGroup`: **Provisioned CodeCommit (default)** vs **Supplied repository**.
  - If supplied: `Input` repo URL (+ a note that a token/secret is configured at deploy, not typed
    here — honor the placeholder/secret convention; never a value-shaped field). **[UNVERIFIED]**
    whether v1.1 Phase 2 accepts a supplied repo in the UI or CodeCommit-only; design the control
    but gate the "Supplied" radio on backend capability.
  - A reminder line echoing the co-located-with-IaC caveat: *"The PR targets the code repository,
    which may differ from the IaC repository."*
- **Transformation** — `Select` scoped to **Python / Node.js** (§3.0): `AWS/python-version-upgrade`
  or `AWS/nodejs-version-upgrade`, auto-selected from the source runtime family and normally
  read-only (shown so the operator sees which managed transformation runs).
- **Run mode** — `RadioGroup`: **Rehearsal (dry run — produce the diff, do not push/open a PR)**
  vs **Real (open a pull request, never merge)** (§2.4). Default **Rehearsal** for safety in a demo.
- **Region notice** — if the deployment region is not us-east-1 / eu-central-1, a `warning`
  `Alert`: *"AWS Transform custom jobs run only in us-east-1 and eu-central-1; this migration runs
  in \<gated region\>."* (§3.3). Derive host/region from the partition per `demo-lab-ui-guide`.

```
Step 2 of 3   ·   Decide
┌ Target runtime ─────────────────────────────────────────────────────────┐
│ [ python3.13  ▾ ]   (recommended by assessment)                          │
├ Source repository ───────────────────────────────────────────────────────┤
│ (•) Provisioned CodeCommit repo   ( ) Supplied repository                 │
│     Repo: lambda-migration-demo (CodeCommit, this account)               │
│     ⓘ The PR targets the code repo, which may differ from the IaC repo.  │
├ Transformation ───────────────────────────────────────────────────────────┤
│ AWS/python-version-upgrade   (selected from runtime family)              │
├ Run mode ─────────────────────────────────────────────────────────────────┤
│ (•) Rehearsal — produce the diff, do not open a PR                        │
│ ( ) Real — open a pull request (never merged)                             │
└───────────────────────────────────────────────────────────────────────────┘
[ Cancel ]                                            [ Previous ] [ Next ]
```

### 5.3 Step 3 — "Review and start migration" (review page, no interactions)
Pure summary of Steps 1-2 in the same order, per Cloudscape (§2.8). Each section has an **"Edit"
link that routes back to its step** (allowed — the edit happens on the step, not the review page;
no inline fields here). A final `Alert info` restates the boundary: *"Submitting starts an AWS
Transform job via the durable workflow. In Real mode it opens a pull request and never merges."*
Submit button: **`Start migration`** (loading state while the `POST` to the API returns the
execution handle).

```
Step 3 of 3   ·   Review and start migration
┌ Function & assessment                                   [ Edit ] ────────┐
│ orders-api · prod · us-east-1 · python3.9 → python3.13 · Complexity Medium│
├ Choices                                                 [ Edit ] ─────────┤
│ Source: Provisioned CodeCommit (lambda-migration-demo)                    │
│ Transformation: AWS/python-version-upgrade     Run mode: Rehearsal        │
├───────────────────────────────────────────────────────────────────────────┤
│ ⓘ Submitting starts AWS Transform via the durable workflow. Real mode     │
│    opens a pull request and never merges.                                 │
└───────────────────────────────────────────────────────────────────────────┘
[ Cancel ]                                      [ Previous ] [ Start migration ]
```

### 5.4 Submit → backend contract (confirm, don't redesign)
Submit calls the new `api.ts` (not `agentcore.ts`): a `POST` that starts/adopts the durable
execution for the selected function(s) with the chosen target/repo/transformation/mode, mirroring
the lifecycle tracker's `POST /refresh` → `GET /refresh/{arn}` shape (`api.ts` header). The browser
sends the Cognito JWT; it holds no AWS credentials. **[UNVERIFIED]** the exact v1.1 route names
(e.g. `POST /migrate`, `GET /migrate/{arn}`); the plan fixes the pattern, not the paths.

### 5.5 Step count & titles comply
3 steps ≤ 7; nav titles `Assess / Decide / Review and start migration` match the form headers and
begin with active verbs; last step is "Review and \[verb\]"; no interactions on it beyond
back-to-step Edit links.

### 5.6 Batch mode
When launched from **Migrate (N)**, Step 1 Assess becomes a **read-only summary table of the N
selected functions** (name, runtime, complexity, any refusal flag), with refused ones (container
image) visibly excluded from the submit set. Step 2 Decide applies choices to the batch (target
runtime per-family is derived; run mode + repo-default apply to all). Step 3 reviews the batch and
submits one durable run per function (bounded, per §8.2 per-(account,region) unit-of-work; one
failing function fails only its own unit).

---

## 6. Surface 3 — Validation / progress (back in the inventory)

After **Start migration**, the wizard closes and returns to **My functions**. The acted rows show
`Migration status = Migrating`. The operator watches progress **in the inventory**, not in a
wizard:

- **Row status** advances `Migrating → PR open → Merged` (or `Failed`), driven by the **durable
  execution history** (not a stored flag), polled like the lifecycle tracker (`GET
  /refresh/{arn}`-style; §2.7). Poll faster while a run is active, slower when idle (the
  `demo-lab-ui-guide` 3 s / 10 s rule).
- **Row drawer / split panel** (replaces the heavy `FunctionDetail` page): clicking a row opens the
  split panel with:
  - the trimmed **Assess facts** (function, runtime→target, complexity),
  - the **durable run as `Steps`** — reuse `RefreshSteps.tsx` almost verbatim: steps
    `Resolve repository → Run AWS Transform (CodeBuild) → Secret & dependency scan → Open pull
    request`, statuses from history, a `ProgressBar` on the in-flight CodeBuild step, a gen-AI
    sparkle where Nova/assessment contributed, collapsing to an `ExpandableSection` when done. The
    header carries the same "runs server-side, re-attaches when you come back" reassurance.
  - the **Merge request link** (`Link external`) once the deliver step opens the PR; in **Rehearsal
    mode**, instead of a PR link, a link/note to the produced diff (no PR opened).
- **Sticky banner while active** — optional `Flashbar` ("Migrating orders-api · AWS Transform
  running") consistent with the lifecycle tracker's active-run banner.

The human **"validate"** beat (the user's step 3 "validate migration") is **reviewing the opened
PR** in the source repo — the UI's job is to surface the PR link and the run's Steps, not to embed
a diff viewer.

```
My functions                                            [ split panel ▸ orders-api ]
┌ orders-api — migration ──────────────────────────────────────────────────┐
│ python3.9 → python3.13 · Complexity Medium · Mode: Real                    │
│ Steps:                                                                     │
│   ✓ Resolve repository          lambda-migration-demo (CodeCommit)         │
│   ⟳ Run AWS Transform           CodeBuild build #42 ▐▓▓▓▓░░░░▌ 55%          │
│   ○ Secret & dependency scan                                               │
│   ○ Open pull request                                                      │
│ (runs server-side — you can navigate away; this re-attaches on return)     │
│ Merge request: — (opens when the run reaches "Open pull request")          │
└───────────────────────────────────────────────────────────────────────────┘
```

---

## 7. What to CUT, mapped to the rules

| Cut | File / symbol | Why (rule) |
|---|---|---|
| Draggable board + resizable widgets | `Dashboard.tsx` `Board`/`BoardItem`, `buildBoardItems()` | `demo-lab-ui-guide`: a Lab/fleet page is a table or labelled facts, not rearrangeable widgets; adds no triage value |
| Runtime donut, complexity column chart, deprecation scatter | `Dashboard.tsx` `PieChart`/`CartesianChart`/`highcharts` | **"No chart soup."** Replaced by labelled summary facts (§4.2) |
| Global unscoped Scan hero | `Dashboard.tsx` Scan button + full-fleet aggregate | §8.1 no omniscient fleet owner; scope-first instead |
| Per-function 4-source grid as the main read | `FunctionDetail.tsx` + `Functions.tsx` `ColumnLayout columns={4}` (TA/Lambda/CloudWatch/Bedrock) | Too much at triage time; trimmed to Assess facts in the wizard + row drawer. **"Every word labelled"** kept, volume cut |
| Entire Transformation tab (Nova-per-file, progress heartbeat, Code Interpreter card) | `FunctionDetail.tsx` transform tab, `invokeTransform` | §8.3 the old per-file rewrite is the "gadget" **replaced by AWS Transform** |
| Migration Plan page (client-side waves) | `MigrationPlan.tsx` `generatePlanLocally()` | Second aggregate the scoped operator doesn't need; urgency folds into the inventory sort |
| Browser SigV4 AgentCore invokes | `agentcore.ts` (whole file) | §5.2 replaced by `api.ts` → API Gateway + Cognito JWT; browser holds no AWS creds |
| "...and AgentCore" copy | `Dashboard.tsx` header description | AgentCore is removed from the architecture |

**Timeline page (`/timeline`) — keep as optional.** The lifecycle tracker has one and it reads well
for "what's due when" within scope. It is **not** required for the three-beat flow; include it only
if cheap. Marked optional in §3's nav.

---

## 8. How the AgentCore removal shows up in the UI layer (confirmation only)

- **Delete** `frontend/src/agentcore.ts` (SigV4 + Cognito Identity Pool + `InvokeAgentRuntimeCommand`
  against three runtime ARNs). **Add** `frontend/src/api.ts` that calls the HTTP API with the
  Cognito **ID token** in `Authorization` — same shape as
  `aws-services-lifecycle-tracker/frontend/src/api.ts` (`POST` to start the durable run, `GET` to
  poll execution status).
- Call-site changes: `invokeDiscover`/`readInventory` → inventory GET/POST for the scope;
  `invokeAnalyze` → folded into the discover+analyze durable run (triggered by **Rescan my scope**);
  `invokeTransform` → the wizard's **Start migration** POST.
- `App.tsx` loses the Identity-Pool wiring assumptions; auth stays Cognito user-pool sign-in but now
  yields a JWT for the API, not AWS credentials. This is a **backend-contract change already decided
  in the plan (§5.1-5.2)** — this document confirms the UI surface of it and does **not** redesign
  the backend.

---

## 9. Open items / unverified (confirm during implementation)

- **[UNVERIFIED]** Whether v1.1's backend already carries `account_id`/`account_name` on rows and
  whether multi-account (hub-and-spoke) is wired yet — the account column/property must render
  conditionally so single-account stays the clean default (§8.2).
- **[UNVERIFIED]** Exact new API routes for the migration (the plan fixes the `POST`-start /
  `GET`-poll pattern, not the paths).
- **[UNVERIFIED]** How the backend detects/stores the **"source co-located with IaC"** flag and the
  **container-image refusal** reason surfaced in Assess.
- **[UNVERIFIED]** The selectable **target-runtime catalog** and whether the UI offers a **supplied
  repository** in Phase 2 or is CodeCommit-only at first (design both; gate the control on backend
  capability).
- **[UNVERIFIED]** Whether Rehearsal mode exposes a downloadable/linkable diff artifact for the row
  drawer, or only a "no PR opened" status.
- **Reuse targets to lift (not copy blindly):** `tag-filter.ts`, `TagFilterModal.tsx`,
  `TagFilterProvider.tsx`, `RefreshSteps.tsx`, and the `useCollection`+`PropertyFilter`+
  `CollectionPreferences` table scaffolding from `MyResources.tsx`.

---

### Sources
- Cloudscape Wizard guidelines —
  [cloudscape.design/components/wizard](https://cloudscape.design/components/wizard/index.html.md)
  (content rephrased for licensing compliance).
- In-repo files cited inline:
  `operations-automation/ai-lambda-runtime-migration/frontend/src/{App.tsx,agentcore.ts,pages/*}`,
  `operations-automation/aws-services-lifecycle-tracker/frontend/src/{App.tsx,api.ts,tag-filter.ts,components/TagFilterModal.tsx,components/RefreshSteps.tsx,pages/MyResources.tsx}`,
  `operations-automation/ai-lambda-runtime-migration/docs/v2-lessons-and-v1.1-plan.md`,
  `.kiro/steering/demo-lab-ui-guide.md`.
