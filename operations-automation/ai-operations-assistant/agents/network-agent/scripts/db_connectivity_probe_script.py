"""
DB Connectivity Probe SSM Script Template for G.O.A.T. Network Diagnostics.

This module provides:
- DB_CONNECTIVITY_PROBE_SCRIPT: Legacy bash wrapper around a self-contained
  Python script that performs three sequential phases: TCP connect, TLS
  handshake, and protocol authentication.
- ENHANCED_DB_CONNECTIVITY_PROBE_SCRIPT: Enhanced multi-layer diagnostic probe
  that performs 6-layer diagnosis: DNS resolution, instance state, network checks,
  connection test, connection pool status, and parameter group analysis.

The scripts use only Python stdlib modules (socket, ssl, struct, json, time,
sys, os) plus boto3 (available on EC2) and are compatible with Python 3.6+.

The templates accept the following format parameters:
- {endpoint}: Target database hostname or IPv4 address
- {port}: Database port (1-65535)
- {engine}: Database engine type ("mysql", "postgresql", or empty/null)
- {instance_id}: (Enhanced only) EC2 instance ID for network checks

Requirements covered: 2.8, 2.10, 6.3, 11.1-11.11
"""

from scripts import DB_CONNECTIVITY_PROBE_MARKER


# ---------------------------------------------------------------------------
# Enhanced Diagnostic Report Structure and Phase Runner
# ---------------------------------------------------------------------------
# These functions are importable for unit testing and property-based testing.
# They are also embedded into the ENHANCED_DB_CONNECTIVITY_PROBE_SCRIPT template.
# ---------------------------------------------------------------------------

import datetime
import socket


def initialize_report(endpoint, port, engine):
    """Initialize a DiagnosticReport dict with all required sections.

    Returns a report dict matching the design schema with all phase sections
    set to initial 'skipped' status. This ensures the report always contains
    all required top-level keys regardless of which phases complete.

    Args:
        endpoint: Target database hostname or IPv4 address.
        port: Database port (1-65535).
        engine: Database engine type or None.

    Returns:
        dict: Initialized DiagnosticReport with metadata and phase placeholders.
    """
    return {
        # Metadata
        'endpoint': endpoint,
        'port': port,
        'engine': engine,
        'probe_timestamp': datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),

        # Phase results — initialized to skipped
        'dns_resolution': {'status': 'skipped', 'resolved_ips': [], 'error': 'Not yet executed'},
        'instance_state': {'status': 'skipped', 'error': 'Not yet executed'},
        'network_checks': {'status': 'skipped', 'error': 'Not yet executed'},
        'connection_test': {
            'tcp': {'connected': False, 'error': 'Not yet executed'},
            'tls': None,
            'auth': None,
        },
        'connection_pool_status': {'status': 'unknown', 'error': 'Not yet executed'},
        'parameter_group_findings': {'status': 'skipped', 'error': 'Not yet executed'},

        # Diagnosis — will be computed after phases run
        'overall_verdict': 'unknown',
        'root_cause_category': 'unknown',
    }


def safe_run(fn, *args):
    """Execute a phase function with error isolation.

    Wraps any phase function call in a try/except so that a failure in one
    phase does not prevent subsequent phases from executing. On exception,
    returns a dict with status='skipped' and the truncated error message.

    Args:
        fn: Callable phase function to execute.
        *args: Arguments to pass to the phase function.

    Returns:
        dict: Phase result dict on success, or {'status': 'skipped', 'error': ...} on failure.
    """
    try:
        return fn(*args)
    except Exception as e:
        return {'status': 'skipped', 'error': str(e)[:512]}


# ---------------------------------------------------------------------------
# Stub phase functions — placeholders for tasks 3.2–3.8
# These return 'skipped' status and will be replaced with real implementations.
# ---------------------------------------------------------------------------

def check_dns(endpoint):
    """Phase 1: DNS Resolution.

    Resolves the RDS endpoint hostname using socket.getaddrinfo() and reports
    the resolved IP addresses. Handles edge cases like empty hostnames and
    IP addresses passed directly (no DNS lookup needed).

    Args:
        endpoint: Target database hostname or IPv4 address.

    Returns:
        dict: {'status': 'pass', 'resolved_ips': [list of IPs]} on success,
              {'status': 'fail', 'resolved_ips': [], 'error': message} on failure.
    """
    # Handle empty or None endpoint
    if not endpoint or not endpoint.strip():
        return {'status': 'fail', 'resolved_ips': [], 'error': 'Empty or missing endpoint hostname'}

    endpoint = endpoint.strip()

    # Check if endpoint is already an IP address (no DNS resolution needed)
    try:
        socket.inet_aton(endpoint)
        # Valid IPv4 address — no DNS resolution required
        return {'status': 'pass', 'resolved_ips': [endpoint]}
    except socket.error:
        pass  # Not an IPv4 literal, proceed with DNS resolution

    # Also check for IPv6 literal
    try:
        socket.inet_pton(socket.AF_INET6, endpoint)
        return {'status': 'pass', 'resolved_ips': [endpoint]}
    except (socket.error, OSError):
        pass  # Not an IPv6 literal, proceed with DNS resolution

    # Perform DNS resolution
    try:
        # Use port 0 to avoid service-specific filtering
        addr_infos = socket.getaddrinfo(endpoint, 0)
        # Extract unique IP addresses from results
        resolved_ips = list(set(
            addr_info[4][0] for addr_info in addr_infos
        ))
        if not resolved_ips:
            return {'status': 'fail', 'resolved_ips': [], 'error': 'DNS resolution returned no addresses'}
        return {'status': 'pass', 'resolved_ips': sorted(resolved_ips)}
    except socket.gaierror as e:
        return {'status': 'fail', 'resolved_ips': [], 'error': f'DNS resolution failed: {e}'}
    except Exception as e:
        return {'status': 'fail', 'resolved_ips': [], 'error': f'DNS resolution error: {e}'}


def check_instance_state(endpoint):
    """Phase 2: RDS Instance State check via DescribeDBInstances.

    Extracts the DB instance identifier from the RDS endpoint hostname
    and queries the RDS API to verify the instance is in 'available' state.

    Args:
        endpoint: Target database hostname or IPv4 address.

    Returns:
        dict: Phase result with status 'pass', 'fail', or 'skipped' and
              relevant details (db_instance_status or error message).
    """
    import re

    # Edge case: endpoint is an IP address — no RDS API lookup possible
    try:
        import ipaddress
        ipaddress.ip_address(endpoint)
        return {'status': 'skipped', 'error': 'Endpoint is an IP address; cannot determine RDS instance identifier'}
    except (ValueError, TypeError):
        pass  # Not an IP address, continue with hostname parsing

    # Parse the DB instance identifier from the RDS endpoint hostname
    # Pattern: <instance-id>.<random>.<region>.rds.amazonaws.com
    if not endpoint or not endpoint.lower().endswith('.rds.amazonaws.com'):
        return {'status': 'skipped', 'error': 'Endpoint does not match RDS hostname pattern (*.rds.amazonaws.com)'}

    # Extract instance ID from the first subdomain segment
    instance_id = endpoint.split('.')[0]
    if not instance_id:
        return {'status': 'skipped', 'error': 'Could not extract DB instance identifier from endpoint'}

    try:
        import boto3
        from botocore.exceptions import ClientError

        rds_client = boto3.client('rds')
        response = rds_client.describe_db_instances(DBInstanceIdentifier=instance_id)

        db_instances = response.get('DBInstances', [])
        if not db_instances:
            return {'status': 'skipped', 'error': f'No DB instance found with identifier: {instance_id}'}

        db_status = db_instances[0].get('DBInstanceStatus', 'unknown')

        if db_status == 'available':
            return {'status': 'pass', 'db_instance_status': 'available'}
        else:
            return {
                'status': 'fail',
                'db_instance_status': db_status,
                'error': 'Instance is not in available state',
            }

    except ClientError as e:
        error_code = e.response.get('Error', {}).get('Code', '')
        if error_code in ('AccessDenied', 'AccessDeniedException', 'UnauthorizedAccess',
                          'AuthFailure', 'InvalidClientTokenId'):
            return {'status': 'skipped', 'error': f'Insufficient IAM permissions to describe RDS instances: {error_code}'}
        return {'status': 'skipped', 'error': f'AWS API error: {error_code} - {str(e)[:256]}'}
    except ImportError:
        return {'status': 'skipped', 'error': 'boto3 not available on this instance'}
    except Exception as e:
        return {'status': 'skipped', 'error': f'Unexpected error checking instance state: {str(e)[:256]}'}


def check_network(instance_id, endpoint, port):
    """Phase 3: Network Checks — SG, NACL, Routes.

    Validates security group rules allow traffic on the target port, analyzes
    NACL rules for blocking entries, and verifies route table connectivity.
    Each sub-check is independent — permission errors on one do not prevent
    others from running.

    Args:
        instance_id: EC2 instance ID (used for context; RDS SGs come from DescribeDBInstances).
        endpoint: RDS endpoint hostname (used to identify the DB instance).
        port: Target port number to validate.

    Returns:
        dict: Structured result with status, security_group, nacl, and route_table findings.
    """
    import boto3

    result = {
        'status': 'skipped',
        'security_group': {'allows_port': False},
        'nacl': {'allows_traffic': True},
        'route_table': {'has_route': False},
        'error': None,
    }

    try:
        rds_client = boto3.client('rds')
        ec2_client = boto3.client('ec2')
    except Exception as e:
        result['error'] = f'Failed to create boto3 clients: {str(e)[:256]}'
        return result

    # --- Security Group Check ---
    sg_ids = []
    db_subnet_ids = []
    vpc_id = None
    try:
        # Find RDS instance by endpoint
        db_instances = rds_client.describe_db_instances()
        target_db = None
        for db in db_instances.get('DBInstances', []):
            db_endpoint = db.get('Endpoint', {}).get('Address', '')
            if db_endpoint == endpoint:
                target_db = db
                break

        if target_db:
            # Get security group IDs from VpcSecurityGroups
            for sg in target_db.get('VpcSecurityGroups', []):
                sg_ids.append(sg.get('VpcSecurityGroupId'))
            # Get subnet group info for NACL/route checks
            subnet_group_name = target_db.get('DBSubnetGroup', {}).get('DBSubnetGroupName')
            vpc_id = target_db.get('DBSubnetGroup', {}).get('VpcId')
            for subnet in target_db.get('DBSubnetGroup', {}).get('Subnets', []):
                db_subnet_ids.append(subnet.get('SubnetIdentifier'))

        if sg_ids:
            sg_response = ec2_client.describe_security_groups(GroupIds=sg_ids)
            allows_port = False
            matching_rule_id = None
            for sg in sg_response.get('SecurityGroups', []):
                for rule in sg.get('IpPermissions', []):
                    # Check if rule allows traffic on the target port
                    from_port = rule.get('FromPort', 0)
                    to_port = rule.get('ToPort', 0)
                    ip_protocol = rule.get('IpProtocol', '')
                    # -1 means all traffic
                    if ip_protocol == '-1' or (from_port <= port <= to_port):
                        allows_port = True
                        matching_rule_id = sg.get('GroupId')
                        break
                if allows_port:
                    break
            result['security_group'] = {
                'allows_port': allows_port,
                'rule_id': matching_rule_id,
            }
        else:
            result['security_group'] = {
                'allows_port': False,
                'error': 'No security groups found for RDS instance',
            }
    except Exception as e:
        error_msg = str(e)[:256]
        if 'AccessDenied' in error_msg or 'UnauthorizedOperation' in error_msg:
            result['security_group'] = {'allows_port': False, 'error': f'Permission denied: {error_msg}'}
        else:
            result['security_group'] = {'allows_port': False, 'error': error_msg}

    # --- NACL Check ---
    try:
        if db_subnet_ids:
            nacl_response = ec2_client.describe_network_acls(
                Filters=[{'Name': 'association.subnet-id', 'Values': db_subnet_ids}]
            )
            blocking_rule = None
            allows_traffic = True
            for nacl in nacl_response.get('NetworkAcls', []):
                # Check inbound rules (Egress=False) sorted by rule number
                inbound_rules = sorted(
                    [e for e in nacl.get('Entries', []) if not e.get('Egress', True)],
                    key=lambda x: x.get('RuleNumber', 32767)
                )
                for entry in inbound_rules:
                    rule_num = entry.get('RuleNumber', 32767)
                    rule_action = entry.get('RuleAction', 'allow')
                    protocol = entry.get('Protocol', '-1')
                    port_range = entry.get('PortRange', {})
                    from_port = port_range.get('From', 0)
                    to_port = port_range.get('To', 65535)

                    # Protocol -1 means all; 6 is TCP
                    port_matches = (protocol == '-1') or (
                        protocol == '6' and from_port <= port <= to_port
                    )
                    if port_matches:
                        if rule_action == 'deny':
                            allows_traffic = False
                            blocking_rule = f'Rule {rule_num}: DENY port {port}'
                        # First matching rule wins (NACL rules are ordered)
                        break

            result['nacl'] = {
                'allows_traffic': allows_traffic,
                'blocking_rule': blocking_rule,
            }
        else:
            result['nacl'] = {'allows_traffic': True, 'error': 'No DB subnets found for NACL check'}
    except Exception as e:
        error_msg = str(e)[:256]
        if 'AccessDenied' in error_msg or 'UnauthorizedOperation' in error_msg:
            result['nacl'] = {'allows_traffic': True, 'error': f'Permission denied: {error_msg}'}
        else:
            result['nacl'] = {'allows_traffic': True, 'error': error_msg}

    # --- Route Table Check ---
    try:
        if db_subnet_ids and vpc_id:
            # Check if there's a route table associated with the DB subnets
            rt_response = ec2_client.describe_route_tables(
                Filters=[{'Name': 'association.subnet-id', 'Values': db_subnet_ids}]
            )
            route_tables = rt_response.get('RouteTables', [])
            # If no explicit association, check main route table for VPC
            if not route_tables:
                rt_response = ec2_client.describe_route_tables(
                    Filters=[
                        {'Name': 'vpc-id', 'Values': [vpc_id]},
                        {'Name': 'association.main', 'Values': ['true']},
                    ]
                )
                route_tables = rt_response.get('RouteTables', [])

            has_route = False
            if route_tables:
                for rt in route_tables:
                    for route in rt.get('Routes', []):
                        # Look for a local route (VPC CIDR) or any active route
                        state = route.get('State', 'active')
                        gateway = route.get('GatewayId', '')
                        if state == 'active' and gateway == 'local':
                            has_route = True
                            break
                    if has_route:
                        break

            result['route_table'] = {'has_route': has_route}
        else:
            result['route_table'] = {'has_route': False, 'error': 'No subnet/VPC info for route check'}
    except Exception as e:
        error_msg = str(e)[:256]
        if 'AccessDenied' in error_msg or 'UnauthorizedOperation' in error_msg:
            result['route_table'] = {'has_route': False, 'error': f'Permission denied: {error_msg}'}
        else:
            result['route_table'] = {'has_route': False, 'error': error_msg}

    # --- Determine overall status ---
    # 'fail' only if a check explicitly shows traffic is blocked
    # 'skipped' if permissions prevent checking
    sg_allows = result['security_group'].get('allows_port', False)
    nacl_allows = result['nacl'].get('allows_traffic', True)
    has_route = result['route_table'].get('has_route', False)

    sg_error = result['security_group'].get('error')
    nacl_error = result['nacl'].get('error')
    rt_error = result['route_table'].get('error')

    if not sg_allows and not sg_error:
        result['status'] = 'fail'
    elif not nacl_allows:
        result['status'] = 'fail'
    elif sg_error and nacl_error and rt_error:
        # All checks were skipped due to permission errors
        result['status'] = 'skipped'
        result['error'] = 'All network checks skipped due to permission errors'
    elif sg_allows and nacl_allows and has_route:
        result['status'] = 'pass'
    elif sg_allows and nacl_allows:
        # SG and NACL pass but route might have errored
        result['status'] = 'pass'
    else:
        result['status'] = 'skipped'
        result['error'] = 'Insufficient data to determine network path status'

    return result


def run_connection_test(endpoint, port, engine):
    """Phase 4: Connection Test — TCP → TLS → Protocol.

    Attempts a layered connection test:
    1. TCP connect to endpoint:port with 10-second timeout
    2. TLS handshake (if TCP succeeds)
    3. Protocol-level authentication detection (if TLS succeeds)

    Captures error codes from connection failures for use by the error
    categorization phase (task 3.6).

    Returns:
        dict with 'tcp', 'tls', and 'auth' keys containing structured results.
    """
    import ssl
    import struct
    import time

    tcp_timeout = 10

    # --- TCP Phase ---
    tcp_result = {'connected': False, 'connect_time_ms': None, 'error': None, 'error_code': None}
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(tcp_timeout)
        start = time.time()
        sock.connect((endpoint, port))
        elapsed = (time.time() - start) * 1000
        tcp_result['connected'] = True
        tcp_result['connect_time_ms'] = round(elapsed, 2)
    except OSError as e:
        tcp_result['error'] = str(e)[:512]
        tcp_result['error_code'] = getattr(e, 'errno', None)
        return {'tcp': tcp_result, 'tls': None, 'auth': None}

    # --- TLS Phase ---
    tls_result = {'connected': False, 'tls_version': None, 'error': None}
    tls_sock = None
    try:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        tls_sock = context.wrap_socket(sock, server_hostname=endpoint)
        tls_result['connected'] = True
        tls_result['tls_version'] = tls_sock.version()
    except Exception as e:
        tls_result['error'] = str(e)[:512]
        # Close raw socket if TLS failed
        try:
            sock.close()
        except Exception:
            pass
        return {'tcp': tcp_result, 'tls': tls_result, 'auth': None}

    # --- Auth/Protocol Phase ---
    engine_lower = (engine or '').strip().lower()
    if not engine_lower or engine_lower in ('null', 'none', ''):
        # No engine specified; skip auth phase
        try:
            tls_sock.close()
        except Exception:
            pass
        return {'tcp': tcp_result, 'tls': tls_result, 'auth': None}

    auth_result = {'success': False, 'details': {}, 'error': None, 'error_code': None}

    if engine_lower == 'mysql':
        auth_result = _auth_phase_mysql(tls_sock)
    elif engine_lower == 'postgresql':
        auth_result = _auth_phase_postgresql(tls_sock)
    else:
        auth_result['error'] = f'Unsupported engine: {engine}'

    try:
        tls_sock.close()
    except Exception:
        pass

    return {'tcp': tcp_result, 'tls': tls_result, 'auth': auth_result}


def _auth_phase_mysql(tls_sock):
    """Read MySQL initial handshake packet to detect protocol-level connectivity."""
    import struct

    result = {'success': False, 'details': {}, 'error': None, 'error_code': None}
    try:
        tls_sock.settimeout(5)
        # Read packet header: 3 bytes length + 1 byte sequence id
        header = b''
        while len(header) < 4:
            chunk = tls_sock.recv(4 - len(header))
            if not chunk:
                result['error'] = 'Connection closed before handshake'
                return result
            header += chunk

        payload_length = struct.unpack('<I', header[:3] + b'\x00')[0]

        # Read the payload
        payload = b''
        while len(payload) < payload_length:
            chunk = tls_sock.recv(payload_length - len(payload))
            if not chunk:
                break
            payload += chunk

        if len(payload) < 2:
            result['error'] = 'Handshake payload too short'
            return result

        # Check if this is an error packet (first byte == 0xFF)
        if payload[0] == 0xFF:
            # Error packet: 2-byte error code follows
            if len(payload) >= 3:
                error_code = struct.unpack('<H', payload[1:3])[0]
                result['error_code'] = error_code
                # Try to extract error message
                msg_start = 3
                if len(payload) > 4 and payload[3:4] == b'#':
                    msg_start = 9  # Skip sql_state marker
                error_msg = payload[msg_start:].decode('ascii', errors='replace')
                result['error'] = f'MySQL error {error_code}: {error_msg}'
            else:
                result['error'] = 'MySQL error packet (too short to decode)'
            return result

        protocol_version = payload[0]
        # Server version is a null-terminated string starting at byte 1
        null_pos = payload.find(b'\x00', 1)
        if null_pos == -1:
            server_version = 'unknown'
        else:
            server_version = payload[1:null_pos].decode('ascii', errors='replace')

        result['success'] = True
        result['details'] = {
            'protocol_version': protocol_version,
            'server_version': server_version,
        }
        return result
    except Exception as e:
        result['error'] = str(e)[:512]
        return result


def _auth_phase_postgresql(tls_sock):
    """Send PostgreSQL StartupMessage and read response."""
    import struct

    result = {'success': False, 'details': {}, 'error': None, 'error_code': None}
    try:
        tls_sock.settimeout(5)
        # Build StartupMessage: version 3.0, user=goat_probe
        user_param = b'user\x00goat_probe\x00\x00'
        version = struct.pack('!I', 196608)  # 3.0
        msg_body = version + user_param
        msg_length = struct.pack('!I', len(msg_body) + 4)
        startup_msg = msg_length + msg_body

        tls_sock.sendall(startup_msg)

        # Read response: first byte indicates message type
        resp = b''
        while len(resp) < 1:
            chunk = tls_sock.recv(1)
            if not chunk:
                result['error'] = 'Connection closed before response'
                return result
            resp += chunk

        msg_type = chr(resp[0])
        auth_type = 'unknown'
        if msg_type == 'R':
            auth_type = 'auth_request'
        elif msg_type == 'E':
            auth_type = 'error'
            # Try to read error details for error code extraction
            try:
                # Read 4-byte length
                length_bytes = b''
                while len(length_bytes) < 4:
                    chunk = tls_sock.recv(4 - len(length_bytes))
                    if not chunk:
                        break
                    length_bytes += chunk
                if len(length_bytes) == 4:
                    body_len = struct.unpack('!I', length_bytes)[0] - 4
                    body = b''
                    while len(body) < body_len:
                        chunk = tls_sock.recv(min(body_len - len(body), 4096))
                        if not chunk:
                            break
                        body += chunk
                    # Parse error fields (each field: type_byte + null-terminated string)
                    error_msg = body.decode('ascii', errors='replace')
                    result['error'] = f'PostgreSQL error: {error_msg[:256]}'
            except Exception:
                pass
        else:
            auth_type = f'other_{msg_type}'

        result['success'] = msg_type == 'R'
        result['details'] = {
            'auth_type': auth_type,
        }
        return result
    except Exception as e:
        result['error'] = str(e)[:512]
        return result


# ---------------------------------------------------------------------------
# Phase 5: Error Categorization
# ---------------------------------------------------------------------------
# Maps MySQL error codes to diagnostic categories for structured reporting.
# This function is importable at module level for property-based testing.
# ---------------------------------------------------------------------------

ERROR_CATEGORIES = {
    # MySQL error codes → categories
    1040: 'pool_exhaustion',      # Too many connections
    1045: 'authentication',       # Access denied
    2003: 'network_timeout',      # Can't connect to server
    2002: 'connection_refused',   # Connection refused (socket)
    2005: 'dns_failure',          # Unknown host
    2006: 'connection_lost',      # Server has gone away
    2013: 'network_timeout',      # Lost connection during query
}


def categorize_error(error_code: int, error_message: str) -> str:
    """Map MySQL error to diagnostic category.

    Categorizes MySQL connection errors into one of the defined diagnostic
    categories based on error code lookup, with fallback pattern matching
    on the error message for codes not in the known mapping.

    Always returns exactly one valid category string — never None or empty.

    Args:
        error_code: MySQL error code (integer).
        error_message: Human-readable error message string.

    Returns:
        str: One of 'pool_exhaustion', 'authentication', 'network_timeout',
             'connection_refused', 'dns_failure', 'connection_lost', 'unknown'.

    Validates: Requirements 2.4
    """
    # Direct error code lookup
    if error_code in ERROR_CATEGORIES:
        return ERROR_CATEGORIES[error_code]

    # Fallback: pattern matching on error message
    msg_lower = error_message.lower() if error_message else ''
    if 'timeout' in msg_lower:
        return 'network_timeout'
    if 'refused' in msg_lower:
        return 'connection_refused'

    return 'unknown'


# ---------------------------------------------------------------------------
# Phase 6: Connection Pool Status — detect_pool_exhaustion + check_pool_status
# ---------------------------------------------------------------------------

EXHAUSTION_THRESHOLD = 0.90  # 90% utilization


def detect_pool_exhaustion(threads_connected: int, max_connections: int) -> dict:
    """Determine pool health from MySQL status values.

    Classifies the connection pool status based on the ratio of active threads
    to maximum allowed connections:
    - 'exhausted' when utilization >= 100%
    - 'warning' when utilization >= 90% but < 100%
    - 'healthy' when utilization < 90%

    Args:
        threads_connected: Number of currently active MySQL threads/connections.
        max_connections: Maximum allowed connections configured in the parameter group.

    Returns:
        dict: Pool status with keys: status, threads_connected, max_connections,
              utilization_percent. Returns 'unknown' status with error if
              max_connections <= 0.

    Validates: Requirements 2.2, 2.3
    """
    if max_connections <= 0:
        return {'status': 'unknown', 'error': 'Invalid max_connections value'}

    utilization = threads_connected / max_connections

    if utilization >= 1.0:
        status = 'exhausted'
    elif utilization >= EXHAUSTION_THRESHOLD:
        status = 'warning'
    else:
        status = 'healthy'

    return {
        'status': status,
        'threads_connected': threads_connected,
        'max_connections': max_connections,
        'utilization_percent': round(utilization * 100, 1),
    }


def check_pool_status(endpoint, port):
    """Phase 6: Connection Pool Status.

    Attempts a MySQL connection to check pool utilization:
    - On success: queries SHOW STATUS LIKE 'Threads_connected' and
      SHOW GLOBAL VARIABLES LIKE 'max_connections', then uses
      detect_pool_exhaustion() to classify the result.
    - On error 1040 (Too many connections): reports pool exhaustion directly.
    - On timeout: reports as network issue, skips pool status.

    Args:
        endpoint: Target database hostname or IPv4 address.
        port: Database port (1-65535).

    Returns:
        dict: Pool status result with status and relevant metrics or error info.

    Validates: Requirements 2.2, 2.3
    """
    try:
        import pymysql
    except ImportError:
        # pymysql not available — try a socket-level approach
        return _check_pool_status_socket(endpoint, port)

    try:
        conn = pymysql.connect(
            host=endpoint,
            port=int(port),
            user='admin',
            database='information_schema',
            connect_timeout=5,
            read_timeout=5,
        )
    except Exception as e:
        error_code = getattr(e, 'args', (None,))[0] if hasattr(e, 'args') and e.args else None
        error_msg = str(e)

        # Error 1040: Too many connections — pool exhaustion confirmed
        if error_code == 1040:
            return {
                'status': 'exhausted',
                'error': 'Too many connections (error 1040)',
            }

        # Timeout errors — likely network issue
        if 'timeout' in error_msg.lower() or 'timed out' in error_msg.lower():
            return {
                'status': 'unknown',
                'error': 'Connection timed out — likely network issue',
            }

        # Other connection errors (auth failures, etc.) — cannot determine pool status
        return {
            'status': 'unknown',
            'error': f'Connection failed: {error_msg[:256]}',
        }

    # Connection succeeded — query pool metrics
    try:
        cursor = conn.cursor()

        # Get Threads_connected
        cursor.execute("SHOW STATUS LIKE 'Threads_connected'")
        row = cursor.fetchone()
        threads_connected = int(row[1]) if row else 0

        # Get max_connections
        cursor.execute("SHOW GLOBAL VARIABLES LIKE 'max_connections'")
        row = cursor.fetchone()
        max_connections = int(row[1]) if row else 0

        cursor.close()
        conn.close()

        return detect_pool_exhaustion(threads_connected, max_connections)

    except Exception as e:
        try:
            conn.close()
        except Exception:
            pass
        return {
            'status': 'unknown',
            'error': f'Failed to query pool status: {str(e)[:256]}',
        }


def _check_pool_status_socket(endpoint, port):
    """Fallback pool status check using raw socket when pymysql is unavailable.

    Attempts a TCP connection to the MySQL port. If the server sends back an
    error packet with code 1040, we know the pool is exhausted. Otherwise,
    we cannot determine pool metrics without a full MySQL client.

    Args:
        endpoint: Target database hostname or IPv4 address.
        port: Database port (1-65535).

    Returns:
        dict: Pool status result.
    """
    import struct

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect((endpoint, int(port)))
    except socket.timeout:
        return {
            'status': 'unknown',
            'error': 'Connection timed out — likely network issue',
        }
    except Exception as e:
        error_msg = str(e)
        if 'timed out' in error_msg.lower() or 'timeout' in error_msg.lower():
            return {
                'status': 'unknown',
                'error': 'Connection timed out — likely network issue',
            }
        return {
            'status': 'unknown',
            'error': f'Socket connection failed: {error_msg[:256]}',
        }

    # Read MySQL initial handshake or error packet
    try:
        sock.settimeout(5)
        # Read packet header: 3 bytes length + 1 byte sequence
        header = b''
        while len(header) < 4:
            chunk = sock.recv(4 - len(header))
            if not chunk:
                break
            header += chunk

        if len(header) < 4:
            sock.close()
            return {'status': 'unknown', 'error': 'Incomplete MySQL packet header'}

        payload_length = struct.unpack('<I', header[:3] + b'\x00')[0]

        # Read payload
        payload = b''
        while len(payload) < payload_length:
            chunk = sock.recv(payload_length - len(payload))
            if not chunk:
                break
            payload += chunk

        sock.close()

        if not payload:
            return {'status': 'unknown', 'error': 'Empty MySQL packet payload'}

        # Check if it's an error packet (first byte = 0xFF)
        if payload[0] == 0xFF and len(payload) >= 3:
            error_code = struct.unpack('<H', payload[1:3])[0]
            if error_code == 1040:
                return {
                    'status': 'exhausted',
                    'error': 'Too many connections (error 1040)',
                }
            return {
                'status': 'unknown',
                'error': f'MySQL error {error_code} during handshake',
            }

        # Server sent a handshake packet — connection is possible but we
        # cannot query SHOW STATUS without full protocol authentication
        return {
            'status': 'unknown',
            'error': 'pymysql not available; cannot query pool metrics (connection succeeded at TCP level)',
        }

    except socket.timeout:
        try:
            sock.close()
        except Exception:
            pass
        return {
            'status': 'unknown',
            'error': 'Connection timed out reading MySQL handshake',
        }
    except Exception as e:
        try:
            sock.close()
        except Exception:
            pass
        return {
            'status': 'unknown',
            'error': f'Error reading MySQL handshake: {str(e)[:256]}',
        }


# ---------------------------------------------------------------------------
# Phase 7: Parameter Group Analysis
# ---------------------------------------------------------------------------

LOW_MAX_CONNECTIONS_THRESHOLD = 50


def flag_parameter_issues(max_connections, instance_class):
    """Flag abnormally low parameter values.

    Checks whether max_connections is below the LOW_MAX_CONNECTIONS_THRESHOLD (50).
    Returns a list of findings — non-empty if and only if max_connections < 50.

    This function is the key importable unit for property-based testing (task 3.12).

    Args:
        max_connections: Positive integer representing the max_connections parameter value.
        instance_class: String representing the RDS instance class (e.g., 'db.t4g.micro').

    Returns:
        list: List of finding dicts. Non-empty if max_connections < 50, empty otherwise.
    """
    findings = []

    if max_connections < LOW_MAX_CONNECTIONS_THRESHOLD:
        findings.append({
            'name': 'max_connections',
            'value': str(max_connections),
            'issue': f'Abnormally low max_connections ({max_connections}) for instance class {instance_class}. '
                     f'Default for this class is typically much higher. '
                     f'This severely limits concurrent client connections.',
        })

    return findings


def check_parameters(endpoint):
    """Phase 7: Parameter Group Analysis.

    Extracts the DB instance identifier from the RDS endpoint hostname,
    queries DescribeDBInstances to get the parameter group name and instance class,
    then queries DescribeDBParameters to get actual parameter values.
    Calls flag_parameter_issues() to determine if there are configuration issues.

    Gracefully handles permission errors (returns status='skipped') and
    non-RDS endpoints (IP addresses → skip).

    Args:
        endpoint: RDS endpoint hostname or IP address.

    Returns:
        dict: Result with keys 'status', 'parameter_group_name', 'flagged_parameters', 'error'.
    """
    import re

    # Skip if endpoint is an IP address (not an RDS hostname)
    if not endpoint or not endpoint.strip():
        return {'status': 'skipped', 'parameter_group_name': None, 'flagged_parameters': [], 'error': 'Empty endpoint'}

    endpoint = endpoint.strip()

    # Check if endpoint is an IP address — skip parameter analysis
    try:
        socket.inet_aton(endpoint)
        return {'status': 'skipped', 'parameter_group_name': None, 'flagged_parameters': [], 'error': 'Endpoint is an IP address, not an RDS hostname'}
    except socket.error:
        pass  # Not an IP address, continue with RDS hostname parsing

    # Extract DB instance identifier from RDS endpoint hostname
    # Pattern: <db-instance-id>.<random>.rds.amazonaws.com
    match = re.match(r'^([^.]+)\.', endpoint)
    if not match:
        return {'status': 'skipped', 'parameter_group_name': None, 'flagged_parameters': [], 'error': 'Could not extract DB instance identifier from endpoint'}

    db_instance_id = match.group(1)

    try:
        import boto3
    except ImportError:
        return {'status': 'skipped', 'parameter_group_name': None, 'flagged_parameters': [], 'error': 'boto3 not available'}

    try:
        rds_client = boto3.client('rds')

        # Get DB instance details
        response = rds_client.describe_db_instances(DBInstanceIdentifier=db_instance_id)
        db_instances = response.get('DBInstances', [])
        if not db_instances:
            return {'status': 'skipped', 'parameter_group_name': None, 'flagged_parameters': [], 'error': f'No DB instance found with identifier {db_instance_id}'}

        db_instance = db_instances[0]
        instance_class = db_instance.get('DBInstanceClass', 'unknown')

        # Get parameter group name
        param_groups = db_instance.get('DBParameterGroups', [])
        if not param_groups:
            return {'status': 'skipped', 'parameter_group_name': None, 'flagged_parameters': [], 'error': 'No parameter group associated with instance'}

        param_group_name = param_groups[0].get('DBParameterGroupName', '')

        # Query parameter group for max_connections
        max_connections = None
        try:
            paginator = rds_client.get_paginator('describe_db_parameters')
            for page in paginator.paginate(DBParameterGroupName=param_group_name):
                for param in page.get('Parameters', []):
                    if param.get('ParameterName') == 'max_connections':
                        param_value = param.get('ParameterValue')
                        if param_value is not None:
                            try:
                                max_connections = int(param_value)
                            except (ValueError, TypeError):
                                pass
                        break
                if max_connections is not None:
                    break
        except Exception as e:
            return {
                'status': 'skipped',
                'parameter_group_name': param_group_name,
                'flagged_parameters': [],
                'error': f'Could not retrieve parameters: {str(e)[:256]}'
            }

        # If max_connections not found or not set explicitly, skip flagging
        if max_connections is None:
            return {
                'status': 'ok',
                'parameter_group_name': param_group_name,
                'flagged_parameters': [],
                'error': None
            }

        # Flag parameter issues
        flagged = flag_parameter_issues(max_connections, instance_class)

        return {
            'status': 'warning' if flagged else 'ok',
            'parameter_group_name': param_group_name,
            'flagged_parameters': flagged,
            'error': None
        }

    except Exception as e:
        error_msg = str(e)
        # Check for permission-related errors
        if 'AccessDenied' in error_msg or 'UnauthorizedAccess' in error_msg or 'not authorized' in error_msg.lower():
            return {'status': 'skipped', 'parameter_group_name': None, 'flagged_parameters': [], 'error': f'Insufficient permissions: {error_msg[:256]}'}
        return {'status': 'skipped', 'parameter_group_name': None, 'flagged_parameters': [], 'error': f'Error querying RDS: {error_msg[:256]}'}


# ---------------------------------------------------------------------------
# Verdict and root cause determination (stub — implemented in task 3.9)
# ---------------------------------------------------------------------------

def determine_verdict(report):
    """Determine overall diagnostic verdict from phase results.

    Analyzes all phase results in severity order and produces a clear
    human-readable verdict string. Checks phases from most fundamental
    (DNS) to most specific (parameter group).

    Args:
        report: Complete DiagnosticReport dict with all phase results.

    Returns:
        str: Overall verdict string describing the primary issue found.
    """
    # Check for failures in severity order (most fundamental first)

    # 1. DNS — nothing else works if name resolution fails
    dns = report.get('dns_resolution', {})
    if dns.get('status') == 'fail':
        error = dns.get('error', '')
        return f'DNS resolution failed for endpoint: {error}' if error else 'DNS resolution failed'

    # 2. Instance state — instance must be available
    instance = report.get('instance_state', {})
    if instance.get('status') == 'fail':
        db_status = instance.get('db_instance_status', 'unknown')
        return f'RDS instance not available (status: {db_status})'

    # 3. Network path — SG, NACL, routes must allow traffic
    network = report.get('network_checks', {})
    if network.get('status') == 'fail':
        sg = network.get('security_group', {})
        nacl = network.get('nacl', {})
        if not sg.get('allows_port', True):
            return 'Network path blocked: security group denies traffic on target port'
        if not nacl.get('allows_traffic', True):
            blocking = nacl.get('blocking_rule', 'unknown rule')
            return f'Network path blocked: NACL denies traffic ({blocking})'
        return 'Network path blocked'

    # 4. Connection pool — exhaustion prevents new connections
    pool = report.get('connection_pool_status', {})
    if pool.get('status') == 'exhausted':
        threads = pool.get('threads_connected')
        max_conn = pool.get('max_connections')
        if threads is not None and max_conn is not None:
            return f'Connection pool exhausted: {threads}/{max_conn} connections in use (100% utilization)'
        return 'Connection pool exhausted: all available connections are in use'

    # 5. Authentication — credentials or access issue
    conn_test = report.get('connection_test', {})
    auth = conn_test.get('auth')
    if auth and not auth.get('success', True):
        error_code = auth.get('error_code')
        if error_code == 1040:
            # This is actually pool exhaustion detected at auth phase
            return 'Connection pool exhausted: MySQL error 1040 (Too many connections)'
        if error_code == 1045:
            return 'Authentication failed: access denied for user'
        error_msg = auth.get('error', '')
        return f'Authentication/protocol error: {error_msg}' if error_msg else 'Authentication failed'

    # 6. TCP connection failure (not covered by above)
    tcp = conn_test.get('tcp', {})
    if not tcp.get('connected', False):
        error = tcp.get('error', '')
        if ('timeout' in error.lower() or 'timed out' in error.lower()) if error else False:
            return 'TCP connection timed out: host unreachable or port blocked'
        if 'refused' in error.lower() if error else False:
            return 'TCP connection refused: port not listening or instance not accepting connections'
        return f'TCP connection failed: {error}' if error else 'TCP connection failed'

    # 7. Pool warning (not exhausted but approaching limit)
    if pool.get('status') == 'warning':
        util = pool.get('utilization_percent', 'N/A')
        return f'Connection pool near exhaustion: {util}% utilization (warning threshold exceeded)'

    # 8. Parameter group misconfiguration
    params = report.get('parameter_group_findings', {})
    if params.get('status') == 'warning':
        flagged = params.get('flagged_parameters', [])
        if flagged:
            param_name = flagged[0].get('name', 'unknown')
            param_value = flagged[0].get('value', 'unknown')
            return f'Parameter group misconfiguration detected: {param_name}={param_value} is abnormally low'
        return 'Parameter group misconfiguration detected'

    # All phases passed or were skipped
    return 'All checks passed — no connectivity issues detected'


def determine_root_cause(report):
    """Determine root cause category from phase results.

    Maps phase failures to exactly one of the defined root_cause_category
    values. Prioritizes root causes from most fundamental to most specific:
    DNS > instance_state > network > pool_exhaustion > authentication >
    parameter_misconfiguration > unknown.

    Args:
        report: Complete DiagnosticReport dict with all phase results.

    Returns:
        str: One of 'network', 'dns', 'instance_state', 'authentication',
             'pool_exhaustion', 'parameter_misconfiguration', 'unknown'.
    """
    # Priority 1: DNS failure — most fundamental
    dns = report.get('dns_resolution', {})
    if dns.get('status') == 'fail':
        return 'dns'

    # Priority 2: Instance not available
    instance = report.get('instance_state', {})
    if instance.get('status') == 'fail':
        return 'instance_state'

    # Priority 3: Network path blocked
    network = report.get('network_checks', {})
    if network.get('status') == 'fail':
        return 'network'

    # Priority 4: Connection pool exhaustion
    pool = report.get('connection_pool_status', {})
    if pool.get('status') == 'exhausted':
        return 'pool_exhaustion'

    # Priority 5: Authentication failure
    # Check auth phase from connection_test — covers error 1045 and other auth issues
    conn_test = report.get('connection_test', {})
    auth = conn_test.get('auth')
    if auth and not auth.get('success', True):
        error_code = auth.get('error_code')
        # Error 1040 at auth phase means pool exhaustion
        if error_code == 1040:
            return 'pool_exhaustion'
        # Error 1045 or other auth-related failures
        if error_code == 1045:
            return 'authentication'
        # Generic auth failure — check if error message suggests auth issue
        error_msg = str(auth.get('error', '')).lower()
        if any(keyword in error_msg for keyword in ('access denied', 'auth', 'credential', 'password', 'permission')):
            return 'authentication'

    # Priority 6: Parameter group misconfiguration
    params = report.get('parameter_group_findings', {})
    if params.get('status') == 'warning':
        return 'parameter_misconfiguration'

    # Priority 7: TCP failure that didn't match network checks (e.g., network_checks skipped)
    tcp = conn_test.get('tcp', {})
    if not tcp.get('connected', False) and tcp.get('error'):
        error_msg = str(tcp.get('error', '')).lower()
        if 'timeout' in error_msg or 'timed out' in error_msg or 'refused' in error_msg:
            return 'network'

    return 'unknown'


def get_pool_remediation():
    """Return remediation steps for connection pool exhaustion.

    Returns the 5 recommended remediation steps when pool exhaustion
    is detected as the root cause.

    Returns:
        list: List of 5 remediation step strings.
    """
    return [
        'Increase max_connections in the RDS parameter group to a value appropriate for the instance class.',
        'Implement connection pooling using Amazon RDS Proxy to manage and share database connections.',
        'Reduce client concurrency by configuring application connection pool sizes to stay within max_connections limits.',
        'Identify and terminate idle connections using SHOW PROCESSLIST and KILL commands.',
        'Consider upgrading to a larger instance class with higher default max_connections.',
    ]


def run_enhanced_probe(instance_id, endpoint, port, engine):
    """Execute the enhanced multi-layer diagnostic probe.

    Orchestrates all diagnostic phases sequentially, with each phase isolated
    via safe_run() so that a failure in one phase does not prevent others from
    executing. After all phases complete, determines the overall verdict and
    root cause category.

    Maintains backward compatibility with existing instance_id, endpoint, port,
    engine parameters.

    Args:
        instance_id: EC2 instance ID (for network checks).
        endpoint: Target database hostname or IPv4 address.
        port: Database port (1-65535).
        engine: Database engine type ("mysql", "postgresql", or None).

    Returns:
        dict: Complete DiagnosticReport with all phase results, verdict, and
              root cause category.
    """
    report = initialize_report(endpoint, port, engine)

    # Phase 1: DNS Resolution
    report['dns_resolution'] = safe_run(check_dns, endpoint)

    # Phase 2: RDS Instance State
    report['instance_state'] = safe_run(check_instance_state, endpoint)

    # Phase 3: Network Checks (SG, NACL, Routes)
    report['network_checks'] = safe_run(check_network, instance_id, endpoint, port)

    # Phase 4: Connection Test (TCP → TLS → Protocol)
    report['connection_test'] = safe_run(run_connection_test, endpoint, port, engine)

    # Phase 6: Connection Pool Status
    report['connection_pool_status'] = safe_run(check_pool_status, endpoint, port)

    # Phase 7: Parameter Group Analysis
    report['parameter_group_findings'] = safe_run(check_parameters, endpoint)

    # Determine overall verdict and root cause
    report['overall_verdict'] = determine_verdict(report)
    report['root_cause_category'] = determine_root_cause(report)

    # Add remediation steps if pool exhaustion is detected
    if report['root_cause_category'] == 'pool_exhaustion':
        report['remediation_steps'] = get_pool_remediation()

    return report

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


# ---------------------------------------------------------------------------
# Enhanced DB Connectivity Probe Script Template
# ---------------------------------------------------------------------------
# This template embeds the full enhanced multi-layer diagnostic probe that
# performs 6-layer diagnosis. It accepts the same format parameters as the
# legacy script plus {instance_id} for network checks.
#
# Format parameters:
#   {endpoint}    - Target database hostname or IPv4 address
#   {port}        - Database port (1-65535)
#   {engine}      - Database engine type ("mysql", "postgresql", or empty/null)
#   {instance_id} - EC2 instance ID for network checks
# ---------------------------------------------------------------------------

ENHANCED_DB_CONNECTIVITY_PROBE_SCRIPT = '''#!/bin/bash
# GOAT Network Diagnostics - Enhanced DB Connectivity Probe (Multi-Layer)
# This script is injected via SSM RunShellScript and executes entirely in /tmp.
# EXIT trap ensures cleanup regardless of success or failure.

trap "rm -f /tmp/_goat_db_enhanced_probe_$$.py /tmp/_goat_cmd_check_$$" EXIT

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

cat > /tmp/_goat_db_enhanced_probe_$$.py << 'GOAT_PYTHON_SCRIPT_EOF'
import socket
import ssl
import struct
import json
import time
import sys
import os
import datetime

MARKER = "''' + DB_CONNECTIVITY_PROBE_MARKER + '''"
ENDPOINT = "{endpoint}"
PORT = {port}
ENGINE = "{engine}"
INSTANCE_ID = "{instance_id}"

TCP_TIMEOUT = 10


# ---------------------------------------------------------------------------
# Core Framework: safe_run, initialize_report, run_enhanced_probe
# ---------------------------------------------------------------------------

def safe_run(fn, *args):
    """Execute phase function, returning skipped status on failure."""
    try:
        return fn(*args)
    except Exception as e:
        return {{"status": "skipped", "error": str(e)[:512]}}


def initialize_report(endpoint, port, engine):
    """Initialize DiagnosticReport with all required sections."""
    return {{
        "endpoint": endpoint,
        "port": port,
        "engine": engine,
        "probe_timestamp": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "dns_resolution": {{"status": "skipped", "resolved_ips": [], "error": "Not yet executed"}},
        "instance_state": {{"status": "skipped", "error": "Not yet executed"}},
        "network_checks": {{"status": "skipped", "error": "Not yet executed"}},
        "connection_test": {{
            "tcp": {{"connected": False, "error": "Not yet executed"}},
            "tls": None,
            "auth": None,
        }},
        "connection_pool_status": {{"status": "unknown", "error": "Not yet executed"}},
        "parameter_group_findings": {{"status": "skipped", "error": "Not yet executed"}},
        "overall_verdict": "unknown",
        "root_cause_category": "unknown",
    }}


# ---------------------------------------------------------------------------
# Phase Stubs (to be replaced in tasks 3.2–3.8)
# ---------------------------------------------------------------------------

def check_dns(endpoint):
    """Phase 1: DNS Resolution."""
    # Handle empty or None endpoint
    if not endpoint or not endpoint.strip():
        return {{"status": "fail", "resolved_ips": [], "error": "Empty or missing endpoint hostname"}}

    endpoint = endpoint.strip()

    # Check if endpoint is already an IP address (no DNS resolution needed)
    try:
        socket.inet_aton(endpoint)
        return {{"status": "pass", "resolved_ips": [endpoint]}}
    except socket.error:
        pass

    # Also check for IPv6 literal
    try:
        socket.inet_pton(socket.AF_INET6, endpoint)
        return {{"status": "pass", "resolved_ips": [endpoint]}}
    except (socket.error, OSError):
        pass

    # Perform DNS resolution
    try:
        addr_infos = socket.getaddrinfo(endpoint, 0)
        resolved_ips = list(set(
            addr_info[4][0] for addr_info in addr_infos
        ))
        if not resolved_ips:
            return {{"status": "fail", "resolved_ips": [], "error": "DNS resolution returned no addresses"}}
        return {{"status": "pass", "resolved_ips": sorted(resolved_ips)}}
    except socket.gaierror as e:
        return {{"status": "fail", "resolved_ips": [], "error": "DNS resolution failed: {{}}".format(e)}}
    except Exception as e:
        return {{"status": "fail", "resolved_ips": [], "error": "DNS resolution error: {{}}".format(e)}}


def check_instance_state(endpoint):
    """Phase 2: RDS Instance State check via DescribeDBInstances."""
    import re

    # Edge case: endpoint is an IP address — no RDS API lookup possible
    try:
        import ipaddress
        ipaddress.ip_address(endpoint)
        return {{"status": "skipped", "error": "Endpoint is an IP address; cannot determine RDS instance identifier"}}
    except (ValueError, TypeError):
        pass  # Not an IP address, continue with hostname parsing

    # Parse the DB instance identifier from the RDS endpoint hostname
    # Pattern: <instance-id>.<random>.<region>.rds.amazonaws.com
    if not endpoint or not endpoint.lower().endswith(".rds.amazonaws.com"):
        return {{"status": "skipped", "error": "Endpoint does not match RDS hostname pattern (*.rds.amazonaws.com)"}}

    # Extract instance ID from the first subdomain segment
    instance_id = endpoint.split(".")[0]
    if not instance_id:
        return {{"status": "skipped", "error": "Could not extract DB instance identifier from endpoint"}}

    try:
        import boto3
        from botocore.exceptions import ClientError

        rds_client = boto3.client("rds")
        response = rds_client.describe_db_instances(DBInstanceIdentifier=instance_id)

        db_instances = response.get("DBInstances", [])
        if not db_instances:
            return {{"status": "skipped", "error": "No DB instance found with identifier: {{}}".format(instance_id)}}

        db_status = db_instances[0].get("DBInstanceStatus", "unknown")

        if db_status == "available":
            return {{"status": "pass", "db_instance_status": "available"}}
        else:
            return {{
                "status": "fail",
                "db_instance_status": db_status,
                "error": "Instance is not in available state",
            }}

    except ClientError as e:
        error_code = e.response.get("Error", {{}}).get("Code", "")
        if error_code in ("AccessDenied", "AccessDeniedException", "UnauthorizedAccess",
                          "AuthFailure", "InvalidClientTokenId"):
            return {{"status": "skipped", "error": "Insufficient IAM permissions to describe RDS instances: {{}}".format(error_code)}}
        return {{"status": "skipped", "error": "AWS API error: {{}} - {{}}".format(error_code, str(e)[:256])}}
    except ImportError:
        return {{"status": "skipped", "error": "boto3 not available on this instance"}}
    except Exception as e:
        return {{"status": "skipped", "error": "Unexpected error checking instance state: {{}}".format(str(e)[:256])}}


def check_network(instance_id, endpoint, port):
    """Phase 3: Network Checks — SG, NACL, Routes.

    Validates security group rules allow traffic on the target port, analyzes
    NACL rules for blocking entries, and verifies route table connectivity.
    Each sub-check is independent — permission errors on one do not prevent
    others from running.
    """
    import boto3

    result = {{
        "status": "skipped",
        "security_group": {{"allows_port": False}},
        "nacl": {{"allows_traffic": True}},
        "route_table": {{"has_route": False}},
        "error": None,
    }}

    try:
        rds_client = boto3.client("rds")
        ec2_client = boto3.client("ec2")
    except Exception as e:
        result["error"] = "Failed to create boto3 clients: {{}}".format(str(e)[:256])
        return result

    # --- Security Group Check ---
    sg_ids = []
    db_subnet_ids = []
    vpc_id = None
    try:
        db_instances = rds_client.describe_db_instances()
        target_db = None
        for db in db_instances.get("DBInstances", []):
            db_endpoint = db.get("Endpoint", {{}}).get("Address", "")
            if db_endpoint == endpoint:
                target_db = db
                break

        if target_db:
            for sg in target_db.get("VpcSecurityGroups", []):
                sg_ids.append(sg.get("VpcSecurityGroupId"))
            vpc_id = target_db.get("DBSubnetGroup", {{}}).get("VpcId")
            for subnet in target_db.get("DBSubnetGroup", {{}}).get("Subnets", []):
                db_subnet_ids.append(subnet.get("SubnetIdentifier"))

        if sg_ids:
            sg_response = ec2_client.describe_security_groups(GroupIds=sg_ids)
            allows_port = False
            matching_rule_id = None
            for sg in sg_response.get("SecurityGroups", []):
                for rule in sg.get("IpPermissions", []):
                    from_port = rule.get("FromPort", 0)
                    to_port = rule.get("ToPort", 0)
                    ip_protocol = rule.get("IpProtocol", "")
                    if ip_protocol == "-1" or (from_port <= port <= to_port):
                        allows_port = True
                        matching_rule_id = sg.get("GroupId")
                        break
                if allows_port:
                    break
            result["security_group"] = {{
                "allows_port": allows_port,
                "rule_id": matching_rule_id,
            }}
        else:
            result["security_group"] = {{
                "allows_port": False,
                "error": "No security groups found for RDS instance",
            }}
    except Exception as e:
        error_msg = str(e)[:256]
        if "AccessDenied" in error_msg or "UnauthorizedOperation" in error_msg:
            result["security_group"] = {{"allows_port": False, "error": "Permission denied: {{}}".format(error_msg)}}
        else:
            result["security_group"] = {{"allows_port": False, "error": error_msg}}

    # --- NACL Check ---
    try:
        if db_subnet_ids:
            nacl_response = ec2_client.describe_network_acls(
                Filters=[{{"Name": "association.subnet-id", "Values": db_subnet_ids}}]
            )
            blocking_rule = None
            allows_traffic = True
            for nacl in nacl_response.get("NetworkAcls", []):
                inbound_rules = sorted(
                    [e for e in nacl.get("Entries", []) if not e.get("Egress", True)],
                    key=lambda x: x.get("RuleNumber", 32767)
                )
                for entry in inbound_rules:
                    rule_num = entry.get("RuleNumber", 32767)
                    rule_action = entry.get("RuleAction", "allow")
                    protocol = entry.get("Protocol", "-1")
                    port_range = entry.get("PortRange", {{}})
                    from_port = port_range.get("From", 0)
                    to_port = port_range.get("To", 65535)

                    port_matches = (protocol == "-1") or (
                        protocol == "6" and from_port <= port <= to_port
                    )
                    if port_matches:
                        if rule_action == "deny":
                            allows_traffic = False
                            blocking_rule = "Rule {{}}: DENY port {{}}".format(rule_num, port)
                        break

            result["nacl"] = {{
                "allows_traffic": allows_traffic,
                "blocking_rule": blocking_rule,
            }}
        else:
            result["nacl"] = {{"allows_traffic": True, "error": "No DB subnets found for NACL check"}}
    except Exception as e:
        error_msg = str(e)[:256]
        if "AccessDenied" in error_msg or "UnauthorizedOperation" in error_msg:
            result["nacl"] = {{"allows_traffic": True, "error": "Permission denied: {{}}".format(error_msg)}}
        else:
            result["nacl"] = {{"allows_traffic": True, "error": error_msg}}

    # --- Route Table Check ---
    try:
        if db_subnet_ids and vpc_id:
            rt_response = ec2_client.describe_route_tables(
                Filters=[{{"Name": "association.subnet-id", "Values": db_subnet_ids}}]
            )
            route_tables = rt_response.get("RouteTables", [])
            if not route_tables:
                rt_response = ec2_client.describe_route_tables(
                    Filters=[
                        {{"Name": "vpc-id", "Values": [vpc_id]}},
                        {{"Name": "association.main", "Values": ["true"]}},
                    ]
                )
                route_tables = rt_response.get("RouteTables", [])

            has_route = False
            if route_tables:
                for rt in route_tables:
                    for route in rt.get("Routes", []):
                        state = route.get("State", "active")
                        gateway = route.get("GatewayId", "")
                        if state == "active" and gateway == "local":
                            has_route = True
                            break
                    if has_route:
                        break

            result["route_table"] = {{"has_route": has_route}}
        else:
            result["route_table"] = {{"has_route": False, "error": "No subnet/VPC info for route check"}}
    except Exception as e:
        error_msg = str(e)[:256]
        if "AccessDenied" in error_msg or "UnauthorizedOperation" in error_msg:
            result["route_table"] = {{"has_route": False, "error": "Permission denied: {{}}".format(error_msg)}}
        else:
            result["route_table"] = {{"has_route": False, "error": error_msg}}

    # --- Determine overall status ---
    sg_allows = result["security_group"].get("allows_port", False)
    nacl_allows = result["nacl"].get("allows_traffic", True)
    has_route = result["route_table"].get("has_route", False)

    sg_error = result["security_group"].get("error")
    nacl_error = result["nacl"].get("error")
    rt_error = result["route_table"].get("error")

    if not sg_allows and not sg_error:
        result["status"] = "fail"
    elif not nacl_allows:
        result["status"] = "fail"
    elif sg_error and nacl_error and rt_error:
        result["status"] = "skipped"
        result["error"] = "All network checks skipped due to permission errors"
    elif sg_allows and nacl_allows and has_route:
        result["status"] = "pass"
    elif sg_allows and nacl_allows:
        result["status"] = "pass"
    else:
        result["status"] = "skipped"
        result["error"] = "Insufficient data to determine network path status"

    return result


def run_connection_test(endpoint, port, engine):
    """Phase 4: Connection Test — TCP → TLS → Protocol.

    Attempts a layered connection test:
    1. TCP connect to endpoint:port with 10-second timeout
    2. TLS handshake (if TCP succeeds)
    3. Protocol-level authentication detection (if TLS succeeds)

    Captures error codes from connection failures for use by the error
    categorization phase.
    """
    tcp_timeout = 10

    # --- TCP Phase ---
    tcp_result = {{"connected": False, "connect_time_ms": None, "error": None, "error_code": None}}
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(tcp_timeout)
        start = time.time()
        sock.connect((endpoint, port))
        elapsed = (time.time() - start) * 1000
        tcp_result["connected"] = True
        tcp_result["connect_time_ms"] = round(elapsed, 2)
    except OSError as e:
        tcp_result["error"] = str(e)[:512]
        tcp_result["error_code"] = getattr(e, "errno", None)
        return {{"tcp": tcp_result, "tls": None, "auth": None}}

    # --- TLS Phase ---
    tls_result = {{"connected": False, "tls_version": None, "error": None}}
    tls_sock = None
    try:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        tls_sock = context.wrap_socket(sock, server_hostname=endpoint)
        tls_result["connected"] = True
        tls_result["tls_version"] = tls_sock.version()
    except Exception as e:
        tls_result["error"] = str(e)[:512]
        try:
            sock.close()
        except Exception:
            pass
        return {{"tcp": tcp_result, "tls": tls_result, "auth": None}}

    # --- Auth/Protocol Phase ---
    engine_lower = (engine or "").strip().lower()
    if not engine_lower or engine_lower in ("null", "none", ""):
        try:
            tls_sock.close()
        except Exception:
            pass
        return {{"tcp": tcp_result, "tls": tls_result, "auth": None}}

    auth_result = {{"success": False, "details": {{}}, "error": None, "error_code": None}}

    if engine_lower == "mysql":
        auth_result = _auth_phase_mysql(tls_sock)
    elif engine_lower == "postgresql":
        auth_result = _auth_phase_postgresql(tls_sock)
    else:
        auth_result["error"] = "Unsupported engine: {{}}".format(engine)

    try:
        tls_sock.close()
    except Exception:
        pass

    return {{"tcp": tcp_result, "tls": tls_result, "auth": auth_result}}


def _auth_phase_mysql(tls_sock):
    """Read MySQL initial handshake packet to detect protocol-level connectivity."""
    result = {{"success": False, "details": {{}}, "error": None, "error_code": None}}
    try:
        tls_sock.settimeout(5)
        # Read packet header: 3 bytes length + 1 byte sequence id
        header = b""
        while len(header) < 4:
            chunk = tls_sock.recv(4 - len(header))
            if not chunk:
                result["error"] = "Connection closed before handshake"
                return result
            header += chunk

        payload_length = struct.unpack("<I", header[:3] + b"\\x00")[0]

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

        # Check if this is an error packet (first byte == 0xFF)
        if payload[0] == 0xFF:
            # Error packet: 2-byte error code follows
            if len(payload) >= 3:
                error_code = struct.unpack("<H", payload[1:3])[0]
                result["error_code"] = error_code
                msg_start = 3
                if len(payload) > 4 and payload[3:4] == b"#":
                    msg_start = 9  # Skip sql_state marker
                error_msg = payload[msg_start:].decode("ascii", errors="replace")
                result["error"] = "MySQL error {{}}: {{}}".format(error_code, error_msg)
            else:
                result["error"] = "MySQL error packet (too short to decode)"
            return result

        protocol_version = payload[0]
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


def _auth_phase_postgresql(tls_sock):
    """Send PostgreSQL StartupMessage and read response."""
    result = {{"success": False, "details": {{}}, "error": None, "error_code": None}}
    try:
        tls_sock.settimeout(5)
        # Build StartupMessage: version 3.0, user=goat_probe
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
            try:
                length_bytes = b""
                while len(length_bytes) < 4:
                    chunk = tls_sock.recv(4 - len(length_bytes))
                    if not chunk:
                        break
                    length_bytes += chunk
                if len(length_bytes) == 4:
                    body_len = struct.unpack("!I", length_bytes)[0] - 4
                    body = b""
                    while len(body) < body_len:
                        chunk = tls_sock.recv(min(body_len - len(body), 4096))
                        if not chunk:
                            break
                        body += chunk
                    error_msg = body.decode("ascii", errors="replace")
                    result["error"] = "PostgreSQL error: {{}}".format(error_msg[:256])
            except Exception:
                pass
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


# ---------------------------------------------------------------------------
# Phase 5: Error Categorization
# ---------------------------------------------------------------------------

ERROR_CATEGORIES = {{
    # MySQL error codes -> categories
    1040: "pool_exhaustion",      # Too many connections
    1045: "authentication",       # Access denied
    2003: "network_timeout",      # Can't connect to server
    2002: "connection_refused",   # Connection refused (socket)
    2005: "dns_failure",          # Unknown host
    2006: "connection_lost",      # Server has gone away
    2013: "network_timeout",      # Lost connection during query
}}


def categorize_error(error_code, error_message):
    """Map MySQL error to diagnostic category.

    Categorizes MySQL connection errors into one of the defined diagnostic
    categories based on error code lookup, with fallback pattern matching
    on the error message for codes not in the known mapping.

    Always returns exactly one valid category string - never None or empty.

    Args:
        error_code: MySQL error code (integer).
        error_message: Human-readable error message string.

    Returns:
        str: One of 'pool_exhaustion', 'authentication', 'network_timeout',
             'connection_refused', 'dns_failure', 'connection_lost', 'unknown'.
    """
    # Direct error code lookup
    if error_code in ERROR_CATEGORIES:
        return ERROR_CATEGORIES[error_code]

    # Fallback: pattern matching on error message
    msg_lower = error_message.lower() if error_message else ""
    if "timeout" in msg_lower:
        return "network_timeout"
    if "refused" in msg_lower:
        return "connection_refused"

    return "unknown"


# ---------------------------------------------------------------------------
# Phase 6: Connection Pool Status
# ---------------------------------------------------------------------------

EXHAUSTION_THRESHOLD = 0.90  # 90% utilization


def detect_pool_exhaustion(threads_connected, max_connections):
    """Determine pool health from MySQL status values."""
    if max_connections <= 0:
        return {{"status": "unknown", "error": "Invalid max_connections value"}}

    utilization = threads_connected / max_connections

    if utilization >= 1.0:
        status = "exhausted"
    elif utilization >= EXHAUSTION_THRESHOLD:
        status = "warning"
    else:
        status = "healthy"

    return {{
        "status": status,
        "threads_connected": threads_connected,
        "max_connections": max_connections,
        "utilization_percent": round(utilization * 100, 1),
    }}


def check_pool_status(endpoint, port):
    """Phase 6: Connection Pool Status.

    Attempts a MySQL connection to check pool utilization:
    - On success: queries SHOW STATUS and SHOW GLOBAL VARIABLES
    - On error 1040: reports pool exhaustion directly
    - On timeout: reports as network issue, skips pool status
    """
    try:
        import pymysql
    except ImportError:
        # pymysql not available - try socket-level approach
        return _check_pool_status_socket(endpoint, port)

    try:
        conn = pymysql.connect(
            host=endpoint,
            port=int(port),
            user="admin",
            database="information_schema",
            connect_timeout=5,
            read_timeout=5,
        )
    except Exception as e:
        error_code = getattr(e, "args", (None,))[0] if hasattr(e, "args") and e.args else None
        error_msg = str(e)

        # Error 1040: Too many connections
        if error_code == 1040:
            return {{
                "status": "exhausted",
                "error": "Too many connections (error 1040)",
            }}

        # Timeout errors
        if "timeout" in error_msg.lower() or "timed out" in error_msg.lower():
            return {{
                "status": "unknown",
                "error": "Connection timed out \u2014 likely network issue",
            }}

        # Other connection errors
        return {{
            "status": "unknown",
            "error": "Connection failed: {{}}".format(error_msg[:256]),
        }}

    # Connection succeeded - query pool metrics
    try:
        cursor = conn.cursor()

        cursor.execute("SHOW STATUS LIKE 'Threads_connected'")
        row = cursor.fetchone()
        threads_connected = int(row[1]) if row else 0

        cursor.execute("SHOW GLOBAL VARIABLES LIKE 'max_connections'")
        row = cursor.fetchone()
        max_connections = int(row[1]) if row else 0

        cursor.close()
        conn.close()

        return detect_pool_exhaustion(threads_connected, max_connections)

    except Exception as e:
        try:
            conn.close()
        except Exception:
            pass
        return {{
            "status": "unknown",
            "error": "Failed to query pool status: {{}}".format(str(e)[:256]),
        }}


def _check_pool_status_socket(endpoint, port):
    """Fallback pool status check using raw socket when pymysql is unavailable."""
    import struct

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect((endpoint, int(port)))
    except socket.timeout:
        return {{
            "status": "unknown",
            "error": "Connection timed out \u2014 likely network issue",
        }}
    except Exception as e:
        error_msg = str(e)
        if "timed out" in error_msg.lower() or "timeout" in error_msg.lower():
            return {{
                "status": "unknown",
                "error": "Connection timed out \u2014 likely network issue",
            }}
        return {{
            "status": "unknown",
            "error": "Socket connection failed: {{}}".format(error_msg[:256]),
        }}

    # Read MySQL initial handshake or error packet
    try:
        sock.settimeout(5)
        header = b""
        while len(header) < 4:
            chunk = sock.recv(4 - len(header))
            if not chunk:
                break
            header += chunk

        if len(header) < 4:
            sock.close()
            return {{"status": "unknown", "error": "Incomplete MySQL packet header"}}

        payload_length = struct.unpack("<I", header[:3] + b"\x00")[0]

        payload = b""
        while len(payload) < payload_length:
            chunk = sock.recv(payload_length - len(payload))
            if not chunk:
                break
            payload += chunk

        sock.close()

        if not payload:
            return {{"status": "unknown", "error": "Empty MySQL packet payload"}}

        # Check if error packet (first byte = 0xFF)
        if payload[0] == 0xFF and len(payload) >= 3:
            error_code = struct.unpack("<H", payload[1:3])[0]
            if error_code == 1040:
                return {{
                    "status": "exhausted",
                    "error": "Too many connections (error 1040)",
                }}
            return {{
                "status": "unknown",
                "error": "MySQL error {{}} during handshake".format(error_code),
            }}

        # Server sent handshake - cannot query without full auth
        return {{
            "status": "unknown",
            "error": "pymysql not available; cannot query pool metrics (connection succeeded at TCP level)",
        }}

    except socket.timeout:
        try:
            sock.close()
        except Exception:
            pass
        return {{
            "status": "unknown",
            "error": "Connection timed out reading MySQL handshake",
        }}
    except Exception as e:
        try:
            sock.close()
        except Exception:
            pass
        return {{
            "status": "unknown",
            "error": "Error reading MySQL handshake: {{}}".format(str(e)[:256]),
        }}


# ---------------------------------------------------------------------------
# Phase 7: Parameter Group Analysis
# ---------------------------------------------------------------------------

LOW_MAX_CONNECTIONS_THRESHOLD = 50


def flag_parameter_issues(max_connections, instance_class):
    """Flag abnormally low parameter values."""
    findings = []

    if max_connections < LOW_MAX_CONNECTIONS_THRESHOLD:
        findings.append({{
            "name": "max_connections",
            "value": str(max_connections),
            "issue": f"Abnormally low max_connections ({{max_connections}}) for instance class {{instance_class}}. "
                     f"Default for this class is typically much higher. "
                     f"This severely limits concurrent client connections.",
        }})

    return findings


def check_parameters(endpoint):
    """Phase 7: Parameter Group Analysis."""
    import re

    # Skip if endpoint is empty
    if not endpoint or not endpoint.strip():
        return {{"status": "skipped", "parameter_group_name": None, "flagged_parameters": [], "error": "Empty endpoint"}}

    endpoint = endpoint.strip()

    # Check if endpoint is an IP address — skip parameter analysis
    try:
        socket.inet_aton(endpoint)
        return {{"status": "skipped", "parameter_group_name": None, "flagged_parameters": [], "error": "Endpoint is an IP address, not an RDS hostname"}}
    except socket.error:
        pass  # Not an IP address, continue

    # Extract DB instance identifier from RDS endpoint hostname
    match = re.match(r"^([^.]+)\.", endpoint)
    if not match:
        return {{"status": "skipped", "parameter_group_name": None, "flagged_parameters": [], "error": "Could not extract DB instance identifier from endpoint"}}

    db_instance_id = match.group(1)

    try:
        import boto3
    except ImportError:
        return {{"status": "skipped", "parameter_group_name": None, "flagged_parameters": [], "error": "boto3 not available"}}

    try:
        rds_client = boto3.client("rds")

        # Get DB instance details
        response = rds_client.describe_db_instances(DBInstanceIdentifier=db_instance_id)
        db_instances = response.get("DBInstances", [])
        if not db_instances:
            return {{"status": "skipped", "parameter_group_name": None, "flagged_parameters": [], "error": f"No DB instance found with identifier {{db_instance_id}}"}}

        db_instance = db_instances[0]
        instance_class = db_instance.get("DBInstanceClass", "unknown")

        # Get parameter group name
        param_groups = db_instance.get("DBParameterGroups", [])
        if not param_groups:
            return {{"status": "skipped", "parameter_group_name": None, "flagged_parameters": [], "error": "No parameter group associated with instance"}}

        param_group_name = param_groups[0].get("DBParameterGroupName", "")

        # Query parameter group for max_connections
        max_connections = None
        try:
            paginator = rds_client.get_paginator("describe_db_parameters")
            for page in paginator.paginate(DBParameterGroupName=param_group_name):
                for param in page.get("Parameters", []):
                    if param.get("ParameterName") == "max_connections":
                        param_value = param.get("ParameterValue")
                        if param_value is not None:
                            try:
                                max_connections = int(param_value)
                            except (ValueError, TypeError):
                                pass
                        break
                if max_connections is not None:
                    break
        except Exception as e:
            return {{
                "status": "skipped",
                "parameter_group_name": param_group_name,
                "flagged_parameters": [],
                "error": f"Could not retrieve parameters: {{str(e)[:256]}}"
            }}

        # If max_connections not found or not set explicitly, skip flagging
        if max_connections is None:
            return {{
                "status": "ok",
                "parameter_group_name": param_group_name,
                "flagged_parameters": [],
                "error": None
            }}

        # Flag parameter issues
        flagged = flag_parameter_issues(max_connections, instance_class)

        return {{
            "status": "warning" if flagged else "ok",
            "parameter_group_name": param_group_name,
            "flagged_parameters": flagged,
            "error": None
        }}

    except Exception as e:
        error_msg = str(e)
        # Check for permission-related errors
        if "AccessDenied" in error_msg or "UnauthorizedAccess" in error_msg or "not authorized" in error_msg.lower():
            return {{"status": "skipped", "parameter_group_name": None, "flagged_parameters": [], "error": f"Insufficient permissions: {{error_msg[:256]}}"}}
        return {{"status": "skipped", "parameter_group_name": None, "flagged_parameters": [], "error": f"Error querying RDS: {{error_msg[:256]}}"}}


# ---------------------------------------------------------------------------
# Verdict and Root Cause Determination
# ---------------------------------------------------------------------------

def determine_verdict(report):
    """Determine overall diagnostic verdict from phase results."""
    # Check for failures in severity order (most fundamental first)

    # 1. DNS — nothing else works if name resolution fails
    dns = report.get("dns_resolution", {{}})
    if dns.get("status") == "fail":
        error = dns.get("error", "")
        return f"DNS resolution failed for endpoint: {{error}}" if error else "DNS resolution failed"

    # 2. Instance state — instance must be available
    instance = report.get("instance_state", {{}})
    if instance.get("status") == "fail":
        db_status = instance.get("db_instance_status", "unknown")
        return f"RDS instance not available (status: {{db_status}})"

    # 3. Network path — SG, NACL, routes must allow traffic
    network = report.get("network_checks", {{}})
    if network.get("status") == "fail":
        sg = network.get("security_group", {{}})
        nacl = network.get("nacl", {{}})
        if not sg.get("allows_port", True):
            return "Network path blocked: security group denies traffic on target port"
        if not nacl.get("allows_traffic", True):
            blocking = nacl.get("blocking_rule", "unknown rule")
            return f"Network path blocked: NACL denies traffic ({{blocking}})"
        return "Network path blocked"

    # 4. Connection pool — exhaustion prevents new connections
    pool = report.get("connection_pool_status", {{}})
    if pool.get("status") == "exhausted":
        threads = pool.get("threads_connected")
        max_conn = pool.get("max_connections")
        if threads is not None and max_conn is not None:
            return f"Connection pool exhausted: {{threads}}/{{max_conn}} connections in use (100% utilization)"
        return "Connection pool exhausted: all available connections are in use"

    # 5. Authentication — credentials or access issue
    conn_test = report.get("connection_test", {{}})
    auth = conn_test.get("auth")
    if auth and not auth.get("success", True):
        error_code = auth.get("error_code")
        if error_code == 1040:
            return "Connection pool exhausted: MySQL error 1040 (Too many connections)"
        if error_code == 1045:
            return "Authentication failed: access denied for user"
        error_msg = auth.get("error", "")
        return f"Authentication/protocol error: {{error_msg}}" if error_msg else "Authentication failed"

    # 6. TCP connection failure (not covered by above)
    tcp = conn_test.get("tcp", {{}})
    if not tcp.get("connected", False):
        error = tcp.get("error", "")
        if ("timeout" in error.lower() or "timed out" in error.lower()) if error else False:
            return "TCP connection timed out: host unreachable or port blocked"
        if "refused" in error.lower() if error else False:
            return "TCP connection refused: port not listening or instance not accepting connections"
        return f"TCP connection failed: {{error}}" if error else "TCP connection failed"

    # 7. Pool warning (not exhausted but approaching limit)
    if pool.get("status") == "warning":
        util = pool.get("utilization_percent", "N/A")
        return f"Connection pool near exhaustion: {{util}}% utilization (warning threshold exceeded)"

    # 8. Parameter group misconfiguration
    params = report.get("parameter_group_findings", {{}})
    if params.get("status") == "warning":
        flagged = params.get("flagged_parameters", [])
        if flagged:
            param_name = flagged[0].get("name", "unknown")
            param_value = flagged[0].get("value", "unknown")
            return f"Parameter group misconfiguration detected: {{param_name}}={{param_value}} is abnormally low"
        return "Parameter group misconfiguration detected"

    # All phases passed or were skipped
    return "All checks passed — no connectivity issues detected"


def determine_root_cause(report):
    """Determine root cause category from phase results."""
    # Priority 1: DNS failure — most fundamental
    dns = report.get("dns_resolution", {{}})
    if dns.get("status") == "fail":
        return "dns"

    # Priority 2: Instance not available
    instance = report.get("instance_state", {{}})
    if instance.get("status") == "fail":
        return "instance_state"

    # Priority 3: Network path blocked
    network = report.get("network_checks", {{}})
    if network.get("status") == "fail":
        return "network"

    # Priority 4: Connection pool exhaustion
    pool = report.get("connection_pool_status", {{}})
    if pool.get("status") == "exhausted":
        return "pool_exhaustion"

    # Priority 5: Authentication failure
    conn_test = report.get("connection_test", {{}})
    auth = conn_test.get("auth")
    if auth and not auth.get("success", True):
        error_code = auth.get("error_code")
        if error_code == 1040:
            return "pool_exhaustion"
        if error_code == 1045:
            return "authentication"
        error_msg = str(auth.get("error", "")).lower()
        if any(keyword in error_msg for keyword in ("access denied", "auth", "credential", "password", "permission")):
            return "authentication"

    # Priority 6: Parameter group misconfiguration
    params = report.get("parameter_group_findings", {{}})
    if params.get("status") == "warning":
        return "parameter_misconfiguration"

    # Priority 7: TCP failure that didn't match network checks
    tcp = conn_test.get("tcp", {{}})
    if not tcp.get("connected", False) and tcp.get("error"):
        error_msg = str(tcp.get("error", "")).lower()
        if "timeout" in error_msg or "timed out" in error_msg or "refused" in error_msg:
            return "network"

    return "unknown"


def get_pool_remediation():
    """Return remediation steps for connection pool exhaustion."""
    return [
        "Increase max_connections in the RDS parameter group to a value appropriate for the instance class.",
        "Implement connection pooling using Amazon RDS Proxy to manage and share database connections.",
        "Reduce client concurrency by configuring application connection pool sizes to stay within max_connections limits.",
        "Identify and terminate idle connections using SHOW PROCESSLIST and KILL commands.",
        "Consider upgrading to a larger instance class with higher default max_connections.",
    ]


# ---------------------------------------------------------------------------
# Enhanced Probe Orchestrator
# ---------------------------------------------------------------------------

def run_enhanced_probe():
    """Execute the enhanced multi-layer diagnostic probe."""
    endpoint = ENDPOINT
    port = PORT
    engine = ENGINE if ENGINE and ENGINE.lower() not in ("", "null", "none") else None
    instance_id = INSTANCE_ID if INSTANCE_ID and INSTANCE_ID.lower() not in ("", "null", "none") else None

    report = initialize_report(endpoint, port, engine)

    # Execute each phase with error isolation
    report["dns_resolution"] = safe_run(check_dns, endpoint)
    report["instance_state"] = safe_run(check_instance_state, endpoint)
    report["network_checks"] = safe_run(check_network, instance_id, endpoint, port)
    report["connection_test"] = safe_run(run_connection_test, endpoint, port, engine)
    report["connection_pool_status"] = safe_run(check_pool_status, endpoint, port)
    report["parameter_group_findings"] = safe_run(check_parameters, endpoint)

    # Determine overall verdict and root cause
    report["overall_verdict"] = determine_verdict(report)
    report["root_cause_category"] = determine_root_cause(report)

    # Add remediation steps if pool exhaustion is detected
    if report["root_cause_category"] == "pool_exhaustion":
        report["remediation_steps"] = get_pool_remediation()

    return report


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    try:
        result = run_enhanced_probe()
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
"$PYTHON_BIN" /tmp/_goat_db_enhanced_probe_$$.py
'''
