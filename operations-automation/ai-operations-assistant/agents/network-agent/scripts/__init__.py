"""
SSM script templates for GOAT Network Diagnostics.

This package contains string constants for SSM script templates that are
injected onto EC2 instances via SSM Run Command. Each script template is
a self-contained bash wrapper around a Python script that uses only stdlib
modules.

The marker constants below are used to identify the beginning of the JSON
output produced by each diagnostic script.
"""

# Marker lines that precede JSON output in each script's stdout.
# The SSM executor parses everything after the marker line as a JSON object.

TCP_TRACEROUTE_MARKER = "===GOAT_DIAGNOSTIC_RESULT_TCP_TRACEROUTE==="
TLS_TRACEROUTE_MARKER = "===GOAT_DIAGNOSTIC_RESULT_TLS_TRACEROUTE==="
DNS_RESOLVE_MARKER = "===GOAT_DIAGNOSTIC_RESULT_DNS_RESOLVE==="
DB_CONNECTIVITY_PROBE_MARKER = "===GOAT_DIAGNOSTIC_RESULT_DB_CONNECTIVITY_PROBE==="
