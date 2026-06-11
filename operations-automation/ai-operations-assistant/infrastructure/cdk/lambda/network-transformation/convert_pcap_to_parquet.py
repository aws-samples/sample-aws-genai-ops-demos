"""
ConvertPcapToParquetLambda — uses scapy (pure Python) instead of tshark
to parse pcap files and produce Parquet via pyarrow.

No Lambda layer with native binaries needed — scapy and pyarrow are
bundled as pip dependencies in the Lambda deployment package.
"""
from __future__ import annotations

import os
import struct
import tempfile
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import boto3
from botocore.exceptions import ClientError


_S3 = None


def _get_s3_client():
    global _S3
    if _S3 is None:
        _S3 = boto3.client("s3")
    return _S3


def _validate_capture_id(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError(f"capture_id must be a string, got {type(value).__name__}")
    if not (1 <= len(value) <= 128):
        raise ValueError(f"capture_id length {len(value)} outside allowed range 1..128")
    for ch in value:
        if not (ch.isalnum() or ch in ("_", "-")):
            raise ValueError(f"capture_id contains disallowed character {ch!r}")
    return value


def _parse_pcap_with_scapy(pcap_path: str) -> List[Dict[str, Any]]:
    """Parse a pcap file using scapy and extract fields matching the
    PCAP_LOGS_COLUMNS schema."""
    # Import scapy here (cold start optimization — only import when needed)
    from scapy.all import (  # type: ignore
        DNS,
        DNSQR,
        DNSRR,
        Ether,
        ICMP,
        IP,
        IPv6,
        Raw,
        TCP,
        UDP,
        rdpcap,
    )
    try:
        from scapy.layers.inet6 import ICMPv6Unknown  # type: ignore
    except Exception:  # pragma: no cover
        ICMPv6Unknown = None  # type: ignore

    try:
        packets = rdpcap(pcap_path)
    except Exception as exc:
        raise RuntimeError(
            f"ConvertPcapToParquet: scapy failed to read pcap: {exc}"
        ) from exc

    rows: List[Dict[str, Any]] = []
    stream_map: Dict[str, int] = {}  # (src_ip, dst_ip, src_port, dst_port) -> stream_id
    stream_counter = 0

    for pkt in packets:
        row: Dict[str, Any] = {}

        # Frame-level metadata
        row["frame_time"] = datetime.fromtimestamp(
            float(pkt.time), tz=timezone.utc
        ).strftime("%Y-%m-%d %H:%M:%S.%f")
        row["frame_size"] = len(pkt)

        # L2 Ethernet fields
        if pkt.haslayer(Ether):
            row["eth_src"] = pkt[Ether].src
            row["eth_dst"] = pkt[Ether].dst
            try:
                row["eth_type"] = f"0x{int(pkt[Ether].type):04x}"
            except (TypeError, ValueError):
                row["eth_type"] = None
        else:
            row["eth_src"] = None
            row["eth_dst"] = None
            row["eth_type"] = None

        # L3 fields — IPv4 / IPv6 with full diagnostic set
        row["ip_version"] = None
        row["ip_ttl"] = None
        row["ip_id"] = None
        row["ip_flags"] = None
        row["ip_frag_offset"] = None
        row["ip_total_length"] = None
        row["ip_proto_num"] = None
        row["ip_dscp"] = None
        row["ip_ecn"] = None
        if pkt.haslayer(IP):
            ip = pkt[IP]
            row["src_ip"] = ip.src
            row["dst_ip"] = ip.dst
            row["ip_version"] = 4
            row["ip_ttl"] = int(ip.ttl)
            row["ip_id"] = int(ip.id)
            try:
                row["ip_flags"] = str(ip.flags) if ip.flags else ""
            except Exception:
                row["ip_flags"] = None
            row["ip_frag_offset"] = int(ip.frag)
            row["ip_total_length"] = int(ip.len)
            row["ip_proto_num"] = int(ip.proto)
            # DiffServ: top 6 bits = DSCP, low 2 bits = ECN
            row["ip_dscp"] = int(ip.tos) >> 2
            row["ip_ecn"] = int(ip.tos) & 0x3
        elif pkt.haslayer(IPv6):
            ip6 = pkt[IPv6]
            row["src_ip"] = ip6.src
            row["dst_ip"] = ip6.dst
            row["ip_version"] = 6
            row["ip_ttl"] = int(ip6.hlim)  # hop limit is the IPv6 TTL analogue
            row["ip_total_length"] = int(ip6.plen)
            row["ip_proto_num"] = int(ip6.nh)
            row["ip_dscp"] = (int(ip6.tc) >> 2) if ip6.tc is not None else None
            row["ip_ecn"] = (int(ip6.tc) & 0x3) if ip6.tc is not None else None
        else:
            row["src_ip"] = None
            row["dst_ip"] = None

        # L4 fields — initialise the full transport field set so every
        # row has a consistent shape regardless of protocol.
        row["src_port"] = None
        row["dst_port"] = None
        row["tcp_seq"] = None
        row["tcp_ack"] = None
        row["tcp_flags"] = None
        row["tcp_window"] = None
        row["tcp_stream"] = None
        row["tcp_options"] = []
        row["tcp_urgent_ptr"] = None
        row["tcp_payload_len"] = None
        row["udp_length"] = None
        row["icmp_type"] = None
        row["icmp_code"] = None

        if pkt.haslayer(TCP):
            tcp = pkt[TCP]
            row["protocol"] = "tcp"
            row["src_port"] = tcp.sport
            row["dst_port"] = tcp.dport
            row["tcp_seq"] = tcp.seq
            row["tcp_ack"] = tcp.ack
            row["tcp_flags"] = _flags_to_hex(tcp.flags)
            row["tcp_window"] = tcp.window
            try:
                row["tcp_urgent_ptr"] = int(tcp.urgptr)
            except (TypeError, ValueError):
                row["tcp_urgent_ptr"] = None
            # TCP segment payload length (0 for pure ACK/SYN/FIN)
            try:
                tcp_payload = bytes(tcp.payload) if tcp.payload else b""
                row["tcp_payload_len"] = len(tcp_payload)
            except Exception:
                row["tcp_payload_len"] = None

            # TCP stream assignment (bidirectional)
            key_fwd = (row["src_ip"], row["dst_ip"], row["src_port"], row["dst_port"])
            key_rev = (row["dst_ip"], row["src_ip"], row["dst_port"], row["src_port"])
            if key_fwd in stream_map:
                row["tcp_stream"] = str(stream_map[key_fwd])
            elif key_rev in stream_map:
                row["tcp_stream"] = str(stream_map[key_rev])
            else:
                stream_map[key_fwd] = stream_counter
                row["tcp_stream"] = str(stream_counter)
                stream_counter += 1

            # TCP options (from SYN packets)
            row["tcp_options"] = _extract_tcp_options(tcp) or []

        elif pkt.haslayer(UDP):
            udp = pkt[UDP]
            row["protocol"] = "udp"
            row["src_port"] = udp.sport
            row["dst_port"] = udp.dport
            try:
                row["udp_length"] = int(udp.len)
            except (TypeError, ValueError):
                row["udp_length"] = None

        elif pkt.haslayer(ICMP):
            icmp = pkt[ICMP]
            row["protocol"] = "icmp"
            try:
                row["icmp_type"] = int(icmp.type)
            except (TypeError, ValueError):
                row["icmp_type"] = None
            try:
                row["icmp_code"] = int(icmp.code)
            except (TypeError, ValueError):
                row["icmp_code"] = None
        else:
            # ICMPv6 and other L4 protocols — label by IP protocol number
            # when available so the row is still classifiable.
            proto_num = row.get("ip_proto_num")
            if proto_num == 58:
                row["protocol"] = "icmpv6"
                # Best-effort ICMPv6 type/code extraction.
                try:
                    from scapy.layers.inet6 import _ICMPv6  # type: ignore
                    if pkt.haslayer(_ICMPv6):
                        icmp6 = pkt[_ICMPv6]
                        row["icmp_type"] = int(getattr(icmp6, "type", None)) if getattr(icmp6, "type", None) is not None else None
                        row["icmp_code"] = int(getattr(icmp6, "code", None)) if getattr(icmp6, "code", None) is not None else None
                except Exception:
                    pass
            else:
                row["protocol"] = "other"

        # TLS fields
        tls_info = _extract_tls_info(pkt)
        row["tls_handshake_type"] = tls_info.get("handshake_type")
        row["tls_record_size"] = tls_info.get("record_size")
        row["tls_sni"] = tls_info.get("sni")
        row["tls_fragment_count"] = tls_info.get("fragment_count")
        row["tls_version"] = tls_info.get("version")
        row["tls_content_type"] = tls_info.get("content_type")

        # DNS fields
        dns_info = _extract_dns_info(pkt)
        row["dns_qname"] = dns_info.get("qname")
        row["dns_response_ips"] = dns_info.get("response_ips") or []
        row["dns_qtype"] = dns_info.get("qtype")
        row["dns_rcode"] = dns_info.get("rcode")
        row["dns_id"] = dns_info.get("id")
        row["dns_is_response"] = dns_info.get("is_response")

        # Payload hex summary (first 256 bytes)
        payload_bytes = bytes(pkt.payload) if pkt.payload else b""
        row["frame_payload_summary"] = payload_bytes[:256].hex() if payload_bytes else None

        rows.append(row)

    return rows


def _flags_to_hex(flags) -> str:
    """Convert scapy TCP flags to hex string matching tshark format (e.g. '0x012')."""
    try:
        return f"0x{int(flags):03x}"
    except (TypeError, ValueError):
        return "0x000"


def _extract_tcp_options(tcp_layer) -> Optional[List[str]]:
    """Extract TCP options as a list of mnemonics."""
    try:
        options = tcp_layer.options
        if not options:
            return None
        result = []
        for opt_name, opt_val in options:
            if opt_name == "MSS":
                result.append(f"MSS:{opt_val}")
            elif opt_name == "WScale":
                result.append(f"WS:{opt_val}")
            elif opt_name == "SAckOK":
                result.append("SACK_PERM")
            elif opt_name == "Timestamp":
                result.append("TS")
            elif opt_name == "NOP":
                result.append("NOP")
            elif opt_name == "EOL":
                result.append("EOL")
            else:
                result.append(str(opt_name))
        return result if result else None
    except Exception:
        return None


def _extract_tls_info(pkt) -> Dict[str, Any]:
    """Extract TLS Client Hello information from TCP payload."""
    result: Dict[str, Any] = {}
    try:
        from scapy.all import TCP, Raw  # type: ignore

        if not pkt.haslayer(TCP) or not pkt.haslayer(Raw):
            return result

        payload = bytes(pkt[Raw].load)
        if len(payload) < 6:
            return result

        # TLS record header: content_type(1) + version(2) + length(2)
        content_type = payload[0]
        # Record content type and version for ALL TLS records (handshake,
        # alert, change-cipher-spec, application-data) so the dataset can
        # support general TLS investigation, not just Client Hello.
        # Valid TLS content types are 20..24; bail out otherwise so we do
        # not mislabel arbitrary TCP payloads as TLS.
        if content_type not in (20, 21, 22, 23, 24):
            return result
        result["content_type"] = content_type
        version_major, version_minor = payload[1], payload[2]
        result["version"] = f"0x{version_major:02x}{version_minor:02x}"

        record_length = struct.unpack("!H", payload[3:5])[0]
        result["record_size"] = record_length + 5  # include the 5-byte header

        if content_type != 22:  # not a Handshake record — stop here
            return result

        # Handshake header: type(1) + length(3)
        if len(payload) < 6:
            return result
        handshake_type = payload[5]
        result["handshake_type"] = handshake_type

        if handshake_type == 1:  # Client Hello
            # Try to extract SNI from extensions
            sni = _extract_sni_from_client_hello(payload[5:])
            if sni:
                result["sni"] = sni

            # Check if the Client Hello spans multiple TCP segments
            # (fragment_count > 1 indicates the TLS record is larger than
            # the TCP segment payload — the actual fragmentation detection)
            tcp_payload_len = len(payload)
            if record_length + 5 > tcp_payload_len:
                # The TLS record is larger than what we have in this segment
                result["fragment_count"] = 2  # at least 2 segments needed
            else:
                result["fragment_count"] = 1

    except Exception:
        pass
    return result


def _extract_sni_from_client_hello(handshake_data: bytes) -> Optional[str]:
    """Parse a Client Hello handshake message to extract the SNI."""
    try:
        if len(handshake_data) < 43:
            return None

        # Skip: handshake_type(1) + length(3) + version(2) + random(32) = 38
        offset = 38

        # Session ID length + session ID
        if offset >= len(handshake_data):
            return None
        session_id_len = handshake_data[offset]
        offset += 1 + session_id_len

        # Cipher suites length + cipher suites
        if offset + 2 > len(handshake_data):
            return None
        cipher_len = struct.unpack("!H", handshake_data[offset:offset + 2])[0]
        offset += 2 + cipher_len

        # Compression methods length + methods
        if offset >= len(handshake_data):
            return None
        comp_len = handshake_data[offset]
        offset += 1 + comp_len

        # Extensions length
        if offset + 2 > len(handshake_data):
            return None
        ext_total_len = struct.unpack("!H", handshake_data[offset:offset + 2])[0]
        offset += 2
        ext_end = offset + ext_total_len

        # Walk extensions looking for SNI (type 0x0000)
        while offset + 4 <= ext_end:
            ext_type = struct.unpack("!H", handshake_data[offset:offset + 2])[0]
            ext_len = struct.unpack("!H", handshake_data[offset + 2:offset + 4])[0]
            offset += 4

            if ext_type == 0:  # SNI extension
                # SNI list: total_len(2) + type(1) + name_len(2) + name
                if ext_len >= 5:
                    name_len = struct.unpack(
                        "!H", handshake_data[offset + 3:offset + 5]
                    )[0]
                    sni = handshake_data[offset + 5:offset + 5 + name_len]
                    return sni.decode("ascii", errors="replace")
                break

            offset += ext_len

    except Exception:
        pass
    return None


def _extract_dns_info(pkt) -> Dict[str, Any]:
    """Extract DNS query/response info."""
    result: Dict[str, Any] = {}
    try:
        from scapy.all import DNS, DNSQR, DNSRR  # type: ignore

        if not pkt.haslayer(DNS):
            return result

        dns = pkt[DNS]
        # QR bit: 0=query, 1=response
        try:
            result["is_response"] = bool(int(dns.qr))
        except (TypeError, ValueError):
            result["is_response"] = None
        # Transaction ID (correlates query/response pairs)
        try:
            result["id"] = int(dns.id)
        except (TypeError, ValueError):
            result["id"] = None
        # Response code (0=NOERROR, 2=SERVFAIL, 3=NXDOMAIN, ...)
        try:
            result["rcode"] = int(dns.rcode)
        except (TypeError, ValueError):
            result["rcode"] = None

        if dns.haslayer(DNSQR):
            qname = dns[DNSQR].qname
            if isinstance(qname, bytes):
                qname = qname.decode("utf-8", errors="replace").rstrip(".")
            result["qname"] = qname
            try:
                result["qtype"] = int(dns[DNSQR].qtype)
            except (TypeError, ValueError):
                result["qtype"] = None

        # Response IPs from answer records
        if dns.ancount and dns.ancount > 0:
            ips = []
            for i in range(dns.ancount):
                try:
                    rr = dns.an[i]
                    if hasattr(rr, "rdata"):
                        ips.append(str(rr.rdata))
                except Exception:
                    pass
            if ips:
                result["response_ips"] = ips

    except Exception:
        pass
    return result


def _get_parquet_schema():
    """Return an explicit pyarrow schema matching the Glue table definition.

    This prevents pyarrow from inferring types (which would write frame_time
    as binary/string instead of timestamp, breaking Athena queries).
    """
    import pyarrow as pa  # type: ignore

    return pa.schema([
        pa.field("frame_time", pa.timestamp("us", tz="UTC")),
        pa.field("frame_size", pa.int64()),
        pa.field("eth_src", pa.string()),
        pa.field("eth_dst", pa.string()),
        pa.field("eth_type", pa.string()),
        pa.field("src_ip", pa.string()),
        pa.field("dst_ip", pa.string()),
        pa.field("src_port", pa.int32()),
        pa.field("dst_port", pa.int32()),
        pa.field("protocol", pa.string()),
        pa.field("ip_version", pa.int32()),
        pa.field("ip_ttl", pa.int32()),
        pa.field("ip_id", pa.int32()),
        pa.field("ip_flags", pa.string()),
        pa.field("ip_frag_offset", pa.int32()),
        pa.field("ip_total_length", pa.int32()),
        pa.field("ip_proto_num", pa.int32()),
        pa.field("ip_dscp", pa.int32()),
        pa.field("ip_ecn", pa.int32()),
        pa.field("icmp_type", pa.int32()),
        pa.field("icmp_code", pa.int32()),
        pa.field("udp_length", pa.int32()),
        pa.field("tcp_seq", pa.int64()),
        pa.field("tcp_ack", pa.int64()),
        pa.field("tcp_flags", pa.string()),
        pa.field("tcp_options", pa.list_(pa.string())),
        pa.field("tcp_stream", pa.string()),
        pa.field("tcp_window", pa.int32()),
        pa.field("tcp_urgent_ptr", pa.int32()),
        pa.field("tcp_payload_len", pa.int32()),
        pa.field("tls_handshake_type", pa.int32()),
        pa.field("tls_record_size", pa.int32()),
        pa.field("tls_sni", pa.string()),
        pa.field("tls_fragment_count", pa.int32()),
        pa.field("tls_version", pa.string()),
        pa.field("tls_content_type", pa.int32()),
        pa.field("dns_qname", pa.string()),
        pa.field("dns_response_ips", pa.list_(pa.string())),
        pa.field("dns_qtype", pa.int32()),
        pa.field("dns_rcode", pa.int32()),
        pa.field("dns_id", pa.int32()),
        pa.field("dns_is_response", pa.bool_()),
        pa.field("frame_payload_summary", pa.string()),
    ])


def _write_parquet(
    rows: List[Dict[str, Any]],
    capture_id: str,
    out_path: str,
) -> int:
    """Write rows to Parquet using pyarrow with an explicit schema."""
    try:
        import pyarrow as pa  # type: ignore
        import pyarrow.parquet as pq  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "ConvertPcapToParquet: pyarrow not available. Install it as a "
            "Lambda layer or include it in the deployment package."
        ) from exc

    schema = _get_parquet_schema()

    if not rows:
        empty_table = pa.table({col.name: [] for col in schema}, schema=schema)
        pq.write_table(empty_table, out_path)
        return os.path.getsize(out_path)

    # Convert frame_time from string to datetime objects for proper timestamp typing
    for row in rows:
        ft = row.get("frame_time")
        if isinstance(ft, str):
            row["frame_time"] = datetime.strptime(ft, "%Y-%m-%d %H:%M:%S.%f").replace(
                tzinfo=timezone.utc
            )

    table = pa.Table.from_pylist(rows, schema=schema)
    pq.write_table(table, out_path, compression="snappy")
    return os.path.getsize(out_path)


def lambda_handler(event: Dict[str, Any], _context: Any) -> Dict[str, Any]:
    """Convert one pcap file to Parquet using scapy and write to S3."""
    capture_id = _validate_capture_id(event.get("capture_id"))
    bucket = event.get("bucket")
    key = event.get("key")

    if not bucket or not isinstance(bucket, str):
        raise ValueError("ConvertPcapToParquet: 'bucket' is required")
    if not key or not isinstance(key, str):
        raise ValueError("ConvertPcapToParquet: 'key' is required")
    if not key.startswith(f"raw/{capture_id}/"):
        raise ValueError(
            f"ConvertPcapToParquet: 'key' {key!r} is not under "
            f"raw/{capture_id}/ prefix; refusing to process"
        )

    s3 = _get_s3_client()

    with tempfile.TemporaryDirectory() as work_dir:
        local_pcap = os.path.join(work_dir, "input.pcap")
        local_parquet = os.path.join(work_dir, "output.parquet")

        try:
            s3.download_file(bucket, key, local_pcap)
        except ClientError as exc:
            raise RuntimeError(
                f"ConvertPcapToParquet: s3:GetObject failed for "
                f"s3://{bucket}/{key}: {exc}"
            ) from exc

        rows = _parse_pcap_with_scapy(local_pcap)
        bytes_written = _write_parquet(rows, capture_id, local_parquet)

        source_basename = os.path.basename(key)
        if source_basename.endswith(".pcap"):
            source_basename = source_basename[: -len(".pcap")]
        destination_key = (
            f"parquet/capture_id={capture_id}/{source_basename}.parquet"
        )

        try:
            s3.upload_file(local_parquet, bucket, destination_key)
        except ClientError as exc:
            raise RuntimeError(
                f"ConvertPcapToParquet: s3:PutObject failed for "
                f"s3://{bucket}/{destination_key}: {exc}"
            ) from exc

    return {
        "capture_id": capture_id,
        "source_key": key,
        "destination_key": destination_key,
        "frame_count": len(rows),
        "bytes_written": bytes_written,
    }
