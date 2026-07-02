"""
DNS Resolve SSM Script Template for G.O.A.T. Network Diagnostics.

This module provides the DNS_RESOLVE_SCRIPT constant — a bash wrapper
around a self-contained Python script that performs DNS resolution from
the instance perspective and identifies the DNS resolver being used.

The script uses only Python stdlib modules (socket, subprocess, json,
sys, os, re, time) and is compatible with Python 3.6+.

The template accepts the following format parameters:
- {hostname}: The hostname to resolve (1-253 chars)
- {record_type}: The DNS record type (A, AAAA, CNAME, MX, TXT, SRV, PTR)

Requirements covered: 10.1-10.4, 10.7, 10.9, 10.10
"""

from scripts import DNS_RESOLVE_MARKER

DNS_RESOLVE_SCRIPT = '''#!/bin/bash
# GOAT Network Diagnostics - DNS Resolve
# This script is injected via SSM RunShellScript and executes entirely in /tmp.
# EXIT trap ensures cleanup regardless of success or failure.

trap "rm -f /tmp/_goat_dns_resolve_$$.py" EXIT

# Detect Python interpreter: try python3 first, fall back to python
PYTHON_BIN=""
if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
else
    echo "''' + DNS_RESOLVE_MARKER + '''"
    echo '{{"error": true, "error_type": "python_not_found", "message": "Python 3 is required but neither python3 nor python was found on PATH."}}'
    exit 0
fi

cat > /tmp/_goat_dns_resolve_$$.py << 'GOAT_PYTHON_SCRIPT_EOF'
import json
import os
import re
import socket
import subprocess
import sys
import time


MARKER = "''' + DNS_RESOLVE_MARKER + '''"
HOSTNAME = "{hostname}"
RECORD_TYPE = "{record_type}"


def get_resolver_address():
    """Identify the DNS resolver address used by this instance.

    Reads /etc/resolv.conf to find nameserver entries.
    Returns a dict with resolver_address, resolver_type, and all_nameservers.
    """
    resolver_address = None
    resolver_type = "unknown"
    nameservers = []

    try:
        with open("/etc/resolv.conf", "r") as f:
            for line in f:
                line = line.strip()
                if line.startswith("nameserver"):
                    parts = line.split()
                    if len(parts) >= 2:
                        nameservers.append(parts[1])
    except (IOError, OSError):
        pass

    if nameservers:
        resolver_address = nameservers[0]
        # Detect VPC resolver (x.x.x.2 pattern - the VPC DNS resolver
        # is always at the VPC CIDR base + 2)
        if resolver_address.endswith(".2"):
            resolver_type = "vpc_resolver"
        elif resolver_address == "169.254.169.253":
            resolver_type = "vpc_resolver"
        elif resolver_address == "127.0.0.53":
            # systemd-resolved - still uses VPC resolver upstream typically
            resolver_type = "systemd_resolved"
        else:
            # Could be a custom DNS from DHCP option set or Route53 Resolver
            resolver_type = "custom"

    return {{
        "resolver_address": resolver_address,
        "resolver_type": resolver_type,
        "all_nameservers": nameservers,
    }}


def resolve_a_record(hostname):
    """Resolve A record using socket.getaddrinfo (works on all Python 3.6+)."""
    try:
        results = socket.getaddrinfo(hostname, None, socket.AF_INET, socket.SOCK_STREAM)
        addresses = list(set(addr[4][0] for addr in results))
        return {{"success": True, "addresses": sorted(addresses)}}
    except socket.gaierror as e:
        return {{"success": False, "error": str(e)}}
    except Exception as e:
        return {{"success": False, "error": str(e)}}


def resolve_aaaa_record(hostname):
    """Resolve AAAA record using socket.getaddrinfo."""
    try:
        results = socket.getaddrinfo(hostname, None, socket.AF_INET6, socket.SOCK_STREAM)
        addresses = list(set(addr[4][0] for addr in results))
        return {{"success": True, "addresses": sorted(addresses)}}
    except socket.gaierror as e:
        return {{"success": False, "error": str(e)}}
    except Exception as e:
        return {{"success": False, "error": str(e)}}


def resolve_via_nslookup(hostname, record_type):
    """Resolve non-A/AAAA records using nslookup as fallback.

    Uses subprocess to call nslookup with the appropriate query type.
    Parses the output to extract records.
    """
    try:
        cmd = ["nslookup", "-type=" + record_type, hostname]
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
        )
        stdout, stderr = proc.communicate(timeout=10)

        if proc.returncode != 0 and not stdout:
            return {{"success": False, "error": stderr.strip()[:500] if stderr else "nslookup failed"}}

        records = parse_nslookup_output(stdout, record_type)
        if records:
            return {{"success": True, "records": records}}
        else:
            # Try to detect NXDOMAIN or no answer
            if "NXDOMAIN" in stdout or "can't find" in stdout.lower():
                return {{"success": False, "error": "NXDOMAIN - hostname not found"}}
            return {{"success": True, "records": []}}

    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except Exception:
            pass
        return {{"success": False, "error": "nslookup timed out after 10 seconds"}}
    except FileNotFoundError:
        return None  # nslookup not available, try dig
    except Exception as e:
        return {{"success": False, "error": str(e)[:500]}}


def resolve_via_dig(hostname, record_type):
    """Resolve records using dig as a second fallback.

    Uses subprocess to call dig with the appropriate query type.
    """
    try:
        cmd = ["dig", "+short", record_type, hostname]
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
        )
        stdout, stderr = proc.communicate(timeout=10)

        if proc.returncode != 0:
            return {{"success": False, "error": stderr.strip()[:500] if stderr else "dig failed"}}

        lines = [l.strip() for l in stdout.strip().split("\\n") if l.strip()]
        if not lines:
            return {{"success": True, "records": []}}
        return {{"success": True, "records": lines}}

    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except Exception:
            pass
        return {{"success": False, "error": "dig timed out after 10 seconds"}}
    except FileNotFoundError:
        return None  # dig not available
    except Exception as e:
        return {{"success": False, "error": str(e)[:500]}}


def parse_nslookup_output(output, record_type):
    """Parse nslookup output to extract DNS records."""
    records = []
    lines = output.split("\\n")
    in_answer = False

    for line in lines:
        line = line.strip()

        # Skip server info section
        if line.startswith("Server:") or line.startswith("Address:"):
            if not in_answer:
                continue

        # Detect start of answer section
        if "answer:" in line.lower() or "name:" in line.lower():
            in_answer = True

        if record_type == "CNAME":
            match = re.search(r"canonical name\\s*=\\s*(.+)", line, re.IGNORECASE)
            if match:
                records.append(match.group(1).strip().rstrip("."))
        elif record_type == "MX":
            match = re.search(r"mail exchanger\\s*=\\s*(\\d+)\\s+(.+)", line, re.IGNORECASE)
            if match:
                records.append(match.group(1) + " " + match.group(2).strip().rstrip("."))
            else:
                match = re.search(r"MX\\s+preference\\s*=\\s*(\\d+).*mail\\s+exchanger\\s*=\\s*(.+)", line, re.IGNORECASE)
                if match:
                    records.append(match.group(1) + " " + match.group(2).strip().rstrip("."))
        elif record_type == "TXT":
            match = re.search(r'text\\s*=\\s*"(.+)"', line, re.IGNORECASE)
            if match:
                records.append(match.group(1))
            else:
                match = re.search(r'TXT\\s+"(.+)"', line)
                if match:
                    records.append(match.group(1))
        elif record_type == "SRV":
            match = re.search(r"service\\s*=.*?(\\d+)\\s+(\\d+)\\s+(\\d+)\\s+(.+)", line, re.IGNORECASE)
            if match:
                records.append(match.group(1) + " " + match.group(2) + " " + match.group(3) + " " + match.group(4).strip().rstrip("."))
        elif record_type == "PTR":
            match = re.search(r"name\\s*=\\s*(.+)", line, re.IGNORECASE)
            if match:
                records.append(match.group(1).strip().rstrip("."))
        elif record_type in ("A", "AAAA"):
            # For A/AAAA via nslookup
            match = re.search(r"[Aa]ddress:\\s*([^\\s#]+)", line)
            if match and in_answer:
                addr = match.group(1).strip()
                if addr and not addr.startswith("127."):
                    records.append(addr)

    return records


def resolve_record(hostname, record_type):
    """Main resolution logic. Uses socket for A/AAAA, subprocess for others."""
    if record_type == "A":
        return resolve_a_record(hostname)
    elif record_type == "AAAA":
        return resolve_aaaa_record(hostname)
    else:
        # For CNAME, MX, TXT, SRV, PTR - use nslookup/dig
        result = resolve_via_nslookup(hostname, record_type)
        if result is not None:
            return result
        # nslookup not available, try dig
        result = resolve_via_dig(hostname, record_type)
        if result is not None:
            return result
        return {{"success": False, "error": "Neither nslookup nor dig available on this instance"}}


def main():
    """Execute DNS resolution and output results."""
    start_time = time.time()
    hostname = HOSTNAME
    record_type = RECORD_TYPE

    # Get resolver info
    resolver_info = get_resolver_address()

    # Perform resolution
    resolution_result = resolve_record(hostname, record_type)

    elapsed_ms = int((time.time() - start_time) * 1000)

    # Build result
    result = {{
        "hostname": hostname,
        "record_type": record_type,
        "resolver": resolver_info,
        "resolution": resolution_result,
        "resolution_time_ms": elapsed_ms,
    }}

    # Output marker + JSON
    print(MARKER)
    print(json.dumps(result))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(MARKER)
        print(json.dumps({{
            "hostname": HOSTNAME,
            "record_type": RECORD_TYPE,
            "resolution": {{"success": False, "error": str(e)[:500]}},
            "resolver": {{"resolver_address": None, "resolver_type": "unknown", "all_nameservers": []}},
            "resolution_time_ms": 0,
        }}))
GOAT_PYTHON_SCRIPT_EOF

$PYTHON_BIN /tmp/_goat_dns_resolve_$$.py
'''
