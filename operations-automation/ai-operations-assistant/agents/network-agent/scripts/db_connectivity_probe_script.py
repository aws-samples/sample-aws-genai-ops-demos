"""
DB Connectivity Probe SSM Script Template for G.O.A.T. Network Diagnostics.

This module provides the DB_CONNECTIVITY_PROBE_SCRIPT constant — a bash wrapper
around a self-contained Python script that performs three sequential phases:
TCP connect, TLS handshake, and protocol authentication.

The script uses only Python stdlib modules (socket, ssl, struct, json, time,
sys, os) and is compatible with Python 3.6+.

The template accepts the following format parameters:
- {endpoint}: Target database hostname or IPv4 address
- {port}: Database port (1-65535)
- {engine}: Database engine type ("mysql", "postgresql", or empty/null)

Requirements covered: 11.1-11.11
"""

from scripts import DB_CONNECTIVITY_PROBE_MARKER

DB_CONNECTIVITY_PROBE_SCRIPT = '''#!/bin/bash
# GOAT Network Diagnostics - DB Connectivity Probe
# This script is injected via SSM RunShellScript and executes entirely in /tmp.
# EXIT trap ensures cleanup regardless of success or failure.

trap "rm -f /tmp/_goat_db_connectivity_probe_$$.py /tmp/_goat_cmd_check_$$" EXIT

# Detect Python interpreter: try python3 first, fall back to python
PYTHON_BIN=""
if command -v python3 > /tmp/_goat_cmd_check_$$ 2>&1; then
    PYTHON_BIN="python3"
elif command -v python > /tmp/_goat_cmd_check_$$ 2>&1; then
    PYTHON_BIN="python"
else
    echo "''' + DB_CONNECTIVITY_PROBE_MARKER + '''"
    echo '{{"error": true, "error_type": "python_not_found", "message": "Python 3 is required but neither python3 nor python was found on PATH."}}'
    exit 0
fi

cat > /tmp/_goat_db_connectivity_probe_$$.py << 'GOAT_PYTHON_SCRIPT_EOF'
import socket
import ssl
import struct
import json
import time
import sys
import os

MARKER = "''' + DB_CONNECTIVITY_PROBE_MARKER + '''"
ENDPOINT = "{endpoint}"
PORT = {port}
ENGINE = "{engine}"

TCP_TIMEOUT = 10


def run_tcp_phase():
    """Phase 1: TCP connect to endpoint:port with timeout."""
    result = {{"connected": False, "connect_time_ms": None, "error": None}}
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(TCP_TIMEOUT)
        start = time.time()
        sock.connect((ENDPOINT, PORT))
        elapsed = (time.time() - start) * 1000
        result["connected"] = True
        result["connect_time_ms"] = round(elapsed, 2)
        return result, sock
    except Exception as e:
        result["error"] = str(e)[:512]
        return result, None


def run_tls_phase(sock):
    """Phase 2: TLS handshake using ssl with SNI."""
    result = {{"connected": False, "tls_version": None, "error": None}}
    try:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        tls_sock = context.wrap_socket(sock, server_hostname=ENDPOINT)
        version = tls_sock.version()
        result["connected"] = True
        result["tls_version"] = version
        return result, tls_sock
    except Exception as e:
        result["error"] = str(e)[:512]
        return result, None


def run_auth_phase_mysql(tls_sock):
    """Phase 3 (MySQL): Read initial handshake packet."""
    result = {{"success": False, "details": {{}}, "error": None}}
    try:
        tls_sock.settimeout(5)
        # Read packet header: 3 bytes length + 1 byte sequence
        header = b""
        while len(header) < 4:
            chunk = tls_sock.recv(4 - len(header))
            if not chunk:
                result["error"] = "Connection closed before handshake"
                return result
            header += chunk

        payload_length = struct.unpack("<I", header[:3] + b"\\x00")[0]
        seq_id = header[3]

        # Read the payload
        payload = b""
        while len(payload) < payload_length:
            chunk = tls_sock.recv(payload_length - len(payload))
            if not chunk:
                break
            payload += chunk

        if len(payload) < 2:
            result["error"] = "Handshake payload too short"
            return result

        protocol_version = payload[0]
        # Server version is a null-terminated string starting at byte 1
        null_pos = payload.find(b"\\x00", 1)
        if null_pos == -1:
            server_version = "unknown"
        else:
            server_version = payload[1:null_pos].decode("ascii", errors="replace")

        result["success"] = True
        result["details"] = {{
            "protocol_version": protocol_version,
            "server_version": server_version,
        }}
        return result
    except Exception as e:
        result["error"] = str(e)[:512]
        return result


def run_auth_phase_postgresql(tls_sock):
    """Phase 3 (PostgreSQL): Send StartupMessage and read response."""
    result = {{"success": False, "details": {{}}, "error": None}}
    try:
        tls_sock.settimeout(5)
        # Build StartupMessage: version 3.0, user=goat_probe
        # Format: int32 length, int32 version(196608=3.0), "user\\0" "goat_probe\\0" "\\0"
        user_param = b"user\\x00goat_probe\\x00\\x00"
        version = struct.pack("!I", 196608)  # 3.0
        msg_body = version + user_param
        msg_length = struct.pack("!I", len(msg_body) + 4)
        startup_msg = msg_length + msg_body

        tls_sock.sendall(startup_msg)

        # Read response: first byte indicates message type
        resp = b""
        while len(resp) < 1:
            chunk = tls_sock.recv(1)
            if not chunk:
                result["error"] = "Connection closed before response"
                return result
            resp += chunk

        msg_type = chr(resp[0])
        auth_type = "unknown"
        if msg_type == "R":
            auth_type = "auth_request"
        elif msg_type == "E":
            auth_type = "error"
        else:
            auth_type = "other_{{}}".format(msg_type)

        result["success"] = msg_type == "R"
        result["details"] = {{
            "auth_type": auth_type,
        }}
        return result
    except Exception as e:
        result["error"] = str(e)[:512]
        return result


def determine_verdict(tcp_result, tls_result, auth_result, engine):
    """Determine overall verdict based on phase results."""
    if not tcp_result["connected"]:
        return "tcp_failed"
    if tls_result is None:
        return "tls_and_auth_skipped"
    if not tls_result["connected"]:
        return "tls_failed"
    if auth_result is None:
        return "tls_and_auth_skipped"
    if not auth_result["success"]:
        return "auth_failed"
    return "all_phases_passed"


def run_probe():
    """Execute the DB connectivity probe and return structured results."""
    engine = ENGINE if ENGINE and ENGINE.lower() not in ("", "null", "none") else None

    # Phase 1: TCP
    tcp_result, sock = run_tcp_phase()

    tls_result = None
    auth_result = None

    # Phase 2: TLS (skipped if TCP fails)
    if tcp_result["connected"] and sock is not None:
        tls_result, tls_sock = run_tls_phase(sock)

        # Phase 3: Auth (skipped if TLS fails or engine is empty/null)
        if tls_result["connected"] and tls_sock is not None and engine:
            if engine.lower() == "mysql":
                auth_result = run_auth_phase_mysql(tls_sock)
            elif engine.lower() == "postgresql":
                auth_result = run_auth_phase_postgresql(tls_sock)

            # Close TLS socket
            try:
                tls_sock.close()
            except Exception:
                pass
        else:
            # Close TLS socket if auth skipped
            if tls_sock is not None:
                try:
                    tls_sock.close()
                except Exception:
                    pass
    else:
        # Close TCP socket if TLS was skipped
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass

    verdict = determine_verdict(tcp_result, tls_result, auth_result, engine)

    result = {{
        "endpoint": ENDPOINT,
        "port": PORT,
        "engine": engine,
        "tcp": tcp_result,
        "tls": tls_result,
        "auth": auth_result,
        "overall_verdict": verdict,
    }}

    return result


if __name__ == "__main__":
    try:
        result = run_probe()
    except Exception as e:
        result = {{
            "error": True,
            "error_type": "unexpected_error",
            "message": str(e)[:1024],
        }}

    print(MARKER)
    print(json.dumps(result))
GOAT_PYTHON_SCRIPT_EOF

# Execute the Python script
"$PYTHON_BIN" /tmp/_goat_db_connectivity_probe_$$.py
'''
