/**
 * Tool Manifest Generator for the GOAT Network Agent ↔ DevOps Agent Integration.
 *
 * Produces the tool manifest JSON from the action schema registry, conforming
 * to the DevOps Agent external tool specification format.
 *
 * Requirements: 2.2, 2.3
 */

import { ACTION_SCHEMAS, getActionNames } from "../schemas/action-schemas";
import type { ToolManifest, ActionDefinition, JSONSchema } from "../types/index";

// ─── Action Description Map ─────────────────────────────────────────────────

/**
 * Human-readable descriptions for each action in the registry.
 * Used when generating the tool manifest for DevOps Agent discovery.
 */
const ACTION_DESCRIPTIONS: Record<string, string> = {
  list_enis:
    "List Elastic Network Interfaces (ENIs) in a VPC, optionally filtered by subnet or status.",
  reverse_dns_lookup:
    "Perform reverse DNS lookups on a list of IP addresses to resolve hostnames.",
  list_captures:
    "List traffic capture sessions, optionally filtered by status.",
  get_capture_progress:
    "Get the progress and current status of an active capture session.",
  cleanup_orphaned_sessions:
    "Clean up orphaned or stale capture sessions that exceed a specified age.",
  start_capture:
    "Start a packet capture session on one or more ENIs with optional duration and filtering.",
  stop_capture:
    "Stop an active packet capture session and retrieve capture metadata.",
  transform_capture:
    "Transform raw pcap capture data into a queryable format (parquet, JSON, or CSV).",
  query_pcap:
    "Execute a SQL-like query against transformed pcap data for a capture session.",
  search_fragmented_packets:
    "Search for IP-fragmented packets in a capture session, identifying reassembly issues.",
  correlate_tcp_streams:
    "Correlate and summarize TCP streams in a capture, optionally filtered by host or port.",
  detect_retransmissions:
    "Detect TCP retransmissions in a capture and calculate retransmission rates per stream.",
  check_tls_hello_size:
    "Identify oversized TLS Client Hello messages that may cause fragmentation issues.",
  get_conversation_stats:
    "Get top network conversations by traffic volume from a capture session.",
  reconstruct_tcp_handshake:
    "Reconstruct and analyze TCP three-way handshakes to identify connection setup failures.",
  classify_tcp_resets:
    "Classify TCP RST packets by origin (client, server, middlebox) and timing.",
  detect_out_of_order_packets:
    "Detect out-of-order TCP packets indicating network path issues or congestion.",
  detect_zero_window:
    "Detect TCP zero-window events indicating receiver buffer exhaustion.",
  analyze_tcp_options:
    "Analyze TCP options (MSS, window scale, SACK, timestamps) and detect option mismatches between peers.",
  get_rtt_distribution:
    "Calculate round-trip time distribution statistics from TCP acknowledgments.",
  get_request_response_latency:
    "Measure application-layer request/response latency for HTTP, HTTPS, or DNS traffic.",
  diagnose_tcp_stream:
    "Run a comprehensive diagnosis on TCP streams to identify issues, calculate health scores, and recommend fixes.",
  full_diagnostic:
    "Execute a complete network diagnostic workflow: capture traffic, transform data, and analyze results in a single operation.",
};

// ─── Manifest Generation ────────────────────────────────────────────────────

/**
 * Generate the complete tool manifest from the action schema registry.
 *
 * Reads all 23 actions from ACTION_SCHEMAS, transforms them into ActionDefinition[]
 * format, and returns a ToolManifest conforming to the DevOps Agent external tool
 * specification.
 *
 * @returns Complete ToolManifest object with all registered actions
 */
export function generateToolManifest(): ToolManifest {
  const actionNames = getActionNames();

  const actions: ActionDefinition[] = actionNames.map((name) => {
    const schema = ACTION_SCHEMAS[name];
    return {
      name,
      description: ACTION_DESCRIPTIONS[name] ?? `Execute the ${name} action.`,
      input_schema: schema.input as JSONSchema,
      output_schema: schema.output as JSONSchema,
      category: schema.category,
      requires_authorization: schema.requiresAuth,
    };
  });

  return {
    tool_name: "goat-network-agent",
    version: "1.0.0",
    description:
      "GOAT Network Agent provides packet-level L7 analysis (traffic capture, pcap analysis, TLS handshake inspection) that complements VPC Reachability Analyzer's L3/L4 path analysis scope.",
    capabilities: [
      "traffic_capture",
      "pcap_analysis",
      "tls_inspection",
      "tcp_diagnostics",
      "network_troubleshooting",
    ],
    actions,
  };
}

/**
 * Generate a tool manifest from an arbitrary set of action definitions.
 *
 * This function enables the Agent Integration Template to produce manifests
 * for any GOAT sub-agent without depending on the Network Agent's specific
 * action registry.
 *
 * @param actions - Array of ActionDefinition objects to include in the manifest
 * @returns ToolManifest containing the provided actions
 */
export function generateManifestFromActions(actions: ActionDefinition[]): ToolManifest {
  return {
    tool_name: "goat-network-agent",
    version: "1.0.0",
    description:
      "GOAT Network Agent provides packet-level L7 analysis (traffic capture, pcap analysis, TLS handshake inspection) that complements VPC Reachability Analyzer's L3/L4 path analysis scope.",
    capabilities: [
      "traffic_capture",
      "pcap_analysis",
      "tls_inspection",
      "tcp_diagnostics",
      "network_troubleshooting",
    ],
    actions,
  };
}
