"""Strands @tool wrappers around the deterministic implementations.

Thin shells only: script tools import from builder/ (88 deterministic tests);
DLT tools carry the payload contract verified against a live deployment
(2026-08-09). Every tool returns JSON — structured output, never prose.
"""

from tools.script_tools import (
    build_jmx,
    build_k6_script,
    build_locust_script,
    parse_spec_input,
    save_generated_script,
    select_targets,
    validate_script,
    validate_spec,
)
from tools.dlt_tools import (
    cancel_test,
    create_scenario,
    discover_dlt_config,
    fetch_results,
    poll_test_status,
    run_scenario,
    upload_script,
)

ALL_TOOLS = [
    # script pipeline (T1–T5)
    parse_spec_input,
    select_targets,
    validate_spec,
    build_jmx,
    build_k6_script,
    build_locust_script,
    validate_script,
    save_generated_script,
    # DLT (T7–T12)
    discover_dlt_config,
    upload_script,
    create_scenario,
    run_scenario,
    poll_test_status,
    cancel_test,
    fetch_results,
]
