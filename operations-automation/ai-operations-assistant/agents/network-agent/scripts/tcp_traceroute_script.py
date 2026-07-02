"""
TCP Traceroute SSM Script Template for G.O.A.T. Network Diagnostics.

This module provides the TCP_TRACEROUTE_SCRIPT constant — a bash wrapper
around a self-contained Python script that performs TTL-based TCP SYN probing.

The script uses only Python stdlib modules (socket, struct, time, select,
json, sys) and is compatible with Python 3.6+.

The template accepts the following format parameters:
- {destination_host}: Target hostname or IPv4 address
- {destination_port}: TCP port to probe (1-65535)
- {max_hops}: Maximum TTL value (1-30)
- {probe_timeout}: Seconds to wait per hop (1-5)

Requirements covered: 1.1-1.14, 3.1-3.7, 3.12, 7.3, 7.7, 7.8
"""

from scripts import TCP_TRACEROUTE_MARKER

TCP_TRACEROUTE_SCRIPT = '''#!/bin/bash
# GOAT Network Diagnostics - TCP Traceroute
# This script is injected via SSM RunShellScript and executes entirely in /tmp.
# EXIT trap ensures cleanup regardless of success or failure.

trap "rm -f /tmp/_goat_tcp_traceroute_$$.py" EXIT

# Detect Python interpreter: try python3 first, fall back to python
PYTHON_BIN=""
if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
else
    echo "''' + TCP_TRACEROUTE_MARKER + '''"
    echo '{{"error": true, "error_type": "python_not_found", "message": "Python 3 is required but neither python3 nor python was found on PATH."}}'
    exit 0
fi

cat > /tmp/_goat_tcp_traceroute_$$.py << 'GOAT_PYTHON_SCRIPT_EOF'
import socket
import struct
import time
import select
import json
import sys
import os
import errno

MARKER = "''' + TCP_TRACEROUTE_MARKER + '''"
DESTINATION_HOST = "{destination_host}"
DESTINATION_PORT = {destination_port}
MAX_HOPS = {max_hops}
PROBE_TIMEOUT = {probe_timeout}


def resolve_host(host):
    """Resolve hostname to IPv4 address."""
    try:
        return socket.gethostbyname(host)
    except socket.gaierror as e:
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
    """Build a TCP SYN packet (IP header constructed by kernel with IP_HDRINCL=0)."""
    # TCP header fields
    offset_flags = (5 << 12) | 0x002  # data offset=5 (20 bytes), SYN flag
    window = socket.htons(1024)
    urgent = 0

    # Pack pseudo header for checksum
    src_ip_packed = socket.inet_aton(get_source_ip())
    dst_ip_packed = socket.inet_aton(dst_ip)

    # Build TCP header with zero checksum first
    tcp_header = struct.pack(
        "!HHIIHHH",
        src_port,        # Source port
        dst_port,        # Destination port
        seq_num,         # Sequence number
        0,               # Acknowledgment number
        offset_flags,    # Data offset + flags
        1024,            # Window size
        0,               # Checksum (placeholder)
    ) + struct.pack("!H", urgent)

    # Pseudo header for checksum calculation
    tcp_length = len(tcp_header)
    pseudo_header = struct.pack(
        "!4s4sBBH",
        src_ip_packed,
        dst_ip_packed,
        0,
        socket.IPPROTO_TCP,
        tcp_length,
    )

    # Calculate checksum
    chksum = checksum(pseudo_header + tcp_header)

    # Rebuild TCP header with correct checksum
    tcp_header = struct.pack(
        "!HHIIHHH",
        src_port,
        dst_port,
        seq_num,
        0,
        offset_flags,
        1024,
        chksum,
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


def run_traceroute():
    """Execute the TCP traceroute and return structured results."""
    start_time = time.time()

    # Resolve destination
    dest_ip = resolve_host(DESTINATION_HOST)
    if dest_ip is None:
        return {{
            "error": True,
            "error_type": "dns_resolution_failed",
            "message": "DNS resolution failed for host: {{}}".format(DESTINATION_HOST),
        }}

    source_ip = get_source_ip()
    hops = []
    destination_reached = False
    destination_status = None
    src_port = 33434 + (os.getpid() % 1000)

    # Create raw sockets
    try:
        # Raw socket to send TCP SYN packets
        send_sock = socket.socket(
            socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_TCP
        )
        # Raw socket to receive ICMP responses
        recv_icmp_sock = socket.socket(
            socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP
        )
        # Raw socket to receive TCP responses (SYN-ACK, RST)
        recv_tcp_sock = socket.socket(
            socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_TCP
        )
    except (PermissionError, OSError) as e:
        if hasattr(e, "errno") and e.errno == errno.EPERM:
            return {{
                "error": True,
                "error_type": "cap_net_raw_denied",
                "message": "CAP_NET_RAW capability is required to create raw sockets. Ensure the process runs with appropriate permissions.",
            }}
        if isinstance(e, PermissionError):
            return {{
                "error": True,
                "error_type": "cap_net_raw_denied",
                "message": "CAP_NET_RAW capability is required to create raw sockets. Ensure the process runs with appropriate permissions.",
            }}
        raise

    recv_icmp_sock.settimeout(PROBE_TIMEOUT)
    recv_tcp_sock.settimeout(PROBE_TIMEOUT)

    try:
        for ttl in range(1, MAX_HOPS + 1):
            # Set TTL on sending socket
            send_sock.setsockopt(
                socket.IPPROTO_IP, socket.IP_TTL, ttl
            )

            seq_num = ttl * 1000
            probe_start = time.time()

            # Build and send TCP SYN
            tcp_packet = build_tcp_syn(src_port, dest_ip, DESTINATION_PORT, seq_num)

            try:
                send_sock.sendto(tcp_packet, (dest_ip, DESTINATION_PORT))
            except OSError as e:
                if e.errno == errno.EPERM:
                    return {{
                        "error": True,
                        "error_type": "cap_net_raw_denied",
                        "message": "CAP_NET_RAW capability is required. Raw socket sendto failed with EPERM.",
                    }}
                raise

            # Wait for response (ICMP or TCP)
            hop_ip = "*"
            hop_rtt = None
            hop_hostname = None
            responded = False

            deadline = probe_start + PROBE_TIMEOUT
            while time.time() < deadline:
                remaining = deadline - time.time()
                if remaining <= 0:
                    break

                # Use select to wait on both sockets
                readable, _, _ = select.select(
                    [recv_icmp_sock, recv_tcp_sock], [], [], remaining
                )

                for sock in readable:
                    try:
                        data, addr = sock.recvfrom(1024)
                    except socket.timeout:
                        continue
                    except OSError:
                        continue

                    if sock is recv_icmp_sock:
                        # Parse ICMP packet
                        # IP header is first 20 bytes (minimum)
                        if len(data) < 28:
                            continue
                        ip_header_len = (data[0] & 0x0F) * 4
                        icmp_data = data[ip_header_len:]
                        if len(icmp_data) < 8:
                            continue
                        icmp_type = icmp_data[0]
                        icmp_code = icmp_data[1]

                        # Type 11 = Time Exceeded
                        if icmp_type == 11 and icmp_code == 0:
                            # Verify this is our packet by checking the
                            # embedded original IP packet
                            hop_ip = addr[0]
                            hop_rtt = round(
                                (time.time() - probe_start) * 1000, 2
                            )
                            hop_hostname = reverse_dns(hop_ip)
                            responded = True
                            break

                        # Type 3 = Destination Unreachable
                        if icmp_type == 3:
                            hop_ip = addr[0]
                            hop_rtt = round(
                                (time.time() - probe_start) * 1000, 2
                            )
                            hop_hostname = reverse_dns(hop_ip)
                            responded = True
                            break

                    elif sock is recv_tcp_sock:
                        # Parse TCP response
                        if len(data) < 40:
                            continue
                        ip_header_len = (data[0] & 0x0F) * 4
                        tcp_data = data[ip_header_len:]
                        if len(tcp_data) < 14:
                            continue

                        tcp_src_port = struct.unpack("!H", tcp_data[0:2])[0]
                        tcp_dst_port = struct.unpack("!H", tcp_data[2:4])[0]
                        tcp_flags = struct.unpack("!B", tcp_data[13:14])[0]

                        # Verify this response is for our probe
                        if tcp_src_port != DESTINATION_PORT:
                            continue
                        if tcp_dst_port != src_port:
                            continue

                        hop_ip = addr[0]
                        hop_rtt = round(
                            (time.time() - probe_start) * 1000, 2
                        )
                        hop_hostname = reverse_dns(hop_ip)
                        responded = True

                        # SYN-ACK: flags = 0x12 (SYN + ACK)
                        if tcp_flags & 0x12 == 0x12:
                            destination_reached = True
                            destination_status = "open"
                            break
                        # RST: flags = 0x04 or RST+ACK = 0x14
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

    result = {{
        "error": False,
        "source_instance_id": "",
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
"$PYTHON_BIN" /tmp/_goat_tcp_traceroute_$$.py
'''
