"""Debug script to find what triggers check_tmp_only_writes failure."""
import re
import sys

sys.path.insert(0, ".")
from scripts.tcp_traceroute_script import TCP_TRACEROUTE_SCRIPT
from scripts.tls_traceroute_script import TLS_TRACEROUTE_SCRIPT
from scripts.dns_resolve_script import DNS_RESOLVE_SCRIPT


def check_script(name, script):
    print(f"\n=== Checking {name} ===")
    lines = script.split("\n")
    found = False
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        # Check for open() calls with write modes to non-/tmp paths
        write_opens = re.findall(
            r'open\s*\(\s*["\'](/[^"\']+)["\'].*["\'][wax]',
            stripped,
        )
        for path in write_opens:
            if not path.startswith("/tmp"):
                print(f"  Line {i}: OPEN WRITE to non-/tmp: {path}")
                print(f"    Content: {stripped[:120]}")
                found = True
        # Check for shell redirections to non-/tmp paths
        redirections = re.findall(r">{1,2}\s*(/\S+)", stripped)
        for path in redirections:
            if not path.startswith("/tmp"):
                print(f"  Line {i}: REDIRECT to non-/tmp: {path}")
                print(f"    Content: {stripped[:120]}")
                found = True
    if not found:
        print("  No issues found!")


check_script("tcp_traceroute", TCP_TRACEROUTE_SCRIPT)
check_script("tls_traceroute", TLS_TRACEROUTE_SCRIPT)
check_script("dns_resolve", DNS_RESOLVE_SCRIPT)
