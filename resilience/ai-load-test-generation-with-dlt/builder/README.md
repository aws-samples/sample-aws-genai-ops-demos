# JMX Builder — spec-driven, not model-written

The LLM writes a **TestSpec JSON**. This builder turns it into a JMeter test plan
by concatenating pre-verified XML fragments. No JMeter property name is ever
produced by a language model.

## Why

JMeter ignores unknown and misspelled properties without any error. A test plan
can be perfectly well-formed XML, load in the GUI, run to completion, and measure
nothing. Every defect below was found in real files — three in hand-authored
production JMX, two in an LLM prompt, two in earlier hand-written examples here:

| Defect | Consequence | Caught by |
|---|---|---|
| `UniformRandomTimer` using `ConstantDelayOffset` / `RandomDelay` | zero think time | run + timing |
| `ThroughputController` without the `ThroughputController.` prefix | 1 execution instead of 60 | run + sample counts |
| Assertion on `response_code` with `test_type=16` (SUBSTRING) matching `"2"` | HTTP 200 with an error body counts as success | `validate_run.py` |
| `RegexExtractor` with an empty `template` | variable resolves to empty string | `validate_run.py` |
| Sampler with no domain and no HTTP Request Defaults | every request fails at connect | `validate_run.py` |
| `JSONPathAssertion` expected value `0000` | compared as the number 0; never matches | `json_literal()` + tests |
| `BackendListener` pointing at a private IP | unreachable from a DLT container; the listener becomes the bottleneck | builder never emits one |
| Absolute or nested CSV path | file absent inside the container | spec validation |
| Body check using `test_type=2` (CONTAINS) with a literal JSON marker | `MalformedCachePatternException`; sample fails as "Bad test configuration" and asserts nothing | run against the live concert API |

`xmllint` catches **none** of these. All the broken files above pass it.

## Usage

```bash
# validate the spec and print the load plan, writing nothing
python3 jmx_builder.py sample-data/orders.spec.json --check

# build
python3 jmx_builder.py sample-data/orders.spec.json -o out.jmx

# smoke-run it for real and assert on the JTL
python3 validate_run.py out.jmx
```

`validate_run.py` rewrites the plan to 1 thread / 1 iteration, runs JMeter
against the actual target, and fails unless every request succeeded and every
correlation variable resolved to a real value. It must pass before any load run.

## Tests

```bash
python3 tests/test_builder.py   # 52 checks, includes real JMeter runs
python3 tests/test_taurus.py    # load-ratio check; needs `pip install bzt`
```

`tests/mock_target.py` is a target that returns HTTP 200 with error envelopes and
a null token on bad credentials — the failure modes a status-code-only assertion
cannot see.

## Verified facts

Established by running JMeter 5.6.3 and Taurus 1.16.51 locally, not from docs.

**Property names** — confirmed against class constants via `javap -p -constants`
and by observing behaviour:

- Think time: `ConstantTimer.delay` + `RandomTimer.range` on `UniformRandomTimer`
- Throughput share: `ThroughputController.style` / `.perThread` /
  `.maxThroughput` plus `percentThroughput` as a `<FloatProperty>` element
- Assertion types: `1`=MATCH (full regex), `2`=CONTAINS (**partial regex**),
  `4`=NOT, `8`=EQUALS (full literal), `16`=SUBSTRING (partial literal), `32`=OR.
  Status checks use EQUALS (8); body checks use SUBSTRING (16) and NOT|SUBSTRING
  (20). **CONTAINS is a regex**, so a literal marker like `"content":[]` throws
  `MalformedCachePatternException` at run time and the sample fails as "Bad test
  configuration" without asserting anything
- `JSONPathAssertion` uses bare keys: `JSON_PATH`, `EXPECTED_VALUE`,
  `JSONVALIDATION`, `EXPECT_NULL`, `INVERT`, `ISREGEX`
- `JSONPostProcessor` uses prefixed keys: `JSONPostProcessor.referenceNames`,
  `.jsonPathExprs`, `.match_numbers`, `.defaultValues`

**JSONPath comparison.** With `ISREGEX=false` the assertion runs
`JSONValue.parse()` on the expected value and compares with `Objects.equals`. So
`0000` parses to the number `0` and never equals the string `"0000"` — producing
the unreadable message *"expected to be '0000', but found '0000'"*. The builder
quotes non-scalar values via a strict JSON round-trip, so zero-padded codes stay
strings while genuine numbers pass through.

**HTTP Request Defaults are inherited.** A sampler with only a path resolves the
host from `ConfigTestElement`/`HttpDefaultsGui`, so the domain is written once.

**Transaction Controller excludes think time.** With
`includeTimers=false`, a 2000 ms timer inside the controller left the transaction
elapsed time at 20 ms.

**Taurus redistributes concurrency by thread-group size** — the claim the whole
load-ratio design depends on. Thread groups of 5 and 1 threads (3:1 weights),
run under `concurrency: 100`, were rewritten to `ConcurrencyThreadGroup` with
`TargetLevel` **83** and **17**, and the observed request mix was 83.1% / 16.9%.
So per-endpoint load share belongs in `ThreadGroup.num_threads`; a
`ThroughputController` inside a single thread group is invisible to Taurus.

Two consequences for DLT:

- `ramp-up` and `hold-for` come from the Taurus YAML and **override** the
  scheduler values in the JMX. The builder still writes them so the plan is
  runnable standalone.
- Setup is emitted as a Once Only Controller **inside every thread group**, so
  each VU authenticates itself. Verified: 100 VUs produced 100 logins, not 1 and
  not one per iteration.

## Design rules

1. **The spec is the only model-authored artifact.** Adding a JMeter feature
   means adding a fragment plus a test, never loosening the spec: add the
   fragment, wire it into `jmx_builder.py`, extend `spec_schema.json`, and add a
   case to `tests/test_builder.py` that fails without it. If the spec cannot
   express what you need, that is the route — not inline XML.
2. **Refuse rather than emit something broken.** A rejected spec is a fixable
   error message; a silently wrong JMX is a wrong load-test report.
3. **A body check is mandatory.** A status code alone cannot detect an API that
   returns HTTP 200 with an error envelope, which is the common convention in the
   APIs this targets. Waiving it is for 204 and HEAD only, and requires
   `body_check_waived` **and** a written reason. `body_contains` is matched as a
   literal, so it has to match the exact serialization — `"resultCode": "0000"`
   with a space will not match `"resultCode":"0000"`. When the field's position
   matters, prefer `json_path_equals`, which is structural.
4. **Extractor defaults are never empty.** An empty default makes a failed
   correlation indistinguishable from success.
5. **Prod writes require a human.** `POST`/`PUT`/`PATCH`/`DELETE` against
   `environment: prod` is rejected unless `allow_prod_writes` is set by a person.
6. **No listeners.** DLT collects results itself; a `BackendListener` aimed at an
   unreachable host throttles the load generator.
