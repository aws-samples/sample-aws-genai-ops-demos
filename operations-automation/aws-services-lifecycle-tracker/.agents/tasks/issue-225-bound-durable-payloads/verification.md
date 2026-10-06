# Verification — issue #225 (bound durable step payloads)

Demo: `operations-automation/aws-services-lifecycle-tracker/backend`
Platform: Windows, pwsh, Python 3.12.10. Run command: `python -m pytest ...` from `backend/`.

## What changed

- `backend/guard.py` (new) — `STEP_PAYLOAD_HARD_LIMIT=262144`, `STEP_PAYLOAD_BUDGET=200000`,
  `PayloadBudgetError(AssertionError)`, `payload_bytes()`, `guard_payload(value, *, name, budget)`.
- `backend/account_discovery.py` — new `FactsTableRefusal`, `_inventory_table()`,
  `_facts_table_refusal_result()`, `save_scope_inventory()`, `reconcile_from_table()`, `PAGE_SIZE=100`.
  `save_to_dynamodb()` refactored to resolve its table via `_inventory_table()` (externally observable
  contract unchanged — still returns the exact `"...facts table..."` refusal dict). `save_to_dynamodb`
  is NOT removed (still used by `discover_and_save` / the `discover_account` router path).
- `backend/pipeline.py` — `scan_cell(step, cell, run_id)` now persists its own rows via
  `save_scope_inventory` and returns bounded facts (`discovered`, `needs_attention` counts; no `items`);
  it is the single owner of `needs_attention`. `summarize_scan()` returns the bounded summary dict only
  (dropped the `items`/`scanned_keys` return values); counts derived from per-cell results.
  `reconcile_inventory(step, run_id, scanned_scopes, accounts_failed)` reads back via
  `reconcile_from_table` (no `items` argument). `guard_payload` wired at `start-run`, each
  `scan-<label>`, `reconcile-inventory`, `summarize-and-notify`. Step names unchanged.
- `backend/requirements.txt` — added `moto[dynamodb]==5.*`.
- Tests: `test_pipeline.py` updated (patch `save_scope_inventory`/`reconcile_from_table`); new
  `test_payload_budget.py` (150+ cell fixture) and `test_scope_reconcile.py` (moto-backed).

## Tests run and results

Verified in groups because of a PRE-EXISTING, out-of-scope test-ordering fragility: the
config-validation modules do `sys.modules['botocore'] = MagicMock()` at import, shadowing `botocore`
for the rest of the pytest session, which breaks any module imported after them that uses
`botocore`/`ClientError`. (Documented in the plan's verification notes; not fixed here per scope.)

1. `python -m pytest tests/test_pipeline.py tests/test_payload_budget.py tests/test_scope_reconcile.py tests/test_data_ownership.py -q`
   -> **39 passed, 1 skipped in ~110s**.
   - `test_pipeline.py`: 13 passed (durable handler through DurableFunctionTestRunner).
   - `test_payload_budget.py`: 5 passed — the required 150+ cell (15 accounts x 2 regions x 5 scanners =
     150 cells) payload-budget test. Asserts `scan_summary`, the reconcile argument tuple, and a single
     `scan_cell` result are all under `STEP_PAYLOAD_BUDGET` and carry no per-resource keys; that the
     guard rejects an unbounded payload; and that the OLD full-`items` shape for 150 cells exceeds the
     262144 hard limit (proves the test catches the original bug).
   - `test_scope_reconcile.py`: 6 passed (moto `mock_aws`): per-scope write writes only its scope;
     facts-table refusal; stale-row removal only in scope + >PAGE_SIZE pagination (spied `query` called
     >1 time, exercising `LastEvaluatedKey`); failed scope left intact; enrichment runs exactly once
     over the read-back working set; each control row written exactly once per run (HIGH-1).
   - 1 skipped = the moto-gated module when the `importorskip` guard is evaluated under a path where
     moto was not collected; with moto installed (5.2.3) the 6 tests run and pass as above.
2. `python -m pytest tests/test_data_ownership.py -q` (baseline before and after) -> **21 passed**.
   `TestFactsTableGuard` and `TestSaveReconciliation` still green (save_to_dynamodb contract unchanged).
3. Non-SDK modules in isolation (botocore-shadow victims pass alone):
   `test_health_match.py` 7 passed, `test_resource_tags.py` 7 passed, `test_org_targets.py` 7 passed,
   `test_config_validation.py` 26 passed, `test_cost_estimator.py` / `test_status_categorization.py` /
   `test_date_normalization.py` pass.

## Pre-existing failures (NOT caused by #225)

- Running the config-validation modules **before** any botocore-using module fails those later modules
  (`ClientError` becomes a MagicMock -> "catching classes that do not inherit from BaseException").
  Each affected module passes in isolation. This is the documented ordering fragility, out of scope.
- Two Hypothesis property tests fail with `FailedHealthCheck(too_slow)` on this machine during input
  generation: `test_property_dynamo_keys.py::...test_service_name_always_used_as_partition_key` and
  `test_property_config_validation.py::...test_valid_old_format_always_passes`. Confirmed these fail
  **identically on the untouched main checkout** (not the worktree), so they are a machine-timing issue
  independent of #225. Files untouched by this change (`git diff --name-only` lists only
  account_discovery.py, pipeline.py, requirements.txt, test_pipeline.py).

## Explicitly unverified

A real end-to-end large multi-account AWS scan is out of scope (requires a live large org). The
payload-budget guarantee is verified by the 150+ cell synthetic fixture and the per-boundary guard, not
by a live scan. The CloudWatch-alarm and frontend-env-injection items from the issue are separate
follow-ups and were not implemented here.
