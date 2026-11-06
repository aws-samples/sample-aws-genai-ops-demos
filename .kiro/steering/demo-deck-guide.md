---
inclusion: auto
name: demo-deck-guide
description: Use when asked to create, update or review the presentation deck of a demo in this repository. Covers the shared deck template (title, slots, fixed slides), where each slide's content comes from in the demo's README, what to do when a piece is missing, and the clone, fill, review workflow on the team's presentation platform MCP.
---

# Demo Deck Guide

Every demo in this repository gets one presentation deck, produced from one shared
template so the whole library reads the same way. The deck is a sales and enablement
asset: it tells a customer-facing audience what problem the demo solves, how it works
and how to try it, in eight slides.

Decks live on the team's presentation platform, in the **GenAI Ops Library** section.
They are authored through its MCP server, never by hand-editing exported HTML.

## The template

- **Title:** `GenAI Ops demo template` (slug `genai-ops-demo-template`), in the GenAI Ops Library section.
- **Locate it** with the platform's `search` tool by title. Never hardcode an owner alias or a URL for it, in a deck, a script or a steering file.
- **Fixed slides** (title and closer) belong to the template. Never edit, move or delete them on a copy.
- **Slots** are the per-demo slides. Fill every slot in one call. A copy with an unfilled slot cannot be exported, so never leave one "for later".

### Slide contract

| # | Slide | Slot id | Source in the demo |
|---|---|---|---|
| 1 | Title: demo name and one-line tagline | fixed, variables `demo-name`, `tagline`, `pillar` | README `#` heading and the italic line under it |
| 2 | The problem it solves | `problem` | README Overview, first paragraph |
| 3 | How it works, three steps | `how-it-works` | README Overview and the pipeline or workflow section |
| 4 | Architecture diagram | `architecture` | The architecture image linked in the README Architecture section |
| 5 | What the audience sees | `walkthrough` | The Storylane link in the README, plus the "Use the Dashboard" or equivalent steps |
| 6 | At a glance: deploy time, cost, key technologies | `at-a-glance` | README At a Glance and Cost Estimate |
| 7 | Try it: deploy command, prerequisites, repository path | `try-it` | README Quick Start and Prerequisites |
| 8 | Thanks and repository | fixed | none |

Title variables: `demo-name` is the README `#` heading, `tagline` the italic line
under it, `pillar` one of Operations automation, Security, Cost optimization,
Observability, Resilience.

## Content rules

- **The README is the source.** Every fact, number and step on a slide comes from the
  demo's README or ARCHITECTURE.md. Do not add claims, numbers or capabilities that are
  not written there.
- **Missing pieces are not invented.** If the README has no architecture image, or no
  Storylane link, fill that slot with the standard placeholder slide (see "Placeholder
  slide"). Never draw a diagram, describe one from the code, or substitute a different
  walkthrough. Then report the gap to the demo owner; fixing it is their task.
- **Architecture image:** upload the image the README links as a deck asset, as PNG. The
  platform refuses SVG files that carry embedded data URLs, which every draw.io SVG
  export does. When the README links one, export the `.drawio` source next to it to PNG
  and upload that. Never redraw the diagram with the platform's diagram tools.
- **Word budget:** 10 to 40 audience-visible words per slide. Headlines state a
  conclusion, not a topic. Detail goes in the speaker notes.
- **Speaker notes carry the receipts.** Each number's source is named in the notes as
  the README section it comes from, for example "README, Cost Estimate".
- **Costs** are quoted as the README states them, with its qualifier (per 1,000
  functions, per month, per run). Never round into a stronger claim.
- **AWS names** are validated with `validate_aws_offering_names` in one call before the
  fill. Pass the copy you will write as `text`; the tool takes free text, not a list of
  names. Fix `wrong-prefix` findings; leave `advisory` ones unless the author confirms.
  Wrap repository paths, branch names and other identifiers in `<code>`, otherwise the
  check reads `ai-lambda-runtime-migration` as a mis-capitalised service name.
- **Writing rules** from the contributor guide apply to slide copy: sentence case, no
  exclamation marks, no "click", no emoji, no "we" for the product.

## Placeholder slide

Used when a slot's source is absent from the README. Same layout for every demo so the
gap is visible and consistent:

- Kicker: the slide's name (Architecture, or What the audience sees).
- Headline: `Not yet provided by the demo owner`.
- One line naming what is expected: `An architecture image linked in the README` or
  `A Storylane walkthrough linked in the README`.
- Speaker notes: the demo path and the README section where the owner should add it.

## Naming

- Deck slug: `genai-ops-<demo-directory-name>`, for example
  `genai-ops-ai-lambda-runtime-migration`.
- Deck title: the README `#` heading.
- Section: GenAI Ops Library.

## Workflow

1. Read the demo's README and ARCHITECTURE.md. Collect the fields of the slide
   contract and note which sources are missing.
2. `validate_aws_offering_names` for every service name you will write.
3. `clone_template` with the template located by title, `new_slug` and `new_title` from
   "Naming", `section` GenAI Ops Library, and `variables` for the title slide.
4. Upload the architecture image as PNG with the asset tool when the README links one
   (see "Architecture image" under Content rules for draw.io SVG files).
5. `fill_template_slots` once, with all six slots. Each fill is a complete
   `<section class="slide ...">` with `data-slide-id`, visible copy within budget and a
   `.speaker-notes` block.
6. `review_deck` once. Poll until ready. Apply findings about the slides you filled.
   Findings about the fixed slides go to the template owner, not into the copy.
7. Report: deck title and slug, the slots filled from the README, the slots filled with
   a placeholder and what the owner must add.

## Maintaining the template

Only the template owner changes the template, and only when its structure must change.

- The title slide is locked with variables and the closer is fully locked. Slides 2 to 7
  carry the slot briefs as visible text; that text is what every clone receives, so
  write briefs as instructions with bracketed placeholders, never as finished-looking
  content.
- A whole-deck publish (`put_deck_html`) keeps slots but **drops template variables**.
  After any whole-deck publish, re-run `mark_template_variable` for `demo-name`,
  `tagline` and `pillar`, then re-apply the slide locks with `set_slide_lock`. Verify
  with `get_template_slides`: three variables, slide 1 `variables`, slide 8 `locked`.
- Review the template with `review_deck` after a change. It reviews the briefs for
  reuse; advice listed under `templateContractSetAside` must not be applied.

## Scope

- One deck per demo directory that has a README, except demos whose README title says
  "work in progress".
- Demos exempt from deployment (`.no-deploy`) still get a deck; the Try it slide shows
  the install or run command the README gives instead of a deploy command.
- Never add a team slide to a library deck.
