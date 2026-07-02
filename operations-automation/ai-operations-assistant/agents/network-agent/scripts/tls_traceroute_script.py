"""
TLS Traceroute SSM Script Template for G.O.A.T. Network Diagnostics.

This module provides the TLS_TRACEROUTE_SCRIPT constant — a bash wrapper
around a self-contained Python script that performs TTL-based TCP SYN probing
followed by TLS handshake validation when the destination is reached.

The script uses only Python stdlib modules (socket, struct, time, select,
json, sys, ssl, os, errno) and is compatible with Python 3.6+.

The template accepts the following format parameters:
- {destination_host}: Target hostname or IPv4 address
- {destination_port}: TCP port to probe (1-65535)
- {max_hops}: Maximum TTL value (1-30)
- {probe_timeout}: Seconds to wait per hop (1-5)
- {sni_override}: SNI hostname override (empty string uses destination_host)

Requirements covered: 2.1-2.9, 3.1-3.7
"""

from scripts import TLS_TRACEROUTE_MARKER

TLS_TRACEROUTE_SCRIPT = '''#!/bin/bash
# GOAT Network Diagnostics - TLS Traceroute
# This script is injected via SSM RunShellScript and executes entirely in /tmp.
# EXIT trap ensures cleanup regardless of success or failure.

trap "rm -f /tmp/_goat_tls_traceroute_$$.py" EXIT

# Detect Python interpreter: try python3 first, fall back to python
PYTHON_BIN=""
if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
else
    echo "''' + TLS_TRACEROUTE_MARKER + '''"
    echo '{{"error": true, "error_type": "python_not_found", "message": "Python 3 is required but neither python3 nor python was found on PATH."}}'
    exit 0
fi

cat > /tmp/_goat_tls_traceroute_$$.py << 'GOAT_PYTHON_SCRIPT_EOF'
import socket
import struct
import time
import select
import json
import sys
import ssl
import os
import errno

MARKER = "''' + TLS_TRACEROUTE_MARKER + '''"
DESTINATION_HOST = "{destination_host}"
DESTINATION_PORT = {destination_port}
MAX_HOPS = {max_hops}
PROBE_TIMEOUT = {probe_timeout}
SNI_OVERRIDE = "{sni_override}"


def resolve_host(host):
    """Resolve hostname to IPv4 address."""
    try:
        return socket.gethostbyname(host)
    except socket.gaierror:
        return None


def reverse_dns(ip):
    """Attempt reverse DNS lookup with 2-second timeout."""
    old_timeout = socket.getdefaulttimeout()
    try:
        socket.setdefaulttimeout(2)
        hostname, _, _ = socket.gethostbyaddr(ip)
        return hostname
    except (socket.herror, socket.gaierror, socket.timeout, OSError):
        return None
    finally:
        socket.setdefaulttimeout(old_timeout)


def get_source_ip():
    """Get the primary private IP of this instance."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "unknown"


def build_tcp_syn(src_port, dst_ip, dst_port, seq_num):
    """Build a TCP SYN packet."""
    offset_flags = (5 << 12) | 0x002
    urgent = 0
    src_ip_packed = socket.inet_aton(get_source_ip())
    dst_ip_packed = socket.inet_aton(dst_ip)

    tcp_header = struct.pack(
        "!HHIIHHH",
        src_port, dst_port, seq_num, 0, offset_flags, 1024, 0,
    ) + struct.pack("!H", urgent)

    tcp_length = len(tcp_header)
    pseudo_header = struct.pack(
        "!4s4sBBH", src_ip_packed, dst_ip_packed, 0,
        socket.IPPROTO_TCP, tcp_length,
    )

    chksum = checksum(pseudo_header + tcp_header)

    tcp_header = struct.pack(
        "!HHIIHHH",
        src_port, dst_port, seq_num, 0, offset_flags, 1024, chksum,
    ) + struct.pack("!H", urgent)

    return tcp_header


def checksum(data):
    """Calculate Internet checksum (RFC 1071)."""
    if len(data) % 2:
        data += b"\\x00"
    s = 0
    for i in range(0, len(data), 2):
        w = (data[i] << 8) + data[i + 1]
        s += w
    s = (s >> 16) + (s & 0xFFFF)
    s += s >> 16
    return (~s) & 0xFFFF


def classify_tls_error(exc):
    """Classify a TLS exception into an error type."""
    msg = str(exc).lower()
    if "certificate verify failed" in msg or "certificate_verify_failed" in msg:
        return "certificate_verify_failed"
    if "timed out" in msg or "timeout" in msg:
        return "handshake_timeout"
    if "connection reset" in msg or "reset by peer" in msg:
        return "connection_reset"
    if "protocol" in msg or "version" in msg or "alert" in msg:
        return "protocol_error"
    return "unknown"


def perform_tls_handshake(dest_ip, dest_port, sni_hostname):
    """Perform TLS handshake and return result dict."""
    tls_result = {{}}
    ctx = ssl.create_default_context()

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(10)
    try:
        sock.connect((dest_ip, dest_port))
        handshake_start = time.time()
        tls_sock = ctx.wrap_socket(sock, server_hostname=sni_hostname)
        try:
            # Handshake happens during wrap_socket for Python 3.6+
            handshake_ms = round((time.time() - handshake_start) * 1000)

            protocol_version = tls_sock.version()
            cipher = tls_sock.cipher()
            cert = tls_sock.getpeercert()

            cipher_suite = cipher[0] if cipher else None

            subject_parts = []
            if cert and "subject" in cert:
                for rdn in cert["subject"]:
                    for attr_type, attr_value in rdn:
                        subject_parts.append("{{}}={{}}".format(attr_type, attr_value))
            subject_str = ", ".join(subject_parts) if subject_parts else None

            issuer_parts = []
            if cert and "issuer" in cert:
                for rdn in cert["issuer"]:
                    for attr_type, attr_value in rdn:
                        issuer_parts.append("{{}}={{}}".format(attr_type, attr_value))
            issuer_str = ", ".join(issuer_parts) if issuer_parts else None

            not_after = cert.get("notAfter") if cert else None

            tls_result = {{
                "handshake_success": True,
                "protocol_version": protocol_version,
                "cipher_suite": cipher_suite,
                "certificate_subject": subject_str,
                "certificate_issuer": issuer_str,
                "certificate_not_after": not_after,
                "handshake_time_ms": handshake_ms,
                "error_type": None,
                "error_detail": None,
            }}
        finally:
            tls_sock.close()
    except Exception as e:
        error_type = classify_tls_error(e)
        error_detail = str(e)[:1024]
        tls_result = {{
            "handshake_success": False,
            "protocol_version": None,
            "cipher_suite": None,
            "certificate_subject": None,
            "certificate_issuer": None,
            "certificate_not_after": None,
            "handshake_time_ms": None,
            "error_type": error_type,
            "error_detail": error_detail,
        }}
    finally:
        try:
            sock.close()
        except Exception:
            pass

    return tls_result


def run_traceroute():
    """Execute the TLS traceroute and return structured results."""
    start_time = time.time()

    dest_ip = resolve_host(DESTINATION_HOST)
    if dest_ip is None:
        return {{
            "error": False,
            "source_ip": get_source_ip(),
            "destination_host": DESTINATION_HOST,
            "destination_ip": None,
            "destination_port": DESTINATION_PORT,
            "destination_reached": False,
            "destination_status": None,
            "total_hops": 0,
            "max_hops": MAX_HOPS,
            "probe_timeout": PROBE_TIMEOUT,
            "trace_duration_ms": round((time.time() - start_time) * 1000, 2),
            "hops": [],
            "tls": None,
            "tls_skipped_reason": "dns_resolution_failed",
            "dns_resolution_failed": True,
        }}

    source_ip = get_source_ip()
    hops = []
    destination_reached = False
    destination_status = None
    src_port = 33434 + (os.getpid() % 1000)

    try:
        send_sock = socket.socket(
            socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_TCP
        )
        recv_icmp_sock = socket.socket(
            socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP
        )
        recv_tcp_sock = socket.socket(
            socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_TCP
        )
    except (PermissionError, OSError) as e:
        if hasattr(e, "errno") and e.errno == errno.EPERM:
            return {{
                "error": True,
                "error_type": "cap_net_raw_denied",
                "message": "CAP_NET_RAW capability is required.",
            }}
        if isinstance(e, PermissionError):
            return {{
                "error": True,
                "error_type": "cap_net_raw_denied",
                "message": "CAP_NET_RAW capability is required.",
            }}
        raise

    recv_icmp_sock.settimeout(PROBE_TIMEOUT)
    recv_tcp_sock.settimeout(PROBE_TIMEOUT)

    try:
        for ttl in range(1, MAX_HOPS + 1):
            send_sock.setsockopt(socket.IPPROTO_IP, socket.IP_TTL, ttl)
            seq_num = ttl * 1000
            probe_start = time.time()

            tcp_packet = build_tcp_syn(src_port, dest_ip, DESTINATION_PORT, seq_num)

            try:
                send_sock.sendto(tcp_packet, (dest_ip, DESTINATION_PORT))
            except OSError as e:
                if e.errno == errno.EPERM:
                    return {{
                        "error": True,
                        "error_type": "cap_net_raw_denied",
                        "message": "CAP_NET_RAW capability is required.",
                    }}
                raise

            hop_ip = "*"
            hop_rtt = None
            hop_hostname = None
            responded = False

            deadline = probe_start + PROBE_TIMEOUT
            while time.time() < deadline:
                remaining = deadline - time.time()
                if remaining <= 0:
                    break

                readable, _, _ = select.select(
                    [recv_icmp_sock, recv_tcp_sock], [], [], remaining
                )

                for sock in readable:
                    try:
                        data, addr = sock.recvfrom(1024)
                    except (socket.timeout, OSError):
                        continue

                    if sock is recv_icmp_sock:
                        if len(data) < 28:
                            continue
                        ip_header_len = (data[0] & 0x0F) * 4
                        icmp_data = data[ip_header_len:]
                        if len(icmp_data) < 8:
                            continue
                        icmp_type = icmp_data[0]
                        icmp_code = icmp_data[1]

                        if icmp_type == 11 and icmp_code == 0:
                            hop_ip = addr[0]
                            hop_rtt = round((time.time() - probe_start) * 1000, 2)
                            hop_hostname = reverse_dns(hop_ip)
                            responded = True
                            break
                        if icmp_type == 3:
                            hop_ip = addr[0]
                            hop_rtt = round((time.time() - probe_start) * 1000, 2)
                            hop_hostname = reverse_dns(hop_ip)
                            responded = True
                            break

                    elif sock is recv_tcp_sock:
                        if len(data) < 40:
                            continue
                        ip_header_len = (data[0] & 0x0F) * 4
                        tcp_data = data[ip_header_len:]
                        if len(tcp_data) < 14:
                            continue

                        tcp_src_port = struct.unpack("!H", tcp_data[0:2])[0]
                        tcp_dst_port = struct.unpack("!H", tcp_data[2:4])[0]
                        tcp_flags = struct.unpack("!B", tcp_data[13:14])[0]

                        if tcp_src_port != DESTINATION_PORT:
                            continue
                        if tcp_dst_port != src_port:
                            continue

                        hop_ip = addr[0]
                        hop_rtt = round((time.time() - probe_start) * 1000, 2)
                        hop_hostname = reverse_dns(hop_ip)
                        responded = True

                        if tcp_flags & 0x12 == 0x12:
                            destination_reached = True
                            destination_status = "open"
                            break
                        elif tcp_flags & 0x04:
                            destination_reached = True
                            destination_status = "port_closed"
                            break

                if responded:
                    break

            hop_entry = {{
                "hop": ttl,
                "ip": hop_ip,
                "rtt_ms": hop_rtt,
                "hostname": hop_hostname,
            }}
            hops.append(hop_entry)

            if destination_reached:
                break

    finally:
        send_sock.close()
        recv_icmp_sock.close()
        recv_tcp_sock.close()

    trace_duration = round((time.time() - start_time) * 1000, 2)

    # Perform TLS handshake if destination was reached
    tls_data = None
    tls_skipped_reason = None

    if destination_reached:
        sni_hostname = SNI_OVERRIDE if SNI_OVERRIDE else DESTINATION_HOST
        tls_data = perform_tls_handshake(dest_ip, DESTINATION_PORT, sni_hostname)
    else:
        tls_skipped_reason = "destination_unreachable"

    result = {{
        "error": False,
        "source_ip": source_ip,
        "destination_host": DESTINATION_HOST,
        "destination_ip": dest_ip,
        "destination_port": DESTINATION_PORT,
        "destination_reached": destination_reached,
        "destination_status": destination_status,
        "total_hops": len(hops),
        "max_hops": MAX_HOPS,
        "probe_timeout": PROBE_TIMEOUT,
        "trace_duration_ms": trace_duration,
        "hops": hops,
        "tls": tls_data,
        "tls_skipped_reason": tls_skipped_reason,
    }}

    return result


if __name__ == "__main__":
    try:
        result = run_traceroute()
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
"$PYTHON_BIN" /tmp/_goat_tls_traceroute_$$.py
'''
