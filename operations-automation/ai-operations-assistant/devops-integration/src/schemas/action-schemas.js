"use strict";
/**
 * Action Schema Registry for the GOAT Network Agent ↔ DevOps Agent Integration.
 *
 * Defines JSON Schema (draft-07 compatible) definitions for all 28 Network Agent
 * actions plus the composite `full_diagnostic` action. Each entry includes input/output
 * schemas, category classification, and authorization requirements.
 *
 * This registry is the single source of truth for:
 * - Request payload validation (via ajv)
 * - Tool manifest generation
 * - DevOps Agent action discovery
 *
 * Requirements: 1.1, 1.3, 4.3, 4.7, 2.1, 2.2, 2.3, 2.4, 2.5
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.ACTION_SCHEMAS = void 0;
exports.getActionSchema = getActionSchema;
exports.getActionNames = getActionNames;
exports.getActionsByCategory = getActionsByCategory;
exports.getAuthRequiredActions = getAuthRequiredActions;
exports.isValidAction = isValidAction;
// ─── Shared Schema Fragments ────────────────────────────────────────────────
const eniIdsSchema = {
    type: "array",
    items: { type: "string", pattern: "^eni-[a-f0-9]{8,17}$", description: "ENI identifier" },
    minItems: 1,
    maxItems: 5,
    description: "List of ENI IDs to target (1-5)",
};
const captureIdSchema = {
    type: "string",
    minLength: 1,
    description: "Capture session identifier",
};
const targetHostSchema = {
    type: "string",
    description: "Optional target host for filtering (IP or hostname)",
};
const analysisFocusSchema = {
    type: "string",
    enum: ["tls", "tcp_health", "dns", "general"],
    default: "general",
    description: "Analysis focus area",
};
const durationMinutesSchema = {
    type: "integer",
    minimum: 1,
    maximum: 10,
    default: 2,
    description: "Capture duration in minutes (1-10)",
};
const timestampSchema = {
    type: "string",
    format: "date-time",
    description: "ISO 8601 timestamp",
};
const statusResponseSchema = {
    type: "object",
    properties: {
        status: { type: "string", enum: ["success", "error"] },
        message: { type: "string", description: "Human-readable status message" },
    },
    required: ["status", "message"],
};
// ─── Action Schema Registry ─────────────────────────────────────────────────
exports.ACTION_SCHEMAS = {
    // ── Utility Actions ─────────────────────────────────────────────────────────
    list_enis: {
        input: {
            type: "object",
            properties: {
                vpc_id: { type: "string", pattern: "^vpc-[a-f0-9]{8,17}$", description: "VPC ID to filter ENIs" },
                filters: {
                    type: "object",
                    properties: {
                        subnet_id: { type: "string", description: "Subnet ID filter" },
                        status: { type: "string", enum: ["available", "in-use", "associated"], description: "ENI status filter" },
                    },
                    additionalProperties: false,
                },
            },
            additionalProperties: false,
        },
        output: {
            type: "object",
            properties: {
                enis: {
                    type: "array",
                    items: {
                        type: "object",
                        properties: {
                            eni_id: { type: "string" },
                            description: { type: "string" },
                            status: { type: "string" },
                            private_ip: { type: "string" },
                            subnet_id: { type: "string" },
                            vpc_id: { type: "string" },
                        },
                        required: ["eni_id", "status"],
                    },
                },
                count: { type: "integer" },
            },
            required: ["enis", "count"],
        },
        category: "utility",
        requiresAuth: false,
    },
    reverse_dns_lookup: {
        input: {
            type: "object",
            properties: {
                ip_addresses: {
                    type: "array",
                    items: { type: "string", description: "IPv4 or IPv6 address" },
                    minItems: 1,
                    maxItems: 20,
                    description: "IP addresses to resolve",
                },
            },
            required: ["ip_addresses"],
            additionalProperties: false,
        },
        output: {
            type: "object",
            properties: {
                results: {
                    type: "array",
                    items: {
                        type: "object",
                        properties: {
                            ip: { type: "string" },
                            hostname: { type: "string" },
                            resolved: { type: "boolean" },
                        },
                        required: ["ip", "resolved"],
                    },
                },
            },
            required: ["results"],
        },
        category: "utility",
        requiresAuth: false,
    },
    list_captures: {
        input: {
            type: "object",
            properties: {
                status: { type: "string", enum: ["active", "completed", "failed", "all"], description: "Filter by capture status" },
                limit: { type: "integer", minimum: 1, maximum: 100, default: 20, description: "Maximum results to return" },
            },
            additionalProperties: false,
        },
        output: {
            type: "object",
            properties: {
                captures: {
                    type: "array",
                    items: {
                        type: "object",
                        properties: {
                            capture_id: { type: "string" },
                            status: { type: "string" },
                            eni_ids: { type: "array", items: { type: "string" } },
                            start_time: timestampSchema,
                            end_time: timestampSchema,
                            duration_minutes: { type: "number" },
                        },
                        required: ["capture_id", "status"],
                    },
                },
                total_count: { type: "integer" },
            },
            required: ["captures", "total_count"],
        },
        category: "utility",
        requiresAuth: false,
    },
    get_capture_progress: {
        input: {
            type: "object",
            properties: {
                capture_id: captureIdSchema,
            },
            required: ["capture_id"],
            additionalProperties: false,
        },
        output: {
            type: "object",
            properties: {
                capture_id: { type: "string" },
                status: { type: "string", enum: ["initializing", "capturing", "stopping", "completed", "failed"] },
                progress_percent: { type: "number", minimum: 0, maximum: 100 },
                elapsed_seconds: { type: "number" },
                packets_captured: { type: "integer" },
                bytes_captured: { type: "integer" },
            },
            required: ["capture_id", "status", "progress_percent"],
        },
        category: "utility",
        requiresAuth: false,
    },
    cleanup_orphaned_sessions: {
        input: {
            type: "object",
            properties: {
                max_age_hours: { type: "integer", minimum: 1, maximum: 168, default: 24, description: "Max session age in hours before cleanup" },
                dry_run: { type: "boolean", default: false, description: "Preview cleanup without deleting" },
            },
            additionalProperties: false,
        },
        output: {
            type: "object",
            properties: {
                cleaned_sessions: { type: "integer", description: "Number of sessions cleaned up" },
                sessions: {
                    type: "array",
                    items: {
                        type: "object",
                        properties: {
                            capture_id: { type: "string" },
                            age_hours: { type: "number" },
                            status: { type: "string" },
                        },
                        required: ["capture_id", "age_hours"],
                    },
                },
                dry_run: { type: "boolean" },
            },
            required: ["cleaned_sessions", "dry_run"],
        },
        category: "utility",
        requiresAuth: true,
    },
    // ── Capture Actions ─────────────────────────────────────────────────────────
    start_capture: {
        input: {
            type: "object",
            properties: {
                eni_ids: eniIdsSchema,
                duration_minutes: durationMinutesSchema,
                target_host: targetHostSchema,
                filter_expression: { type: "string", description: "BPF-style packet filter expression" },
            },
            required: ["eni_ids"],
            additionalProperties: false,
        },
        output: {
            type: "object",
            properties: {
                capture_id: { type: "string", description: "Unique capture session identifier" },
                status: { type: "string", enum: ["initializing", "capturing"] },
                eni_ids: { type: "array", items: { type: "string" } },
                start_time: timestampSchema,
                expected_end_time: timestampSchema,
            },
            required: ["capture_id", "status", "eni_ids", "start_time"],
        },
        category: "capture",
        requiresAuth: true,
    },
    stop_capture: {
        input: {
            type: "object",
            properties: {
                capture_id: captureIdSchema,
            },
            required: ["capture_id"],
            additionalProperties: false,
        },
        output: {
            type: "object",
            properties: {
                capture_id: { type: "string" },
                status: { type: "string", enum: ["completed", "failed"] },
                end_time: timestampSchema,
                packets_captured: { type: "integer" },
                bytes_captured: { type: "integer" },
                pcap_location: { type: "string", description: "S3 URI of captured pcap data" },
            },
            required: ["capture_id", "status", "end_time"],
        },
        category: "capture",
        requiresAuth: true,
    },
    transform_capture: {
        input: {
            type: "object",
            properties: {
                capture_id: captureIdSchema,
                output_format: { type: "string", enum: ["parquet", "json", "csv"], default: "parquet", description: "Transformation output format" },
            },
            required: ["capture_id"],
            additionalProperties: false,
        },
        output: {
            type: "object",
            properties: {
                capture_id: { type: "string" },
                transformation_id: { type: "string" },
                status: { type: "string", enum: ["transforming", "completed", "failed"] },
                output_location: { type: "string", description: "S3 URI of transformed data" },
                records_processed: { type: "integer" },
            },
            required: ["capture_id", "transformation_id", "status"],
        },
        category: "capture",
        requiresAuth: true,
    },
    // ── Analysis Actions ────────────────────────────────────────────────────────
    query_pcap: {
        input: {
            type: "object",
            properties: {
                capture_id: captureIdSchema,
                sql: { type: "string", description: "SQL query against pcap_logs table (e.g., SELECT * FROM pcap_logs WHERE dst_port = 443 LIMIT 10)" },
                limit: { type: "integer", minimum: 1, maximum: 1000, default: 100 },
            },
            required: ["capture_id", "sql"],
            additionalProperties: false,
        },
        output: {
            type: "object",
            properties: {
                rows: { type: "array", items: { type: "object" } },
                row_count: { type: "integer" },
                query_execution_ms: { type: "number" },
            },
            required: ["rows", "row_count"],
        },
        category: "analysis",
        requiresAuth: false,
    },
    search_fragmented_packets: {
        input: {
            type: "object",
            properties: {
                capture_id: captureIdSchema,
                target_host: targetHostSchema,
                min_fragment_count: { type: "integer", minimum: 2, default: 2, description: "Minimum fragments per packet to flag" },
            },
            required: ["capture_id"],
            additionalProperties: false,
        },
        output: {
            type: "object",
            properties: {
                fragmented_packets: {
                    type: "array",
                    items: {
                        type: "object",
                        properties: {
                            packet_id: { type: "string" },
                            fragment_count: { type: "integer" },
                            total_size: { type: "integer" },
                            source_ip: { type: "string" },
                            dest_ip: { type: "string" },
                            protocol: { type: "string" },
                        },
                        required: ["packet_id", "fragment_count"],
                    },
                },
                total_fragmented: { type: "integer" },
            },
            required: ["fragmented_packets", "total_fragmented"],
        },
        category: "analysis",
        requiresAuth: false,
    },
    correlate_tcp_streams: {
        input: {
            type: "object",
            properties: {
                capture_id: captureIdSchema,
                target_host: targetHostSchema,
                port: { type: "integer", minimum: 1, maximum: 65535, description: "TCP port filter" },
            },
            required: ["capture_id"],
            additionalProperties: false,
        },
        output: {
            type: "object",
            properties: {
                streams: {
                    type: "array",
                    items: {
                        type: "object",
                        properties: {
                            stream_id: { type: "string" },
                            source: { type: "string" },
                            destination: { type: "string" },
                            packets: { type: "integer" },
                            bytes: { type: "integer" },
                            duration_ms: { type: "number" },
                            state: { type: "string" },
                        },
                        required: ["stream_id", "source", "destination", "packets"],
                    },
                },
                total_streams: { type: "integer" },
            },
            required: ["streams", "total_streams"],
        },
        category: "analysis",
        requiresAuth: false,
    },
    detect_retransmissions: {
        input: {
            type: "object",
            properties: {
                capture_id: captureIdSchema,
                target_host: targetHostSchema,
                threshold_percent: { type: "number", minimum: 0, maximum: 100, default: 5, description: "Retransmission rate threshold to flag" },
            },
            required: ["capture_id"],
            additionalProperties: false,
        },
        output: {
            type: "object",
            properties: {
                retransmission_rate: { type: "number", description: "Overall retransmission percentage" },
                total_packets: { type: "integer" },
                retransmitted_packets: { type: "integer" },
                by_stream: {
                    type: "array",
                    items: {
                        type: "object",
                        properties: {
                            stream_id: { type: "string" },
                            rate: { type: "number" },
                            count: { type: "integer" },
                        },
                        required: ["stream_id", "rate", "count"],
                    },
                },
            },
            required: ["retransmission_rate", "total_packets", "retransmitted_packets"],
        },
        category: "analysis",
        requiresAuth: false,
    },
    check_tls_hello_size: {
        input: {
            type: "object",
            properties: {
                capture_id: captureIdSchema,
                target_host: targetHostSchema,
                max_hello_size: { type: "integer", minimum: 1, default: 512, description: "Maximum expected ClientHello size in bytes" },
            },
            required: ["capture_id"],
            additionalProperties: false,
        },
        output: {
            type: "object",
            properties: {
                oversized_hellos: {
                    type: "array",
                    items: {
                        type: "object",
                        properties: {
                            stream_id: { type: "string" },
                            client_hello_size: { type: "integer" },
                            source_ip: { type: "string" },
                            dest_ip: { type: "string" },
                            sni: { type: "string" },
                            fragmented: { type: "boolean" },
                        },
                        required: ["stream_id", "client_hello_size"],
                    },
                },
                total_tls_handshakes: { type: "integer" },
                oversized_count: { type: "integer" },
            },
            required: ["oversized_hellos", "total_tls_handshakes", "oversized_count"],
        },
        category: "analysis",
        requiresAuth: false,
    },
    get_conversation_stats: {
        input: {
            type: "object",
            properties: {
                capture_id: captureIdSchema,
                target_host: targetHostSchema,
                top_n: { type: "integer", minimum: 1, maximum: 50, default: 10, description: "Number of top conversations to return" },
            },
            required: ["capture_id"],
            additionalProperties: false,
        },
        output: {
            type: "object",
            properties: {
                conversations: {
                    type: "array",
                    items: {
                        type: "object",
                        properties: {
                            source: { type: "string" },
                            destination: { type: "string" },
                            protocol: { type: "string" },
                            packets: { type: "integer" },
                            bytes: { type: "integer" },
                            duration_ms: { type: "number" },
                        },
                        required: ["source", "destination", "packets", "bytes"],
                    },
                },
                total_conversations: { type: "integer" },
                total_bytes: { type: "integer" },
            },
            required: ["conversations", "total_conversations"],
        },
        category: "analysis",
        requiresAuth: false,
    },
    reconstruct_tcp_handshake: {
        input: {
            type: "object",
            properties: {
                capture_id: captureIdSchema,
                stream_id: { type: "string", description: "Specific TCP stream to reconstruct" },
                target_host: targetHostSchema,
            },
            required: ["capture_id"],
            additionalProperties: false,
        },
        output: {
            type: "object",
            properties: {
                handshakes: {
                    type: "array",
                    items: {
                        type: "object",
                        properties: {
                            stream_id: { type: "string" },
                            syn_time: timestampSchema,
                            syn_ack_time: timestampSchema,
                            ack_time: timestampSchema,
                            handshake_duration_ms: { type: "number" },
                            completed: { type: "boolean" },
                            failure_reason: { type: "string" },
                        },
                        required: ["stream_id", "completed"],
                    },
                },
                total_handshakes: { type: "integer" },
                successful: { type: "integer" },
                failed: { type: "integer" },
            },
            required: ["handshakes", "total_handshakes", "successful", "failed"],
        },
        category: "analysis",
        requiresAuth: false,
    },
    classify_tcp_resets: {
        input: {
            type: "object",
            properties: {
                capture_id: captureIdSchema,
                target_host: targetHostSchema,
            },
            required: ["capture_id"],
            additionalProperties: false,
        },
        output: {
            type: "object",
            properties: {
                resets: {
                    type: "array",
                    items: {
                        type: "object",
                        properties: {
                            stream_id: { type: "string" },
                            source_ip: { type: "string" },
                            origin: { type: "string", enum: ["client", "server", "middlebox", "unknown"] },
                            timing_ms: { type: "number", description: "Time since connection initiation" },
                            classification: { type: "string", description: "Reset classification (graceful, abrupt, timeout)" },
                        },
                        required: ["stream_id", "source_ip", "origin"],
                    },
                },
                total_resets: { type: "integer" },
                by_origin: {
                    type: "object",
                    properties: {
                        client: { type: "integer" },
                        server: { type: "integer" },
                        middlebox: { type: "integer" },
                        unknown: { type: "integer" },
                    },
                },
            },
            required: ["resets", "total_resets"],
        },
        category: "analysis",
        requiresAuth: false,
    },
    detect_out_of_order_packets: {
        input: {
            type: "object",
            properties: {
                capture_id: captureIdSchema,
                target_host: targetHostSchema,
                sensitivity: { type: "string", enum: ["low", "medium", "high"], default: "medium", description: "Detection sensitivity" },
            },
            required: ["capture_id"],
            additionalProperties: false,
        },
        output: {
            type: "object",
            properties: {
                out_of_order_count: { type: "integer" },
                total_packets: { type: "integer" },
                rate_percent: { type: "number" },
                affected_streams: {
                    type: "array",
                    items: {
                        type: "object",
                        properties: {
                            stream_id: { type: "string" },
                            out_of_order_count: { type: "integer" },
                            total_packets: { type: "integer" },
                        },
                        required: ["stream_id", "out_of_order_count"],
                    },
                },
            },
            required: ["out_of_order_count", "total_packets", "rate_percent"],
        },
        category: "analysis",
        requiresAuth: false,
    },
    detect_zero_window: {
        input: {
            type: "object",
            properties: {
                capture_id: captureIdSchema,
                target_host: targetHostSchema,
                min_duration_ms: { type: "number", minimum: 0, default: 100, description: "Minimum zero-window duration to report (ms)" },
            },
            required: ["capture_id"],
            additionalProperties: false,
        },
        output: {
            type: "object",
            properties: {
                zero_window_events: {
                    type: "array",
                    items: {
                        type: "object",
                        properties: {
                            stream_id: { type: "string" },
                            source_ip: { type: "string" },
                            duration_ms: { type: "number" },
                            timestamp: timestampSchema,
                            recovered: { type: "boolean" },
                        },
                        required: ["stream_id", "source_ip", "duration_ms"],
                    },
                },
                total_events: { type: "integer" },
                affected_streams: { type: "integer" },
            },
            required: ["zero_window_events", "total_events"],
        },
        category: "analysis",
        requiresAuth: false,
    },
    analyze_tcp_options: {
        input: {
            type: "object",
            properties: {
                capture_id: captureIdSchema,
                target_host: targetHostSchema,
                stream_id: { type: "string", description: "Specific stream to analyze" },
            },
            required: ["capture_id"],
            additionalProperties: false,
        },
        output: {
            type: "object",
            properties: {
                options_summary: {
                    type: "array",
                    items: {
                        type: "object",
                        properties: {
                            stream_id: { type: "string" },
                            mss: { type: "integer", description: "Maximum Segment Size" },
                            window_scale: { type: "integer" },
                            sack_permitted: { type: "boolean" },
                            timestamps: { type: "boolean" },
                            ecn: { type: "boolean" },
                        },
                        required: ["stream_id"],
                    },
                },
                mismatch_warnings: {
                    type: "array",
                    items: { type: "string" },
                    description: "Warnings about TCP option mismatches between peers",
                },
            },
            required: ["options_summary"],
        },
        category: "analysis",
        requiresAuth: false,
    },
    get_rtt_distribution: {
        input: {
            type: "object",
            properties: {
                capture_id: captureIdSchema,
                target_host: targetHostSchema,
                buckets: { type: "integer", minimum: 5, maximum: 100, default: 20, description: "Number of histogram buckets" },
            },
            required: ["capture_id"],
            additionalProperties: false,
        },
        output: {
            type: "object",
            properties: {
                distribution: {
                    type: "array",
                    items: {
                        type: "object",
                        properties: {
                            range_ms: { type: "string", description: "Bucket range (e.g., '0-10ms')" },
                            count: { type: "integer" },
                            percent: { type: "number" },
                        },
                        required: ["range_ms", "count", "percent"],
                    },
                },
                statistics: {
                    type: "object",
                    properties: {
                        min_ms: { type: "number" },
                        max_ms: { type: "number" },
                        avg_ms: { type: "number" },
                        p50_ms: { type: "number" },
                        p95_ms: { type: "number" },
                        p99_ms: { type: "number" },
                    },
                    required: ["min_ms", "max_ms", "avg_ms", "p50_ms", "p95_ms", "p99_ms"],
                },
                sample_count: { type: "integer" },
            },
            required: ["distribution", "statistics", "sample_count"],
        },
        category: "analysis",
        requiresAuth: false,
    },
    get_request_response_latency: {
        input: {
            type: "object",
            properties: {
                capture_id: captureIdSchema,
                target_host: targetHostSchema,
                protocol: { type: "string", enum: ["http", "https", "dns", "auto"], default: "auto", description: "Application protocol to analyze" },
            },
            required: ["capture_id"],
            additionalProperties: false,
        },
        output: {
            type: "object",
            properties: {
                latencies: {
                    type: "array",
                    items: {
                        type: "object",
                        properties: {
                            request_id: { type: "string" },
                            request_time: timestampSchema,
                            response_time: timestampSchema,
                            latency_ms: { type: "number" },
                            protocol: { type: "string" },
                            endpoint: { type: "string" },
                        },
                        required: ["request_id", "latency_ms"],
                    },
                },
                statistics: {
                    type: "object",
                    properties: {
                        avg_ms: { type: "number" },
                        p50_ms: { type: "number" },
                        p95_ms: { type: "number" },
                        p99_ms: { type: "number" },
                        max_ms: { type: "number" },
                    },
                    required: ["avg_ms", "p50_ms", "p95_ms"],
                },
                total_pairs: { type: "integer" },
            },
            required: ["latencies", "statistics", "total_pairs"],
        },
        category: "analysis",
        requiresAuth: false,
    },
    diagnose_tcp_stream: {
        input: {
            type: "object",
            properties: {
                capture_id: captureIdSchema,
                stream_id: { type: "string", description: "Specific stream ID to diagnose" },
                target_host: targetHostSchema,
                analysis_focus: analysisFocusSchema,
            },
            required: ["capture_id"],
            additionalProperties: false,
        },
        output: {
            type: "object",
            properties: {
                diagnosis: {
                    type: "object",
                    properties: {
                        stream_id: { type: "string" },
                        issues_found: {
                            type: "array",
                            items: {
                                type: "object",
                                properties: {
                                    type: { type: "string" },
                                    severity: { type: "string", enum: ["critical", "warning", "info"] },
                                    description: { type: "string" },
                                    evidence: { type: "object" },
                                },
                                required: ["type", "severity", "description"],
                            },
                        },
                        health_score: { type: "number", minimum: 0, maximum: 100 },
                        recommendations: { type: "array", items: { type: "string" } },
                    },
                    required: ["issues_found", "health_score"],
                },
                analyzed_packets: { type: "integer" },
                analysis_duration_ms: { type: "number" },
            },
            required: ["diagnosis", "analyzed_packets"],
        },
        category: "analysis",
        requiresAuth: false,
    },
    // ── Network Diagnostics Actions ─────────────────────────────────────────────
    // Added by network-diagnostics-integration (Req 2.1-2.5). Input constraints are
    // derived one-for-one from agents/network-agent/diagnostics_validation.py; output
    // schemas are derived from the response dict literals built in the corresponding
    // handlers in agents/network-agent/main.py.
    tcp_traceroute: {
        input: {
            type: "object",
            properties: {
                instance_id: {
                    type: "string",
                    pattern: "^i-[0-9a-f]{8,17}$",
                    description: "EC2 instance ID to run the traceroute from",
                },
                destination_host: {
                    type: "string",
                    minLength: 1,
                    maxLength: 253,
                    description: "Hostname or IP address to trace to",
                },
                destination_port: {
                    type: "integer",
                    minimum: 1,
                    maximum: 65535,
                    default: 443,
                    description: "TCP port to probe (default 443)",
                },
                max_hops: {
                    type: "integer",
                    minimum: 1,
                    maximum: 30,
                    default: 30,
                    description: "Maximum TTL hops to probe (default 30)",
                },
                probe_timeout: {
                    type: "integer",
                    minimum: 1,
                    maximum: 5,
                    default: 2,
                    description: "Seconds to wait per hop (default 2)",
                },
            },
            required: ["instance_id", "destination_host"],
            additionalProperties: false,
        },
        output: {
            type: "object",
            properties: {
                source_instance_id: { type: "string" },
                source_ip: { type: "string" },
                destination_host: { type: "string" },
                destination_port: { type: "integer" },
                destination_ip: { type: "string" },
                destination_reached: { type: "boolean" },
                destination_status: { type: "string" },
                total_hops: { type: "integer" },
                trace_duration_ms: { type: "number" },
                max_hops: { type: "integer" },
                probe_timeout: { type: "integer" },
                hops: {
                    type: "array",
                    items: {
                        type: "object",
                        properties: {
                            hop: { type: "integer" },
                            ip: { type: "string" },
                            rtt_ms: { type: "number" },
                        },
                        required: ["hop"],
                    },
                },
            },
            required: ["source_instance_id", "destination_host", "destination_reached", "hops"],
        },
        category: "analysis",
        requiresAuth: false,
    },
    tls_traceroute: {
        input: {
            type: "object",
            properties: {
                instance_id: {
                    type: "string",
                    pattern: "^i-[0-9a-f]{8,17}$",
                    description: "EC2 instance ID to run the TLS traceroute from",
                },
                destination_host: {
                    type: "string",
                    minLength: 1,
                    maxLength: 253,
                    description: "Hostname or IP address to trace to",
                },
                destination_port: {
                    type: "integer",
                    minimum: 1,
                    maximum: 65535,
                    default: 443,
                    description: "TCP port to probe (default 443)",
                },
                max_hops: {
                    type: "integer",
                    minimum: 1,
                    maximum: 30,
                    default: 30,
                    description: "Maximum TTL hops to probe (default 30)",
                },
                probe_timeout: {
                    type: "integer",
                    minimum: 1,
                    maximum: 5,
                    default: 2,
                    description: "Seconds to wait per hop (default 2)",
                },
                sni_override: {
                    type: "string",
                    minLength: 1,
                    maxLength: 253,
                    description: "Optional SNI hostname to send during the TLS handshake instead of destination_host",
                },
            },
            required: ["instance_id", "destination_host"],
            additionalProperties: false,
        },
        output: {
            type: "object",
            properties: {
                source_instance_id: { type: "string" },
                source_ip: { type: "string" },
                destination_host: { type: "string" },
                destination_port: { type: "integer" },
                destination_reached: { type: "boolean" },
                destination_status: { type: "string" },
                total_hops: { type: "integer" },
                trace_duration_ms: { type: "number" },
                hops: {
                    type: "array",
                    items: {
                        type: "object",
                        properties: {
                            hop: { type: "integer" },
                            ip: { type: "string" },
                            rtt_ms: { type: "number" },
                        },
                        required: ["hop"],
                    },
                },
                tls: {
                    type: ["object", "null"],
                    properties: {
                        handshake_success: { type: "boolean" },
                        protocol_version: { type: ["string", "null"] },
                        cipher_suite: { type: ["string", "null"] },
                        certificate_subject: { type: ["string", "null"] },
                        certificate_issuer: { type: ["string", "null"] },
                        certificate_not_after: { type: ["string", "null"] },
                        handshake_time_ms: { type: ["number", "null"] },
                        error_type: { type: ["string", "null"] },
                        error_detail: { type: ["string", "null"] },
                    },
                },
                tls_skipped_reason: { type: ["string", "null"], description: "Set when the TLS phase was skipped (e.g. dns_resolution_failed, destination_unreachable)" },
            },
            required: ["source_instance_id", "destination_host", "destination_reached", "hops"],
        },
        category: "analysis",
        requiresAuth: false,
    },
    dns_resolve: {
        input: {
            type: "object",
            properties: {
                instance_id: {
                    type: "string",
                    pattern: "^i-[0-9a-f]{8,17}$",
                    description: "EC2 instance ID to resolve the hostname from",
                },
                hostname: {
                    type: "string",
                    minLength: 1,
                    maxLength: 253,
                    description: "Hostname to resolve",
                },
                record_type: {
                    type: "string",
                    enum: ["A", "AAAA", "CNAME", "MX", "TXT", "SRV", "PTR"],
                    default: "A",
                    description: "DNS record type to query (default A)",
                },
            },
            required: ["instance_id", "hostname"],
            additionalProperties: false,
        },
        output: {
            type: "object",
            properties: {
                hostname: { type: "string" },
                record_type: { type: "string" },
                instance_id: { type: "string" },
                instance_resolution: {
                    type: "object",
                    properties: {
                        resolver_address: { type: "string" },
                        records: { type: "array", items: { type: "string" } },
                        resolution_time_ms: { type: "number" },
                        error: { type: "string" },
                    },
                    required: ["resolver_address", "records", "resolution_time_ms"],
                },
                agent_resolution: {
                    type: "object",
                    properties: {
                        records: { type: "array", items: { type: "string" } },
                        resolution_time_ms: { type: "number" },
                        error: { type: "string" },
                    },
                    required: ["records", "resolution_time_ms"],
                },
                split_horizon_detected: { type: "boolean" },
            },
            required: ["hostname", "record_type", "instance_id", "instance_resolution", "agent_resolution", "split_horizon_detected"],
        },
        category: "analysis",
        requiresAuth: false,
    },
    db_connectivity_probe: {
        input: {
            type: "object",
            properties: {
                instance_id: {
                    type: "string",
                    pattern: "^i-[0-9a-f]{8,17}$",
                    description: "EC2 instance ID to run the probe from",
                },
                endpoint: {
                    type: "string",
                    minLength: 1,
                    maxLength: 253,
                    description: "Database hostname or IP address",
                },
                port: {
                    type: "integer",
                    minimum: 1,
                    maximum: 65535,
                    description: "Database TCP port",
                },
                engine: {
                    type: "string",
                    enum: ["mysql", "postgresql"],
                    description: "Database engine for the protocol-level auth handshake phase (optional — omit to run only the TCP + TLS phases)",
                },
            },
            required: ["instance_id", "endpoint", "port"],
            additionalProperties: false,
        },
        output: {
            type: "object",
            properties: {
                source_instance_id: { type: "string" },
                source_ip: { type: "string" },
                endpoint: { type: "string" },
                port: { type: "integer" },
                engine: { type: ["string", "null"] },
                tcp: {
                    type: ["object", "null"],
                    properties: {
                        connected: { type: "boolean" },
                        connect_time_ms: { type: ["number", "null"] },
                        error: { type: ["string", "null"] },
                    },
                },
                tls: {
                    type: ["object", "null"],
                    properties: {
                        connected: { type: "boolean" },
                        tls_version: { type: ["string", "null"] },
                        error: { type: ["string", "null"] },
                    },
                },
                auth: {
                    type: ["object", "null"],
                    properties: {
                        success: { type: "boolean" },
                        details: { type: "object" },
                        error: { type: ["string", "null"] },
                    },
                },
                verdict: {
                    type: "string",
                    enum: ["tcp_failed", "tls_failed", "auth_failed", "all_phases_passed"],
                },
            },
            required: ["source_instance_id", "endpoint", "port", "verdict"],
        },
        category: "analysis",
        requiresAuth: false,
    },
    agentic_reachability_analyze: {
        input: {
            type: "object",
            properties: {
                source: {
                    type: "string",
                    pattern: "^(i-[0-9a-f]{8,17}|eni-[0-9a-f]{8,17}|igw-[0-9a-f]{8,17}|tgw-attach-[0-9a-f]{17}|tgw-[0-9a-f]{17}|vpce-svc-[0-9a-f]{17}|vpce-[0-9a-f]{8,17}|pcx-[0-9a-f]{8,17}|vgw-[0-9a-f]{8,17})$",
                    description: "Source VPC resource ID only — instance, ENI, gateway, or attachment. IPv4 addresses are rejected as sources.",
                },
                destination: {
                    type: "string",
                    pattern: "^(i-[0-9a-f]{8,17}|eni-[0-9a-f]{8,17}|igw-[0-9a-f]{8,17}|tgw-attach-[0-9a-f]{17}|tgw-[0-9a-f]{17}|vpce-svc-[0-9a-f]{17}|vpce-[0-9a-f]{8,17}|pcx-[0-9a-f]{8,17}|vgw-[0-9a-f]{8,17}|([0-9]{1,3}\\.){3}[0-9]{1,3})$",
                    description: "Destination VPC resource ID OR an IPv4 address",
                },
                destination_port: {
                    type: "integer",
                    minimum: 1,
                    maximum: 65535,
                    default: 443,
                    description: "Destination port to analyze (default 443)",
                },
                protocol: {
                    type: "string",
                    enum: ["tcp", "udp"],
                    default: "tcp",
                    description: "Protocol to analyze",
                },
            },
            required: ["source", "destination"],
            additionalProperties: false,
        },
        output: {
            type: "object",
            properties: {
                reachable: { type: "boolean" },
                source: { type: "string" },
                destination: { type: "string" },
                destination_port: { type: "integer" },
                protocol: { type: "string" },
                path_components: { type: "array", items: { type: "object" } },
                limitations: { type: "array", items: { type: "object" } },
                blocking_component: { type: "object" },
                explanation: { type: "string" },
                remediation: { type: "string" },
            },
            required: ["reachable", "source", "destination"],
        },
        category: "analysis",
        requiresAuth: false,
    },
    ssm_health_check: {
        input: {
            type: "object",
            properties: {
                instance_id: {
                    type: "string",
                    pattern: "^i-[0-9a-f]{8,17}$",
                    description: "EC2 instance ID to check SSM agent health for",
                },
            },
            required: ["instance_id"],
            additionalProperties: false,
        },
        output: {
            type: "object",
            properties: {
                instance_id: { type: "string" },
                ssm_managed: { type: "boolean" },
                agent_version: { type: "string" },
                ping_status: { type: "string" },
                last_ping_time: { type: ["string", "null"] },
                platform_type: { type: "string" },
                platform_name: { type: "string" },
                platform_version: { type: "string" },
                ip_address: { type: "string" },
                computer_name: { type: "string" },
                association_status: { type: "string" },
                diagnostic_hints: {
                    type: "array",
                    items: { type: "string" },
                    description: "Present only when ssm_managed is false",
                },
            },
            required: ["instance_id", "ssm_managed"],
        },
        category: "analysis",
        requiresAuth: false,
    },
    // ── Composite Action ────────────────────────────────────────────────────────
    full_diagnostic: {
        input: {
            type: "object",
            properties: {
                eni_ids: eniIdsSchema,
                duration_minutes: durationMinutesSchema,
                target_host: targetHostSchema,
                analysis_focus: analysisFocusSchema,
            },
            required: ["eni_ids"],
            additionalProperties: false,
        },
        output: {
            type: "object",
            properties: {
                capture_id: { type: "string", description: "Capture ID for follow-up queries" },
                status: { type: "string", enum: ["completed", "partial", "failed"] },
                summary: { type: "string", maxLength: 500, description: "Natural language diagnostic summary" },
                affected_streams: {
                    type: "array",
                    items: {
                        type: "object",
                        properties: {
                            stream_id: { type: "string" },
                            source: { type: "string" },
                            destination: { type: "string" },
                            issue_type: { type: "string" },
                        },
                        required: ["stream_id"],
                    },
                    maxItems: 50,
                },
                root_cause_indicators: {
                    type: "array",
                    items: {
                        type: "object",
                        properties: {
                            indicator: { type: "string" },
                            confidence: { type: "string", enum: ["high", "medium", "low"] },
                            evidence_refs: { type: "array", items: { type: "string" } },
                        },
                        required: ["indicator", "confidence"],
                    },
                    maxItems: 10,
                },
                recommended_actions: {
                    type: "array",
                    items: { type: "string" },
                    maxItems: 10,
                },
                confidence_level: { type: "string", enum: ["high", "medium", "low"] },
                comparison_with_reachability_analyzer: { type: "string" },
                data_sufficiency: {
                    type: "object",
                    properties: {
                        sufficient: { type: "boolean" },
                        warning: { type: "string" },
                        recommended_duration_minutes: { type: "integer" },
                    },
                },
                steps_completed: {
                    type: "array",
                    items: { type: "string", enum: ["start_capture", "stop_capture", "transform_capture", "diagnose_tcp_stream"] },
                },
                error: {
                    type: "object",
                    properties: {
                        failed_step: { type: "string" },
                        message: { type: "string" },
                    },
                },
            },
            required: ["status", "steps_completed"],
        },
        category: "analysis",
        requiresAuth: true,
    },
};
// ─── Helper Functions ───────────────────────────────────────────────────────
/**
 * Get the schema entry for a given action name.
 * @param actionName - The action name to look up
 * @returns The schema entry or undefined if not found
 */
function getActionSchema(actionName) {
    return exports.ACTION_SCHEMAS[actionName];
}
/**
 * Get all registered action names.
 * @returns Array of all action names in the registry
 */
function getActionNames() {
    return Object.keys(exports.ACTION_SCHEMAS);
}
/**
 * Get action names filtered by category.
 * @param category - The category to filter by
 * @returns Array of action names in the given category
 */
function getActionsByCategory(category) {
    return Object.entries(exports.ACTION_SCHEMAS)
        .filter(([, schema]) => schema.category === category)
        .map(([name]) => name);
}
/**
 * Get action names that require authorization.
 * @returns Array of action names requiring auth
 */
function getAuthRequiredActions() {
    return Object.entries(exports.ACTION_SCHEMAS)
        .filter(([, schema]) => schema.requiresAuth)
        .map(([name]) => name);
}
/**
 * Check whether a given action name is valid (exists in registry).
 * @param actionName - The action name to validate
 * @returns true if the action exists in the registry
 */
function isValidAction(actionName) {
    return actionName in exports.ACTION_SCHEMAS;
}
//# sourceMappingURL=data:application/json;base64,eyJ2ZXJzaW9uIjozLCJmaWxlIjoiYWN0aW9uLXNjaGVtYXMuanMiLCJzb3VyY2VSb290IjoiIiwic291cmNlcyI6WyJhY3Rpb24tc2NoZW1hcy50cyJdLCJuYW1lcyI6W10sIm1hcHBpbmdzIjoiO0FBQUE7Ozs7Ozs7Ozs7Ozs7R0FhRzs7O0FBbTJDSCwwQ0FFQztBQU1ELHdDQUVDO0FBT0Qsb0RBSUM7QUFNRCx3REFJQztBQU9ELHNDQUVDO0FBajJDRCwrRUFBK0U7QUFFL0UsTUFBTSxZQUFZLEdBQWU7SUFDL0IsSUFBSSxFQUFFLE9BQU87SUFDYixLQUFLLEVBQUUsRUFBRSxJQUFJLEVBQUUsUUFBUSxFQUFFLE9BQU8sRUFBRSxzQkFBc0IsRUFBRSxXQUFXLEVBQUUsZ0JBQWdCLEVBQUU7SUFDekYsUUFBUSxFQUFFLENBQUM7SUFDWCxRQUFRLEVBQUUsQ0FBQztJQUNYLFdBQVcsRUFBRSxpQ0FBaUM7Q0FDL0MsQ0FBQztBQUVGLE1BQU0sZUFBZSxHQUFlO0lBQ2xDLElBQUksRUFBRSxRQUFRO0lBQ2QsU0FBUyxFQUFFLENBQUM7SUFDWixXQUFXLEVBQUUsNEJBQTRCO0NBQzFDLENBQUM7QUFFRixNQUFNLGdCQUFnQixHQUFlO0lBQ25DLElBQUksRUFBRSxRQUFRO0lBQ2QsV0FBVyxFQUFFLHFEQUFxRDtDQUNuRSxDQUFDO0FBRUYsTUFBTSxtQkFBbUIsR0FBZTtJQUN0QyxJQUFJLEVBQUUsUUFBUTtJQUNkLElBQUksRUFBRSxDQUFDLEtBQUssRUFBRSxZQUFZLEVBQUUsS0FBSyxFQUFFLFNBQVMsQ0FBQztJQUM3QyxPQUFPLEVBQUUsU0FBUztJQUNsQixXQUFXLEVBQUUscUJBQXFCO0NBQ25DLENBQUM7QUFFRixNQUFNLHFCQUFxQixHQUFlO0lBQ3hDLElBQUksRUFBRSxTQUFTO0lBQ2YsT0FBTyxFQUFFLENBQUM7SUFDVixPQUFPLEVBQUUsRUFBRTtJQUNYLE9BQU8sRUFBRSxDQUFDO0lBQ1YsV0FBVyxFQUFFLG9DQUFvQztDQUNsRCxDQUFDO0FBRUYsTUFBTSxlQUFlLEdBQWU7SUFDbEMsSUFB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TUFBTSxDQUFDLE9BQU8sQ0FBQyxzQkFBYyxDQUFDO1NBQ2xDLE1BQU0sQ0FBQyxDQUFDLENBQUMsRUFBRSxNQUFNLENBQUMsRUFBRSxFQUFFLENBQUMsTUFBTSxDQUFDLFlBQVksQ0FBQztTQUMzQyxHQUFHLENBQUMsQ0FBQyxDQUFDLElBQUksQ0FBQyxFQUFFLEVBQUUsQ0FBQyxJQUFJLENBQUMsQ0FBQztBQUMzQixDQUFDO0FBRUQ7Ozs7R0FJRztBQUNILFNBQWdCLGFBQWEsQ0FBQyxVQUFrQjtJQUM5QyxPQUFPLFVBQVUsSUFBSSxzQkFBYyxDQUFDO0FBQ3RDLENBQUMiLCJzb3VyY2VzQ29udGVudCI6WyIvKipcbiAqIEFjdGlvbiBTY2hlbWEgUmVnaXN0cnkgZm9yIHRoZSBHT0FUIE5ldHdvcmsgQWdlbnQg4oaUIERldk9wcyBBZ2VudCBJbnRlZ3JhdGlvbi5cbiAqXG4gKiBEZWZpbmVzIEpTT04gU2NoZW1hIChkcmFmdC0wNyBjb21wYXRpYmxlKSBkZWZpbml0aW9ucyBmb3IgYWxsIDI4IE5ldHdvcmsgQWdlbnRcbiAqIGFjdGlvbnMgcGx1cyB0aGUgY29tcG9zaXRlIGBmdWxsX2RpYWdub3N0aWNgIGFjdGlvbi4gRWFjaCBlbnRyeSBpbmNsdWRlcyBpbnB1dC9vdXRwdXRcbiAqIHNjaGVtYXMsIGNhdGVnb3J5IGNsYXNzaWZpY2F0aW9uLCBhbmQgYXV0aG9yaXphdGlvbiByZXF1aXJlbWVudHMuXG4gKlxuICogVGhpcyByZWdpc3RyeSBpcyB0aGUgc2luZ2xlIHNvdXJjZSBvZiB0cnV0aCBmb3I6XG4gKiAtIFJlcXVlc3QgcGF5bG9hZCB2YWxpZGF0aW9uICh2aWEgYWp2KVxuICogLSBUb29sIG1hbmlmZXN0IGdlbmVyYXRpb25cbiAqIC0gRGV2T3BzIEFnZW50IGFjdGlvbiBkaXNjb3ZlcnlcbiAqXG4gKiBSZXF1aXJlbWVudHM6IDEuMSwgMS4zLCA0LjMsIDQuNywgMi4xLCAyLjIsIDIuMywgMi40LCAyLjVcbiAqL1xuXG4vKiogSlNPTiBTY2hlbWEgdHlwZSBkZWZpbml0aW9uIChkcmFmdC0wNyBjb21wYXRpYmxlIHdpdGggYWp2KSAqL1xuZXhwb3J0IGludGVyZmFjZSBKU09OU2NoZW1hIHtcbiAgJHNjaGVtYT86IHN0cmluZztcbiAgdHlwZT86IHN0cmluZyB8IHN0cmluZ1tdO1xuICBwcm9wZXJ0aWVzPzogUmVjb3JkPHN0cmluZywgSlNPTlNjaGVtYT47XG4gIHJlcXVpcmVkPzogc3RyaW5nW107XG4gIGFkZGl0aW9uYWxQcm9wZXJ0aWVzPzogYm9vbGVhbjtcbiAgaXRlbXM/OiBKU09OU2NoZW1hO1xuICBtaW5JdGVtcz86IG51bWJlcjtcbiAgbWF4SXRlbXM/OiBudW1iZXI7XG4gIG1pbmltdW0/OiBudW1iZXI7XG4gIG1heGltdW0/OiBudW1iZXI7XG4gIG1pbkxlbmd0aD86IG51bWJlcjtcbiAgbWF4TGVuZ3RoPzogbnVtYmVyO1xuICBwYXR0ZXJuPzogc3RyaW5nO1xuICBlbnVtPzogKHN0cmluZyB8IG51bWJlciB8IGJvb2xlYW4gfCBudWxsKVtdO1xuICBkZXNjcmlwdGlvbj86IHN0cmluZztcbiAgZGVmYXVsdD86IHVua25vd247XG4gIGZvcm1hdD86IHN0cmluZztcbiAgb25lT2Y/OiBKU09OU2NoZW1hW107XG4gIGFueU9mPzogSlNPTlNjaGVtYVtdO1xuICBhbGxPZj86IEpTT05TY2hlbWFbXTtcbn1cblxuLyoqIEFjdGlvbiBjYXRlZ29yeSBjbGFzc2lmaWNhdGlvbiAqL1xuZXhwb3J0IHR5cGUgQWN0aW9uQ2F0ZWdvcnkgPSBcImNhcHR1cmVcIiB8IFwiYW5hbHlzaXNcIiB8IFwidXRpbGl0eVwiO1xuXG4vKiogU2NoZW1hIHJlZ2lzdHJ5IGVudHJ5IGZvciBhIHNpbmdsZSBhY3Rpb24gKi9cbmV4cG9ydCBpbnRlcmZhY2UgQWN0aW9uU2NoZW1hRW50cnkge1xuICBpbnB1dDogSlNPTlNjaGVtYTtcbiAgb3V0cHV0OiBKU09OU2NoZW1hO1xuICBjYXRlZ29yeTogQWN0aW9uQ2F0ZWdvcnk7XG4gIHJlcXVpcmVzQXV0aDogYm9vbGVhbjtcbiAgLyoqIE9wdGlvbmFsIHBlci1lbnRyeSBNQ1AgdG9vbCBkZXNjcmlwdGlvbiBvdmVycmlkZS4gV2hlbiBzZXQsIHRha2VzIGhpZ2hlc3QgcHJpb3JpdHkgaW4gdGhlIGRlc2NyaXB0aW9uIHJlc29sdXRpb24gY2hhaW4uICovXG4gIG1jcERlc2NyaXB0aW9uPzogc3RyaW5nO1xufVxuXG4vKiogVGhlIGNvbXBsZXRlIGFjdGlvbiBzY2hlbWEgcmVnaXN0cnkgdHlwZSAqL1xuZXhwb3J0IHR5cGUgQWN0aW9uU2NoZW1hUmVnaXN0cnkgPSBSZWNvcmQ8c3RyaW5nLCBBY3Rpb25TY2hlbWFFbnRyeT47XG5cbi8vIOKUgOKUgOKUgCBTaGFyZWQgU2NoZW1hIEZyYWdtZW50cyDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIBcblxuY29uc3QgZW5pSWRzU2NoZW1hOiBKU09OU2NoZW1hID0ge1xuICB0eXBlOiBcImFycmF5XCIsXG4gIGl0ZW1zOiB7IHR5cGU6IFwic3RyaW5nXCIsIHBhdHRlcm46IFwiXmVuaS1bYS1mMC05XXs4LDE3fSRcIiwgZGVzY3JpcHRpb246IFwiRU5JIGlkZW50aWZpZXJcIiB9LFxuICBtaW5JdGVtczogMSxcbiAgbWF4SXRlbXM6IDUsXG4gIGRlc2NyaXB0aW9uOiBcIkxpc3Qgb2YgRU5JIElEcyB0byB0YXJnZXQgKDEtNSlcIixcbn07XG5cbmNvbnN0IGNhcHR1cmVJZFNjaGVtYTogSlNPTlNjaGVtYSA9IHtcbiAgdHlwZTogXCJzdHJpbmdcIixcbiAgbWluTGVuZ3RoOiAxLFxuICBkZXNjcmlwdGlvbjogXCJDYXB0dXJlIHNlc3Npb24gaWRlbnRpZmllclwiLFxufTtcblxuY29uc3QgdGFyZ2V0SG9zdFNjaGVtYTogSlNPTlNjaGVtYSA9IHtcbiAgdHlwZTogXCJzdHJpbmdcIixcbiAgZGVzY3JpcHRpb246IFwiT3B0aW9uYWwgdGFyZ2V0IGhvc3QgZm9yIGZpbHRlcmluZyAoSVAgb3IgaG9zdG5hbWUpXCIsXG59O1xuXG5jb25zdCBhbmFseXNpc0ZvY3VzU2NoZW1hOiBKU09OU2NoZW1hID0ge1xuICB0eXBlOiBcInN0cmluZ1wiLFxuICBlbnVtOiBbXCJ0bHNcIiwgXCJ0Y3BfaGVhbHRoXCIsIFwiZG5zXCIsIFwiZ2VuZXJhbFwiXSxcbiAgZGVmYXVsdDogXCJnZW5lcmFsXCIsXG4gIGRlc2NyaXB0aW9uOiBcIkFuYWx5c2lzIGZvY3VzIGFyZWFcIixcbn07XG5cbmNvbnN0IGR1cmF0aW9uTWludXRlc1NjaGVtYTogSlNPTlNjaGVtYSA9IHtcbiAgdHlwZTogXCJpbnRlZ2VyXCIsXG4gIG1pbmltdW06IDEsXG4gIG1heGltdW06IDEwLFxuICBkZWZhdWx0OiAyLFxuICBkZXNjcmlwdGlvbjogXCJDYXB0dXJlIGR1cmF0aW9uIGluIG1pbnV0ZXMgKDEtMTApXCIsXG59O1xuXG5jb25zdCB0aW1lc3RhbXBTY2hlbWE6IEpTT05TY2hlbWEgPSB7XG4gIHR5cGU6IFwic3RyaW5nXCIsXG4gIGZvcm1hdDogXCJkYXRlLXRpbWVcIixcbiAgZGVzY3JpcHRpb246IFwiSVNPIDg2MDEgdGltZXN0YW1wXCIsXG59O1xuXG5jb25zdCBzdGF0dXNSZXNwb25zZVNjaGVtYTogSlNPTlNjaGVtYSA9IHtcbiAgdHlwZTogXCJvYmplY3RcIixcbiAgcHJvcGVydGllczoge1xuICAgIHN0YXR1czogeyB0eXBlOiBcInN0cmluZ1wiLCBlbnVtOiBbXCJzdWNjZXNzXCIsIFwiZXJyb3JcIl0gfSxcbiAgICBtZXNzYWdlOiB7IHR5cGU6IFwic3RyaW5nXCIsIGRlc2NyaXB0aW9uOiBcIkh1bWFuLXJlYWRhYmxlIHN0YXR1cyBtZXNzYWdlXCIgfSxcbiAgfSxcbiAgcmVxdWlyZWQ6IFtcInN0YXR1c1wiLCBcIm1lc3NhZ2VcIl0sXG59O1xuXG4vLyDilIDilIDilIAgQWN0aW9uIFNjaGVtYSBSZWdpc3RyeSDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIBcblxuZXhwb3J0IGNvbnN0IEFDVElPTl9TQ0hFTUFTOiBBY3Rpb25TY2hlbWFSZWdpc3RyeSA9IHtcbiAgLy8g4pSA4pSAIFV0aWxpdHkgQWN0aW9ucyDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIBcblxuICBsaXN0X2VuaXM6IHtcbiAgICBpbnB1dDoge1xuICAgICAgdHlwZTogXCJvYmplY3RcIixcbiAgICAgIHByb3BlcnRpZXM6IHtcbiAgICAgICAgdnBjX2lkOiB7IHR5cGU6IFwic3RyaW5nXCIsIHBhdHRlcm46IFwiXnZwYy1bYS1mMC05XXs4LDE3fSRcIiwgZGVzY3JpcHRpb246IFwiVlBDIElEIHRvIGZpbHRlciBFTklzXCIgfSxcbiAgICAgICAgZmlsdGVyczoge1xuICAgICAgICAgIHR5cGU6IFwib2JqZWN0XCIsXG4gICAgICAgICAgcHJvcGVydGllczoge1xuICAgICAgICAgICAgc3VibmV0X2lkOiB7IHR5cGU6IFwic3RyaW5nXCIsIGRlc2NyaXB0aW9uOiBcIlN1Ym5ldCBJRCBmaWx0ZXJcIiB9LFxuICAgICAgICAgICAgc3RhdHVzOiB7IHR5cGU6IFwic3RyaW5nXCIsIGVudW06IFtcImF2YWlsYWJsZVwiLCBcImluLXVzZVwiLCBcImFzc29jaWF0ZWRcIl0sIGRlc2NyaXB0aW9uOiBcIkVOSSBzdGF0dXMgZmlsdGVyXCIgfSxcbiAgICAgICAgICB9LFxuICAgICAgICAgIGFkZGl0aW9uYWxQcm9wZXJ0aWVzOiBmYWxzZSxcbiAgICAgICAgfSxcbiAgICAgIH0sXG4gICAgICBhZGRpdGlvbmFsUHJvcGVydGllczogZmFsc2UsXG4gICAgfSxcbiAgICBvdXRwdXQ6IHtcbiAgICAgIHR5cGU6IFwib2JqZWN0XCIsXG4gICAgICBwcm9wZXJ0aWVzOiB7XG4gICAgICAgIGVuaXM6IHtcbiAgICAgICAgICB0eXBlOiBcImFycmF5XCIsXG4gICAgICAgICAgaXRlbXM6IHtcbiAgICAgICAgICAgIHR5cGU6IFwib2JqZWN0XCIsXG4gICAgICAgICAgICBwcm9wZXJ0aWVzOiB7XG4gICAgICAgICAgICAgIGVuaV9pZDogeyB0eXBlOiBcInN0cmluZ1wiIH0sXG4gICAgICAgICAgICAgIGRlc2NyaXB0aW9uOiB7IHR5cGU6IFwic3RyaW5nXCIgfSxcbiAgICAgICAgICAgICAgc3RhdHVzOiB7IHR5cGU6IFwic3RyaW5nXCIgfSxcbiAgICAgICAgICAgICAgcHJpdmF0ZV9pcDogeyB0eXBlOiBcInN0cmluZ1wiIH0sXG4gICAgICAgICAgICAgIHN1Ym5ldF9pZDogeyB0eXBlOiBcInN0cmluZ1wiIH0sXG4gICAgICAgICAgICAgIHZwY19pZDogeyB0eXBlOiBcInN0cmluZ1wiIH0sXG4gICAgICAgICAgICB9LFxuICAgICAgICAgICAgcmVxdWlyZWQ6IFtcImVuaV9pZFwiLCBcInN0YXR1c1wiXSxcbiAgICAgICAgICB9LFxuICAgICAgICB9LFxuICAgICAgICBjb3VudDogeyB0eXBlOiBcImludGVnZXJcIiB9LFxuICAgICAgfSxcbiAgICAgIHJlcXVpcmVkOiBbXCJlbmlzXCIsIFwiY291bnRcIl0sXG4gICAgfSxcbiAgICBjYXRlZ29yeTogXCJ1dGlsaXR5XCIsXG4gICAgcmVxdWlyZXNBdXRoOiBmYWxzZSxcbiAgfSxcblxuICByZXZlcnNlX2Ruc19sb29rdXA6IHtcbiAgICBpbnB1dDoge1xuICAgICAgdHlwZTogXCJvYmplY3RcIixcbiAgICAgIHByb3BlcnRpZXM6IHtcbiAgICAgICAgaXBfYWRkcmVzc2VzOiB7XG4gICAgICAgICAgdHlwZTogXCJhcnJheVwiLFxuICAgICAgICAgIGl0ZW1zOiB7IHR5cGU6IFwic3RyaW5nXCIsIGRlc2NyaXB0aW9uOiBcIklQdjQgb3IgSVB2NiBhZGRyZXNzXCIgfSxcbiAgICAgICAgICBtaW5JdGVtczogMSxcbiAgICAgICAgICBtYXhJdGVtczogMjAsXG4gICAgICAgICAgZGVzY3JpcHRpb246IFwiSVAgYWRkcmVzc2VzIHRvIHJlc29sdmVcIixcbiAgICAgICAgfSxcbiAgICAgIH0sXG4gICAgICByZXF1aXJlZDogW1wiaXBfYWRkcmVzc2VzXCJdLFxuICAgICAgYWRkaXRpb25hbFByb3BlcnRpZXM6IGZhbHNlLFxuICAgIH0sXG4gICAgb3V0cHV0OiB7XG4gICAgICB0eXBlOiBcIm9iamVjdFwiLFxuICAgICAgcHJvcGVydGllczoge1xuICAgICAgICByZXN1bHRzOiB7XG4gICAgICAgICAgdHlwZTogXCJhcnJheVwiLFxuICAgICAgICAgIGl0ZW1zOiB7XG4gICAgICAgICAgICB0eXBlOiBcIm9iamVjdFwiLFxuICAgICAgICAgICAgcHJvcGVydGllczoge1xuICAgICAgICAgICAgICBpcDogeyB0eXBlOiBcInN0cmluZ1wiIH0sXG4gICAgICAgICAgICAgIGhvc3RuYW1lOiB7IHR5cGU6IFwic3RyaW5nXCIgfSxcbiAgICAgICAgICAgICAgcmVzb2x2ZWQ6IHsgdHlwZTogXCJib29sZWFuXCIgfSxcbiAgICAgICAgICAgIH0sXG4gICAgICAgICAgICByZXF1aXJlZDogW1wiaXBcIiwgXCJyZXNvbHZlZFwiXSxcbiAgICAgICAgICB9LFxuICAgICAgICB9LFxuICAgICAgfSxcbiAgICAgIHJlcXVpcmVkOiBbXCJyZXN1bHRzXCJdLFxuICAgIH0sXG4gICAgY2F0ZWdvcnk6IFwidXRpbGl0eVwiLFxuICAgIHJlcXVpcmVzQXV0aDogZmFsc2UsXG4gIH0sXG5cbiAgbGlzdF9jYXB0dXJlczoge1xuICAgIGlucHV0OiB7XG4gICAgICB0eXBlOiBcIm9iamVjdFwiLFxuICAgICAgcHJvcGVydGllczoge1xuICAgICAgICBzdGF0dXM6IHsgdHlwZTogXCJzdHJpbmdcIiwgZW51bTogW1wiYWN0aXZlXCIsIFwiY29tcGxldGVkXCIsIFwiZmFpbGVkXCIsIFwiYWxsXCJdLCBkZXNjcmlwdGlvbjogXCJGaWx0ZXIgYnkgY2FwdHVyZSBzdGF0dXNcIiB9LFxuICAgICAgICBsaW1pdDogeyB0eXBlOiBcImludGVnZXJcIiwgbWluaW11bTogMSwgbWF4aW11bTogMTAwLCBkZWZhdWx0OiAyMCwgZGVzY3JpcHRpb246IFwiTWF4aW11bSByZXN1bHRzIHRvIHJldHVyblwiIH0sXG4gICAgICB9LFxuICAgICAgYWRkaXRpb25hbFByb3BlcnRpZXM6IGZhbHNlLFxuICAgIH0sXG4gICAgb3V0cHV0OiB7XG4gICAgICB0eXBlOiBcIm9iamVjdFwiLFxuICAgICAgcHJvcGVydGllczoge1xuICAgICAgICBjYXB0dXJlczoge1xuICAgICAgICAgIHR5cGU6IFwiYXJyYXlcIixcbiAgICAgICAgICBpdGVtczoge1xuICAgICAgICAgICAgdHlwZTogXCJvYmplY3RcIixcbiAgICAgICAgICAgIHByb3BlcnRpZXM6IHtcbiAgICAgICAgICAgICAgY2FwdHVyZV9pZDogeyB0eXBlOiBcInN0cmluZ1wiIH0sXG4gICAgICAgICAgICAgIHN0YXR1czogeyB0eXBlOiBcInN0cmluZ1wiIH0sXG4gICAgICAgICAgICAgIGVuaV9pZHM6IHsgdHlwZTogXCJhcnJheVwiLCBpdGVtczogeyB0eXBlOiBcInN0cmluZ1wiIH0gfSxcbiAgICAgICAgICAgICAgc3RhcnRfdGltZTogdGltZXN0YW1wU2NoZW1hLFxuICAgICAgICAgICAgICBlbmRfdGltZTogdGltZXN0YW1wU2NoZW1hLFxuICAgICAgICAgICAgICBkdXJhdGlvbl9taW51dGVzOiB7IHR5cGU6IFwibnVtYmVyXCIgfSxcbiAgICAgICAgICAgIH0sXG4gICAgICAgICAgICByZXF1aXJlZDogW1wiY2FwdHVyZV9pZFwiLCBcInN0YXR1c1wiXSxcbiAgICAgICAgICB9LFxuICAgICAgICB9LFxuICAgICAgICB0b3RhbF9jb3VudDogeyB0eXBlOiBcImludGVnZXJcIiB9LFxuICAgICAgfSxcbiAgICAgIHJlcXVpcmVkOiBbXCJjYXB0dXJlc1wiLCBcInRvdGFsX2NvdW50XCJdLFxuICAgIH0sXG4gICAgY2F0ZWdvcnk6IFwidXRpbGl0eVwiLFxuICAgIHJlcXVpcmVzQXV0aDogZmFsc2UsXG4gIH0sXG5cbiAgZ2V0X2NhcHR1cmVfcHJvZ3Jlc3M6IHtcbiAgICBpbnB1dDoge1xuICAgICAgdHlwZTogXCJvYmplY3RcIixcbiAgICAgIHByb3BlcnRpZXM6IHtcbiAgICAgICAgY2FwdHVyZV9pZDogY2FwdHVyZUlkU2NoZW1hLFxuICAgICAgfSxcbiAgICAgIHJlcXVpcmVkOiBbXCJjYXB0dXJlX2lkXCJdLFxuICAgICAgYWRkaXRpb25hbFByb3BlcnRpZXM6IGZhbHNlLFxuICAgIH0sXG4gICAgb3V0cHV0OiB7XG4gICAgICB0eXBlOiBcIm9iamVjdFwiLFxuICAgICAgcHJvcGVydGllczoge1xuICAgICAgICBjYXB0dXJlX2lkOiB7IHR5cGU6IFwic3RyaW5nXCIgfSxcbiAgICAgICAgc3RhdHVzOiB7IHR5cGU6IFwic3RyaW5nXCIsIGVudW06IFtcImluaXRpYWxpemluZ1wiLCBcImNhcHR1cmluZ1wiLCBcInN0b3BwaW5nXCIsIFwiY29tcGxldGVkXCIsIFwiZmFpbGVkXCJdIH0sXG4gICAgICAgIHByb2dyZXNzX3BlcmNlbnQ6IHsgdHlwZTogXCJudW1iZXJcIiwgbWluaW11bTogMCwgbWF4aW11bTogMTAwIH0sXG4gICAgICAgIGVsYXBzZWRfc2Vjb25kczogeyB0eXBlOiBcIm51bWJlclwiIH0sXG4gICAgICAgIHBhY2tldHNfY2FwdHVyZWQ6IHsgdHlwZTogXCJpbnRlZ2VyXCIgfSxcbiAgICAgICAgYnl0ZXNfY2FwdHVyZWQ6IHsgdHlwZTogXCJpbnRlZ2VyXCIgfSxcbiAgICAgIH0sXG4gICAgICByZXF1aXJlZDogW1wiY2FwdHVyZV9pZFwiLCBcInN0YXR1c1wiLCBcInByb2dyZXNzX3BlcmNlbnRcIl0sXG4gICAgfSxcbiAgICBjYXRlZ29yeTogXCJ1dGlsaXR5XCIsXG4gICAgcmVxdWlyZXNBdXRoOiBmYWxzZSxcbiAgfSxcblxuICBjbGVhbnVwX29ycGhhbmVkX3Nlc3Npb25zOiB7XG4gICAgaW5wdXQ6IHtcbiAgICAgIHR5cGU6IFwib2JqZWN0XCIsXG4gICAgICBwcm9wZXJ0aWVzOiB7XG4gICAgICAgIG1heF9hZ2VfaG91cnM6IHsgdHlwZTogXCJpbnRlZ2VyXCIsIG1pbmltdW06IDEsIG1heGltdW06IDE2OCwgZGVmYXVsdDogMjQsIGRlc2NyaXB0aW9uOiBcIk1heCBzZXNzaW9uIGFnZSBpbiBob3VycyBiZWZvcmUgY2xlYW51cFwiIH0sXG4gICAgICAgIGRyeV9ydW46IHsgdHlwZTogXCJib29sZWFuXCIsIGRlZmF1bHQ6IGZhbHNlLCBkZXNjcmlwdGlvbjogXCJQcmV2aWV3IGNsZWFudXAgd2l0aG91dCBkZWxldGluZ1wiIH0sXG4gICAgICB9LFxuICAgICAgYWRkaXRpb25hbFByb3BlcnRpZXM6IGZhbHNlLFxuICAgIH0sXG4gICAgb3V0cHV0OiB7XG4gICAgICB0eXBlOiBcIm9iamVjdFwiLFxuICAgICAgcHJvcGVydGllczoge1xuICAgICAgICBjbGVhbmVkX3Nlc3Npb25zOiB7IHR5cGU6IFwiaW50ZWdlclwiLCBkZXNjcmlwdGlvbjogXCJOdW1iZXIgb2Ygc2Vzc2lvbnMgY2xlYW5lZCB1cFwiIH0sXG4gICAgICAgIHNlc3Npb25zOiB7XG4gICAgICAgICAgdHlwZTogXCJhcnJheVwiLFxuICAgICAgICAgIGl0ZW1zOiB7XG4gICAgICAgICAgICB0eXBlOiBcIm9iamVjdFwiLFxuICAgICAgICAgICAgcHJvcGVydGllczoge1xuICAgICAgICAgICAgICBjYXB0dXJlX2lkOiB7IHR5cGU6IFwic3RyaW5nXCIgfSxcbiAgICAgICAgICAgICAgYWdlX2hvdXJzOiB7IHR5cGU6IFwibnVtYmVyXCIgfSxcbiAgICAgICAgICAgICAgc3RhdHVzOiB7IHR5cGU6IFwic3RyaW5nXCIgfSxcbiAgICAgICAgICAgIH0sXG4gICAgICAgICAgICByZXF1aXJlZDogW1wiY2FwdHVyZV9pZFwiLCBcImFnZV9ob3Vyc1wiXSxcbiAgICAgICAgICB9LFxuICAgICAgICB9LFxuICAgICAgICBkcnlfcnVuOiB7IHR5cGU6IFwiYm9vbGVhblwiIH0sXG4gICAgICB9LFxuICAgICAgcmVxdWlyZWQ6IFtcImNsZWFuZWRfc2Vzc2lvbnNcIiwgXCJkcnlfcnVuXCJdLFxuICAgIH0sXG4gICAgY2F0ZWdvcnk6IFwidXRpbGl0eVwiLFxuICAgIHJlcXVpcmVzQXV0aDogdHJ1ZSxcbiAgfSxcblxuICAvLyDilIDilIAgQ2FwdHVyZSBBY3Rpb25zIOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgFxuXG4gIHN0YXJ0X2NhcHR1cmU6IHtcbiAgICBpbnB1dDoge1xuICAgICAgdHlwZTogXCJvYmplY3RcIixcbiAgICAgIHByb3BlcnRpZXM6IHtcbiAgICAgICAgZW5pX2lkczogZW5pSWRzU2NoZW1hLFxuICAgICAgICBkdXJhdGlvbl9taW51dGVzOiBkdXJhdGlvbk1pbnV0ZXNTY2hlbWEsXG4gICAgICAgIHRhcmdldF9ob3N0OiB0YXJnZXRIb3N0U2NoZW1hLFxuICAgICAgICBmaWx0ZXJfZXhwcmVzc2lvbjogeyB0eXBlOiBcInN0cmluZ1wiLCBkZXNjcmlwdGlvbjogXCJCUEYtc3R5bGUgcGFja2V0IGZpbHRlciBleHByZXNzaW9uXCIgfSxcbiAgICAgIH0sXG4gICAgICByZXF1aXJlZDogW1wiZW5pX2lkc1wiXSxcbiAgICAgIGFkZGl0aW9uYWxQcm9wZXJ0aWVzOiBmYWxzZSxcbiAgICB9LFxuICAgIG91dHB1dDoge1xuICAgICAgdHlwZTogXCJvYmplY3RcIixcbiAgICAgIHByb3BlcnRpZXM6IHtcbiAgICAgICAgY2FwdHVyZV9pZDogeyB0eXBlOiBcInN0cmluZ1wiLCBkZXNjcmlwdGlvbjogXCJVbmlxdWUgY2FwdHVyZSBzZXNzaW9uIGlkZW50aWZpZXJcIiB9LFxuICAgICAgICBzdGF0dXM6IHsgdHlwZTogXCJzdHJpbmdcIiwgZW51bTogW1wiaW5pdGlhbGl6aW5nXCIsIFwiY2FwdHVyaW5nXCJdIH0sXG4gICAgICAgIGVuaV9pZHM6IHsgdHlwZTogXCJhcnJheVwiLCBpdGVtczogeyB0eXBlOiBcInN0cmluZ1wiIH0gfSxcbiAgICAgICAgc3RhcnRfdGltZTogdGltZXN0YW1wU2NoZW1hLFxuICAgICAgICBleHBlY3RlZF9lbmRfdGltZTogdGltZXN0YW1wU2NoZW1hLFxuICAgICAgfSxcbiAgICAgIHJlcXVpcmVkOiBbXCJjYXB0dXJlX2lkXCIsIFwic3RhdHVzXCIsIFwiZW5pX2lkc1wiLCBcInN0YXJ0X3RpbWVcIl0sXG4gICAgfSxcbiAgICBjYXRlZ29yeTogXCJjYXB0dXJlXCIsXG4gICAgcmVxdWlyZXNBdXRoOiB0cnVlLFxuICB9LFxuXG4gIHN0b3BfY2FwdHVyZToge1xuICAgIGlucHV0OiB7XG4gICAgICB0eXBlOiBcIm9iamVjdFwiLFxuICAgICAgcHJvcGVydGllczoge1xuICAgICAgICBjYXB0dXJlX2lkOiBjYXB0dXJlSWRTY2hlbWEsXG4gICAgICB9LFxuICAgICAgcmVxdWlyZWQ6IFtcImNhcHR1cmVfaWRcIl0sXG4gICAgICBhZGRpdGlvbmFsUHJvcGVydGllczogZmFsc2UsXG4gICAgfSxcbiAgICBvdXRwdXQ6IHtcbiAgICAgIHR5cGU6IFwib2JqZWN0XCIsXG4gICAgICBwcm9wZXJ0aWVzOiB7XG4gICAgICAgIGNhcHR1cmVfaWQ6IHsgdHlwZTogXCJzdHJpbmdcIiB9LFxuICAgICAgICBzdGF0dXM6IHsgdHlwZTogXCJzdHJpbmdcIiwgZW51bTogW1wiY29tcGxldGVkXCIsIFwiZmFpbGVkXCJdIH0sXG4gICAgICAgIGVuZF90aW1lOiB0aW1lc3RhbXBTY2hlbWEsXG4gICAgICAgIHBhY2tldHNfY2FwdHVyZWQ6IHsgdHlwZTogXCJpbnRlZ2VyXCIgfSxcbiAgICAgICAgYnl0ZXNfY2FwdHVyZWQ6IHsgdHlwZTogXCJpbnRlZ2VyXCIgfSxcbiAgICAgICAgcGNhcF9sb2NhdGlvbjogeyB0eXBlOiBcInN0cmluZ1wiLCBkZXNjcmlwdGlvbjogXCJTMyBVUkkgb2YgY2FwdHVyZWQgcGNhcCBkYXRhXCIgfSxcbiAgICAgIH0sXG4gICAgICByZXF1aXJlZDogW1wiY2FwdHVyZV9pZFwiLCBcInN0YXR1c1wiLCBcImVuZF90aW1lXCJdLFxuICAgIH0sXG4gICAgY2F0ZWdvcnk6IFwiY2FwdHVyZVwiLFxuICAgIHJlcXVpcmVzQXV0aDogdHJ1ZSxcbiAgfSxcblxuICB0cmFuc2Zvcm1fY2FwdHVyZToge1xuICAgIGlucHV0OiB7XG4gICAgICB0eXBlOiBcIm9iamVjdFwiLFxuICAgICAgcHJvcGVydGllczoge1xuICAgICAgICBjYXB0dXJlX2lkOiBjYXB0dXJlSWRTY2hlbWEsXG4gICAgICAgIG91dHB1dF9mb3JtYXQ6IHsgdHlwZTogXCJzdHJpbmdcIiwgZW51bTogW1wicGFycXVldFwiLCBcImpzb25cIiwgXCJjc3ZcIl0sIGRlZmF1bHQ6IFwicGFycXVldFwiLCBkZXNjcmlwdGlvbjogXCJUcmFuc2Zvcm1hdGlvbiBvdXRwdXQgZm9ybWF0XCIgfSxcbiAgICAgIH0sXG4gICAgICByZXF1aXJlZDogW1wiY2FwdHVyZV9pZFwiXSxcbiAgICAgIGFkZGl0aW9uYWxQcm9wZXJ0aWVzOiBmYWxzZSxcbiAgICB9LFxuICAgIG91dHB1dDoge1xuICAgICAgdHlwZTogXCJvYmplY3RcIixcbiAgICAgIHByb3BlcnRpZXM6IHtcbiAgICAgICAgY2FwdHVyZV9pZDogeyB0eXBlOiBcInN0cmluZ1wiIH0sXG4gICAgICAgIHRyYW5zZm9ybWF0aW9uX2lkOiB7IHR5cGU6IFwic3RyaW5nXCIgfSxcbiAgICAgICAgc3RhdHVzOiB7IHR5cGU6IFwic3RyaW5nXCIsIGVudW06IFtcInRyYW5zZm9ybWluZ1wiLCBcImNvbXBsZXRlZFwiLCBcImZhaWxlZFwiXSB9LFxuICAgICAgICBvdXRwdXRfbG9jYXRpb246IHsgdHlwZTogXCJzdHJpbmdcIiwgZGVzY3JpcHRpb246IFwiUzMgVVJJIG9mIHRyYW5zZm9ybWVkIGRhdGFcIiB9LFxuICAgICAgICByZWNvcmRzX3Byb2Nlc3NlZDogeyB0eXBlOiBcImludGVnZXJcIiB9LFxuICAgICAgfSxcbiAgICAgIHJlcXVpcmVkOiBbXCJjYXB0dXJlX2lkXCIsIFwidHJhbnNmb3JtYXRpb25faWRcIiwgXCJzdGF0dXNcIl0sXG4gICAgfSxcbiAgICBjYXRlZ29yeTogXCJjYXB0dXJlXCIsXG4gICAgcmVxdWlyZXNBdXRoOiB0cnVlLFxuICB9LFxuXG4gIC8vIOKUgOKUgCBBbmFseXNpcyBBY3Rpb25zIOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgFxuXG4gIHF1ZXJ5X3BjYXA6IHtcbiAgICBpbnB1dDoge1xuICAgICAgdHlwZTogXCJvYmplY3RcIixcbiAgICAgIHByb3BlcnRpZXM6IHtcbiAgICAgICAgY2FwdHVyZV9pZDogY2FwdHVyZUlkU2NoZW1hLFxuICAgICAgICBzcWw6IHsgdHlwZTogXCJzdHJpbmdcIiwgZGVzY3JpcHRpb246IFwiU1FMIHF1ZXJ5IGFnYWluc3QgcGNhcF9sb2dzIHRhYmxlIChlLmcuLCBTRUxFQ1QgKiBGUk9NIHBjYXBfbG9ncyBXSEVSRSBkc3RfcG9ydCA9IDQ0MyBMSU1JVCAxMClcIiB9LFxuICAgICAgICBsaW1pdDogeyB0eXBlOiBcImludGVnZXJcIiwgbWluaW11bTogMSwgbWF4aW11bTogMTAwMCwgZGVmYXVsdDogMTAwIH0sXG4gICAgICB9LFxuICAgICAgcmVxdWlyZWQ6IFtcImNhcHR1cmVfaWRcIiwgXCJzcWxcIl0sXG4gICAgICBhZGRpdGlvbmFsUHJvcGVydGllczogZmFsc2UsXG4gICAgfSxcbiAgICBvdXRwdXQ6IHtcbiAgICAgIHR5cGU6IFwib2JqZWN0XCIsXG4gICAgICBwcm9wZXJ0aWVzOiB7XG4gICAgICAgIHJvd3M6IHsgdHlwZTogXCJhcnJheVwiLCBpdGVtczogeyB0eXBlOiBcIm9iamVjdFwiIH0gfSxcbiAgICAgICAgcm93X2NvdW50OiB7IHR5cGU6IFwiaW50ZWdlclwiIH0sXG4gICAgICAgIHF1ZXJ5X2V4ZWN1dGlvbl9tczogeyB0eXBlOiBcIm51bWJlclwiIH0sXG4gICAgICB9LFxuICAgICAgcmVxdWlyZWQ6IFtcInJvd3NcIiwgXCJyb3dfY291bnRcIl0sXG4gICAgfSxcbiAgICBjYXRlZ29yeTogXCJhbmFseXNpc1wiLFxuICAgIHJlcXVpcmVzQXV0aDogZmFsc2UsXG4gIH0sXG5cbiAgc2VhcmNoX2ZyYWdtZW50ZWRfcGFja2V0czoge1xuICAgIGlucHV0OiB7XG4gICAgICB0eXBlOiBcIm9iamVjdFwiLFxuICAgICAgcHJvcGVydGllczoge1xuICAgICAgICBjYXB0dXJlX2lkOiBjYXB0dXJlSWRTY2hlbWEsXG4gICAgICAgIHRhcmdldF9ob3N0OiB0YXJnZXRIb3N0U2NoZW1hLFxuICAgICAgICBtaW5fZnJhZ21lbnRfY291bnQ6IHsgdHlwZTogXCJpbnRlZ2VyXCIsIG1pbmltdW06IDIsIGRlZmF1bHQ6IDIsIGRlc2NyaXB0aW9uOiBcIk1pbmltdW0gZnJhZ21lbnRzIHBlciBwYWNrZXQgdG8gZmxhZ1wiIH0sXG4gICAgICB9LFxuICAgICAgcmVxdWlyZWQ6IFtcImNhcHR1cmVfaWRcIl0sXG4gICAgICBhZGRpdGlvbmFsUHJvcGVydGllczogZmFsc2UsXG4gICAgfSxcbiAgICBvdXRwdXQ6IHtcbiAgICAgIHR5cGU6IFwib2JqZWN0XCIsXG4gICAgICBwcm9wZXJ0aWVzOiB7XG4gICAgICAgIGZyYWdtZW50ZWRfcGFja2V0czoge1xuICAgICAgICAgIHR5cGU6IFwiYXJyYXlcIixcbiAgICAgICAgICBpdGVtczoge1xuICAgICAgICAgICAgdHlwZTogXCJvYmplY3RcIixcbiAgICAgICAgICAgIHByb3BlcnRpZXM6IHtcbiAgICAgICAgICAgICAgcGFja2V0X2lkOiB7IHR5cGU6IFwic3RyaW5nXCIgfSxcbiAgICAgICAgICAgICAgZnJhZ21lbnRfY291bnQ6IHsgdHlwZTogXCJpbnRlZ2VyXCIgfSxcbiAgICAgICAgICAgICAgdG90YWxfc2l6ZTogeyB0eXBlOiBcImludGVnZXJcIiB9LFxuICAgICAgICAgICAgICBzb3VyY2VfaXA6IHsgdHlwZTogXCJzdHJpbmdcIiB9LFxuICAgICAgICAgICAgICBkZXN0X2lwOiB7IHR5cGU6IFwic3RyaW5nXCIgfSxcbiAgICAgICAgICAgICAgcHJvdG9jb2w6IHsgdHlwZTogXCJzdHJpbmdcIiB9LFxuICAgICAgICAgICAgfSxcbiAgICAgICAgICAgIHJlcXVpcmVkOiBbXCJwYWNrZXRfaWRcIiwgXCJmcmFnbWVudF9jb3VudFwiXSxcbiAgICAgICAgICB9LFxuICAgICAgICB9LFxuICAgICAgICB0b3RhbF9mcmFnbWVudGVkOiB7IHR5cGU6IFwiaW50ZWdlclwiIH0sXG4gICAgICB9LFxuICAgICAgcmVxdWlyZWQ6IFtcImZyYWdtZW50ZWRfcGFja2V0c1wiLCBcInRvdGFsX2ZyYWdtZW50ZWRcIl0sXG4gICAgfSxcbiAgICBjYXRlZ29yeTogXCJhbmFseXNpc1wiLFxuICAgIHJlcXVpcmVzQXV0aDogZmFsc2UsXG4gIH0sXG5cbiAgY29ycmVsYXRlX3RjcF9zdHJlYW1zOiB7XG4gICAgaW5wdXQ6IHtcbiAgICAgIHR5cGU6IFwib2JqZWN0XCIsXG4gICAgICBwcm9wZXJ0aWVzOiB7XG4gICAgICAgIGNhcHR1cmVfaWQ6IGNhcHR1cmVJZFNjaGVtYSxcbiAgICAgICAgdGFyZ2V0X2hvc3Q6IHRhcmdldEhvc3RTY2hlbWEsXG4gICAgICAgIHBvcnQ6IHsgdHlwZTogXCJpbnRlZ2VyXCIsIG1pbmltdW06IDEsIG1heGltdW06IDY1NTM1LCBkZXNjcmlwdGlvbjogXCJUQ1AgcG9ydCBmaWx0ZXJcIiB9LFxuICAgICAgfSxcbiAgICAgIHJlcXVpcmVkOiBbXCJjYXB0dXJlX2lkXCJdLFxuICAgICAgYWRkaXRpb25hbFByb3BlcnRpZXM6IGZhbHNlLFxuICAgIH0sXG4gICAgb3V0cHV0OiB7XG4gICAgICB0eXBlOiBcIm9iamVjdFwiLFxuICAgICAgcHJvcGVydGllczoge1xuICAgICAgICBzdHJlYW1zOiB7XG4gICAgICAgICAgdHlwZTogXCJhcnJheVwiLFxuICAgICAgICAgIGl0ZW1zOiB7XG4gICAgICAgICAgICB0eXBlOiBcIm9iamVjdFwiLFxuICAgICAgICAgICAgcHJvcGVydGllczoge1xuICAgICAgICAgICAgICBzdHJlYW1faWQ6IHsgdHlwZTogXCJzdHJpbmdcIiB9LFxuICAgICAgICAgICAgICBzb3VyY2U6IHsgdHlwZTogXCJzdHJpbmdcIiB9LFxuICAgICAgICAgICAgICBkZXN0aW5hdGlvbjogeyB0eXBlOiBcInN0cmluZ1wiIH0sXG4gICAgICAgICAgICAgIHBhY2tldHM6IHsgdHlwZTogXCJpbnRlZ2VyXCIgfSxcbiAgICAgICAgICAgICAgYnl0ZXM6IHsgdHlwZTogXCJpbnRlZ2VyXCIgfSxcbiAgICAgICAgICAgICAgZHVyYXRpb25fbXM6IHsgdHlwZTogXCJudW1iZXJcIiB9LFxuICAgICAgICAgICAgICBzdGF0ZTogeyB0eXBlOiBcInN0cmluZ1wiIH0sXG4gICAgICAgICAgICB9LFxuICAgICAgICAgICAgcmVxdWlyZWQ6IFtcInN0cmVhbV9pZFwiLCBcInNvdXJjZVwiLCBcImRlc3RpbmF0aW9uXCIsIFwicGFja2V0c1wiXSxcbiAgICAgICAgICB9LFxuICAgICAgICB9LFxuICAgICAgICB0b3RhbF9zdHJlYW1zOiB7IHR5cGU6IFwiaW50ZWdlclwiIH0sXG4gICAgICB9LFxuICAgICAgcmVxdWlyZWQ6IFtcInN0cmVhbXNcIiwgXCJ0b3RhbF9zdHJlYW1zXCJdLFxuICAgIH0sXG4gICAgY2F0ZWdvcnk6IFwiYW5hbHlzaXNcIixcbiAgICByZXF1aXJlc0F1dGg6IGZhbHNlLFxuICB9LFxuXG4gIGRldGVjdF9yZXRyYW5zbWlzc2lvbnM6IHtcbiAgICBpbnB1dDoge1xuICAgICAgdHlwZTogXCJvYmplY3RcIixcbiAgICAgIHByb3BlcnRpZXM6IHtcbiAgICAgICAgY2FwdHVyZV9pZDogY2FwdHVyZUlkU2NoZW1hLFxuICAgICAgICB0YXJnZXRfaG9zdDogdGFyZ2V0SG9zdFNjaGVtYSxcbiAgICAgICAgdGhyZXNob2xkX3BlcmNlbnQ6IHsgdHlwZTogXCJudW1iZXJcIiwgbWluaW11bTogMCwgbWF4aW11bTogMTAwLCBkZWZhdWx0OiA1LCBkZXNjcmlwdGlvbjogXCJSZXRyYW5zbWlzc2lvbiByYXRlIHRocmVzaG9sZCB0byBmbGFnXCIgfSxcbiAgICAgIH0sXG4gICAgICByZXF1aXJlZDogW1wiY2FwdHVyZV9pZFwiXSxcbiAgICAgIGFkZGl0aW9uYWxQcm9wZXJ0aWVzOiBmYWxzZSxcbiAgICB9LFxuICAgIG91dHB1dDoge1xuICAgICAgdHlwZTogXCJvYmplY3RcIixcbiAgICAgIHByb3BlcnRpZXM6IHtcbiAgICAgICAgcmV0cmFuc21pc3Npb25fcmF0ZTogeyB0eXBlOiBcIm51bWJlclwiLCBkZXNjcmlwdGlvbjogXCJPdmVyYWxsIHJldHJhbnNtaXNzaW9uIHBlcmNlbnRhZ2VcIiB9LFxuICAgICAgICB0b3RhbF9wYWNrZXRzOiB7IHR5cGU6IFwiaW50ZWdlclwiIH0sXG4gICAgICAgIHJldHJhbnNtaXR0ZWRfcGFja2V0czogeyB0eXBlOiBcImludGVnZXJcIiB9LFxuICAgICAgICBieV9zdHJlYW06IHtcbiAgICAgICAgICB0eXBlOiBcImFycmF5XCIsXG4gICAgICAgICAgaXRlbXM6IHtcbiAgICAgICAgICAgIHR5cGU6IFwib2JqZWN0XCIsXG4gICAgICAgICAgICBwcm9wZXJ0aWVzOiB7XG4gICAgICAgICAgICAgIHN0cmVhbV9pZDogeyB0eXBlOiBcInN0cmluZ1wiIH0sXG4gICAgICAgICAgICAgIHJhdGU6IHsgdHlwZTogXCJudW1iZXJcIiB9LFxuICAgICAgICAgICAgICBjb3VudDogeyB0eXBlOiBcImludGVnZXJcIiB9LFxuICAgICAgICAgICAgfSxcbiAgICAgICAgICAgIHJlcXVpcmVkOiBbXCJzdHJlYW1faWRcIiwgXCJyYXRlXCIsIFwiY291bnRcIl0sXG4gICAgICAgICAgfSxcbiAgICAgICAgfSxcbiAgICAgIH0sXG4gICAgICByZXF1aXJlZDogW1wicmV0cmFuc21pc3Npb25fcmF0ZVwiLCBcInRvdGFsX3BhY2tldHNcIiwgXCJyZXRyYW5zbWl0dGVkX3BhY2tldHNcIl0sXG4gICAgfSxcbiAgICBjYXRlZ29yeTogXCJhbmFseXNpc1wiLFxuICAgIHJlcXVpcmVzQXV0aDogZmFsc2UsXG4gIH0sXG5cbiAgY2hlY2tfdGxzX2hlbGxvX3NpemU6IHtcbiAgICBpbnB1dDoge1xuICAgICAgdHlwZTogXCJvYmplY3RcIixcbiAgICAgIHByb3BlcnRpZXM6IHtcbiAgICAgICAgY2FwdHVyZV9pZDogY2FwdHVyZUlkU2NoZW1hLFxuICAgICAgICB0YXJnZXRfaG9zdDogdGFyZ2V0SG9zdFNjaGVtYSxcbiAgICAgICAgbWF4X2hlbGxvX3NpemU6IHsgdHlwZTogXCJpbnRlZ2VyXCIsIG1pbmltdW06IDEsIGRlZmF1bHQ6IDUxMiwgZGVzY3JpcHRpb246IFwiTWF4aW11bSBleHBlY3RlZCBDbGllbnRIZWxsbyBzaXplIGluIGJ5dGVzXCIgfSxcbiAgICAgIH0sXG4gICAgICByZXF1aXJlZDogW1wiY2FwdHVyZV9pZFwiXSxcbiAgICAgIGFkZGl0aW9uYWxQcm9wZXJ0aWVzOiBmYWxzZSxcbiAgICB9LFxuICAgIG91dHB1dDoge1xuICAgICAgdHlwZTogXCJvYmplY3RcIixcbiAgICAgIHByb3BlcnRpZXM6IHtcbiAgICAgICAgb3ZlcnNpemVkX2hlbGxvczoge1xuICAgICAgICAgIHR5cGU6IFwiYXJyYXlcIixcbiAgICAgICAgICBpdGVtczoge1xuICAgICAgICAgICAgdHlwZTogXCJvYmplY3RcIixcbiAgICAgICAgICAgIHByb3BlcnRpZXM6IHtcbiAgICAgICAgICAgICAgc3RyZWFtX2lkOiB7IHR5cGU6IFwic3RyaW5nXCIgfSxcbiAgICAgICAgICAgICAgY2xpZW50X2hlbGxvX3NpemU6IHsgdHlwZTogXCJpbnRlZ2VyXCIgfSxcbiAgICAgICAgICAgICAgc291cmNlX2lwOiB7IHR5cGU6IFwic3RyaW5nXCIgfSxcbiAgICAgICAgICAgICAgZGVzdF9pcDogeyB0eXBlOiBcInN0cmluZ1wiIH0sXG4gICAgICAgICAgICAgIHNuaTogeyB0eXBlOiBcInN0cmluZ1wiIH0sXG4gICAgICAgICAgICAgIGZyYWdtZW50ZWQ6IHsgdHlwZTogXCJib29sZWFuXCIgfSxcbiAgICAgICAgICAgIH0sXG4gICAgICAgICAgICByZXF1aXJlZDogW1wic3RyZWFtX2lkXCIsIFwiY2xpZW50X2hlbGxvX3NpemVcIl0sXG4gICAgICAgICAgfSxcbiAgICAgICAgfSxcbiAgICAgICAgdG90YWxfdGxzX2hhbmRzaGFrZXM6IHsgdHlwZTogXCJpbnRlZ2VyXCIgfSxcbiAgICAgICAgb3ZlcnNpemVkX2NvdW50OiB7IHR5cGU6IFwiaW50ZWdlclwiIH0sXG4gICAgICB9LFxuICAgICAgcmVxdWlyZWQ6IFtcIm92ZXJzaXplZF9oZWxsb3NcIiwgXCJ0b3RhbF90bHNfaGFuZHNoYWtlc1wiLCBcIm92ZXJzaXplZF9jb3VudFwiXSxcbiAgICB9LFxuICAgIGNhdGVnb3J5OiBcImFuYWx5c2lzXCIsXG4gICAgcmVxdWlyZXNBdXRoOiBmYWxzZSxcbiAgfSxcblxuICBnZXRfY29udmVyc2F0aW9uX3N0YXRzOiB7XG4gICAgaW5wdXQ6IHtcbiAgICAgIHR5cGU6IFwib2JqZWN0XCIsXG4gICAgICBwcm9wZXJ0aWVzOiB7XG4gICAgICAgIGNhcHR1cmVfaWQ6IGNhcHR1cmVJZFNjaGVtYSxcbiAgICAgICAgdGFyZ2V0X2hvc3Q6IHRhcmdldEhvc3RTY2hlbWEsXG4gICAgICAgIHRvcF9uOiB7IHR5cGU6IFwiaW50ZWdlclwiLCBtaW5pbXVtOiAxLCBtYXhpbXVtOiA1MCwgZGVmYXVsdDogMTAsIGRlc2NyaXB0aW9uOiBcIk51bWJlciBvZiB0b3AgY29udmVyc2F0aW9ucyB0byByZXR1cm5cIiB9LFxuICAgICAgfSxcbiAgICAgIHJlcXVpcmVkOiBbXCJjYXB0dXJlX2lkXCJdLFxuICAgICAgYWRkaXRpb25hbFByb3BlcnRpZXM6IGZhbHNlLFxuICAgIH0sXG4gICAgb3V0cHV0OiB7XG4gICAgICB0eXBlOiBcIm9iamVjdFwiLFxuICAgICAgcHJvcGVydGllczoge1xuICAgICAgICBjb252ZXJzYXRpb25zOiB7XG4gICAgICAgICAgdHlwZTogXCJhcnJheVwiLFxuICAgICAgICAgIGl0ZW1zOiB7XG4gICAgICAgICAgICB0eXBlOiBcIm9iamVjdFwiLFxuICAgICAgICAgICAgcHJvcGVydGllczoge1xuICAgICAgICAgICAgICBzb3VyY2U6IHsgdHlwZTogXCJzdHJpbmdcIiB9LFxuICAgICAgICAgICAgICBkZXN0aW5hdGlvbjogeyB0eXBlOiBcInN0cmluZ1wiIH0sXG4gICAgICAgICAgICAgIHByb3RvY29sOiB7IHR5cGU6IFwic3RyaW5nXCIgfSxcbiAgICAgICAgICAgICAgcGFja2V0czogeyB0eXBlOiBcImludGVnZXJcIiB9LFxuICAgICAgICAgICAgICBieXRlczogeyB0eXBlOiBcImludGVnZXJcIiB9LFxuICAgICAgICAgICAgICBkdXJhdGlvbl9tczogeyB0eXBlOiBcIm51bWJlclwiIH0sXG4gICAgICAgICAgICB9LFxuICAgICAgICAgICAgcmVxdWlyZWQ6IFtcInNvdXJjZVwiLCBcImRlc3RpbmF0aW9uXCIsIFwicGFja2V0c1wiLCBcImJ5dGVzXCJdLFxuICAgICAgICAgIH0sXG4gICAgICAgIH0sXG4gICAgICAgIHRvdGFsX2NvbnZlcnNhdGlvbnM6IHsgdHlwZTogXCJpbnRlZ2VyXCIgfSxcbiAgICAgICAgdG90YWxfYnl0ZXM6IHsgdHlwZTogXCJpbnRlZ2VyXCIgfSxcbiAgICAgIH0sXG4gICAgICByZXF1aXJlZDogW1wiY29udmVyc2F0aW9uc1wiLCBcInRvdGFsX2NvbnZlcnNhdGlvbnNcIl0sXG4gICAgfSxcbiAgICBjYXRlZ29yeTogXCJhbmFseXNpc1wiLFxuICAgIHJlcXVpcmVzQXV0aDogZmFsc2UsXG4gIH0sXG5cbiAgcmVjb25zdHJ1Y3RfdGNwX2hhbmRzaGFrZToge1xuICAgIGlucHV0OiB7XG4gICAgICB0eXBlOiBcIm9iamVjdFwiLFxuICAgICAgcHJvcGVydGllczoge1xuICAgICAgICBjYXB0dXJlX2lkOiBjYXB0dXJlSWRTY2hlbWEsXG4gICAgICAgIHN0cmVhbV9pZDogeyB0eXBlOiBcInN0cmluZ1wiLCBkZXNjcmlwdGlvbjogXCJTcGVjaWZpYyBUQ1Agc3RyZWFtIHRvIHJlY29uc3RydWN0XCIgfSxcbiAgICAgICAgdGFyZ2V0X2hvc3Q6IHRhcmdldEhvc3RTY2hlbWEsXG4gICAgICB9LFxuICAgICAgcmVxdWlyZWQ6IFtcImNhcHR1cmVfaWRcIl0sXG4gICAgICBhZGRpdGlvbmFsUHJvcGVydGllczogZmFsc2UsXG4gICAgfSxcbiAgICBvdXRwdXQ6IHtcbiAgICAgIHR5cGU6IFwib2JqZWN0XCIsXG4gICAgICBwcm9wZXJ0aWVzOiB7XG4gICAgICAgIGhhbmRzaGFrZXM6IHtcbiAgICAgICAgICB0eXBlOiBcImFycmF5XCIsXG4gICAgICAgICAgaXRlbXM6IHtcbiAgICAgICAgICAgIHR5cGU6IFwib2JqZWN0XCIsXG4gICAgICAgICAgICBwcm9wZXJ0aWVzOiB7XG4gICAgICAgICAgICAgIHN0cmVhbV9pZDogeyB0eXBlOiBcInN0cmluZ1wiIH0sXG4gICAgICAgICAgICAgIHN5bl90aW1lOiB0aW1lc3RhbXBTY2hlbWEsXG4gICAgICAgICAgICAgIHN5bl9hY2tfdGltZTogdGltZXN0YW1wU2NoZW1hLFxuICAgICAgICAgICAgICBhY2tfdGltZTogdGltZXN0YW1wU2NoZW1hLFxuICAgICAgICAgICAgICBoYW5kc2hha2VfZHVyYXRpb25fbXM6IHsgdHlwZTogXCJudW1iZXJcIiB9LFxuICAgICAgICAgICAgICBjb21wbGV0ZWQ6IHsgdHlwZTogXCJib29sZWFuXCIgfSxcbiAgICAgICAgICAgICAgZmFpbHVyZV9yZWFzb246IHsgdHlwZTogXCJzdHJpbmdcIiB9LFxuICAgICAgICAgICAgfSxcbiAgICAgICAgICAgIHJlcXVpcmVkOiBbXCJzdHJlYW1faWRcIiwgXCJjb21wbGV0ZWRcIl0sXG4gICAgICAgICAgfSxcbiAgICAgICAgfSxcbiAgICAgICAgdG90YWxfaGFuZHNoYWtlczogeyB0eXBlOiBcImludGVnZXJcIiB9LFxuICAgICAgICBzdWNjZXNzZnVsOiB7IHR5cGU6IFwiaW50ZWdlclwiIH0sXG4gICAgICAgIGZhaWxlZDogeyB0eXBlOiBcImludGVnZXJcIiB9LFxuICAgICAgfSxcbiAgICAgIHJlcXVpcmVkOiBbXCJoYW5kc2hha2VzXCIsIFwidG90YWxfaGFuZHNoYWtlc1wiLCBcInN1Y2Nlc3NmdWxcIiwgXCJmYWlsZWRcIl0sXG4gICAgfSxcbiAgICBjYXRlZ29yeTogXCJhbmFseXNpc1wiLFxuICAgIHJlcXVpcmVzQXV0aDogZmFsc2UsXG4gIH0sXG5cbiAgY2xhc3NpZnlfdGNwX3Jlc2V0czoge1xuICAgIGlucHV0OiB7XG4gICAgICB0eXBlOiBcIm9iamVjdFwiLFxuICAgICAgcHJvcGVydGllczoge1xuICAgICAgICBjYXB0dXJlX2lkOiBjYXB0dXJlSWRTY2hlbWEsXG4gICAgICAgIHRhcmdldF9ob3N0OiB0YXJnZXRIb3N0U2NoZW1hLFxuICAgICAgfSxcbiAgICAgIHJlcXVpcmVkOiBbXCJjYXB0dXJlX2lkXCJdLFxuICAgICAgYWRkaXRpb25hbFByb3BlcnRpZXM6IGZhbHNlLFxuICAgIH0sXG4gICAgb3V0cHV0OiB7XG4gICAgICB0eXBlOiBcIm9iamVjdFwiLFxuICAgICAgcHJvcGVydGllczoge1xuICAgICAgICByZXNldHM6IHtcbiAgICAgICAgICB0eXBlOiBcImFycmF5XCIsXG4gICAgICAgICAgaXRlbXM6IHtcbiAgICAgICAgICAgIHR5cGU6IFwib2JqZWN0XCIsXG4gICAgICAgICAgICBwcm9wZXJ0aWVzOiB7XG4gICAgICAgICAgICAgIHN0cmVhbV9pZDogeyB0eXBlOiBcInN0cmluZ1wiIH0sXG4gICAgICAgICAgICAgIHNvdXJjZV9pcDogeyB0eXBlOiBcInN0cmluZ1wiIH0sXG4gICAgICAgICAgICAgIG9yaWdpbjogeyB0eXBlOiBcInN0cmluZ1wiLCBlbnVtOiBbXCJjbGllbnRcIiwgXCJzZXJ2ZXJcIiwgXCJtaWRkbGVib3hcIiwgXCJ1bmtub3duXCJdIH0sXG4gICAgICAgICAgICAgIHRpbWluZ19tczogeyB0eXBlOiBcIm51bWJlclwiLCBkZXNjcmlwdGlvbjogXCJUaW1lIHNpbmNlIGNvbm5lY3Rpb24gaW5pdGlhdGlvblwiIH0sXG4gICAgICAgICAgICAgIGNsYXNzaWZpY2F0aW9uOiB7IHR5cGU6IFwic3RyaW5nXCIsIGRlc2NyaXB0aW9uOiBcIlJlc2V0IGNsYXNzaWZpY2F0aW9uIChncmFjZWZ1bCwgYWJydXB0LCB0aW1lb3V0KVwiIH0sXG4gICAgICAgICAgICB9LFxuICAgICAgICAgICAgcmVxdWlyZWQ6IFtcInN0cmVhbV9pZFwiLCBcInNvdXJjZV9pcFwiLCBcIm9yaWdpblwiXSxcbiAgICAgICAgICB9LFxuICAgICAgICB9LFxuICAgICAgICB0b3RhbF9yZXNldHM6IHsgdHlwZTogXCJpbnRlZ2VyXCIgfSxcbiAgICAgICAgYnlfb3JpZ2luOiB7XG4gICAgICAgICAgdHlwZTogXCJvYmplY3RcIixcbiAgICAgICAgICBwcm9wZXJ0aWVzOiB7XG4gICAgICAgICAgICBjbGllbnQ6IHsgdHlwZTogXCJpbnRlZ2VyXCIgfSxcbiAgICAgICAgICAgIHNlcnZlcjogeyB0eXBlOiBcImludGVnZXJcIiB9LFxuICAgICAgICAgICAgbWlkZGxlYm94OiB7IHR5cGU6IFwiaW50ZWdlclwiIH0sXG4gICAgICAgICAgICB1bmtub3duOiB7IHR5cGU6IFwiaW50ZWdlclwiIH0sXG4gICAgICAgICAgfSxcbiAgICAgICAgfSxcbiAgICAgIH0sXG4gICAgICByZXF1aXJlZDogW1wicmVzZXRzXCIsIFwidG90YWxfcmVzZXRzXCJdLFxuICAgIH0sXG4gICAgY2F0ZWdvcnk6IFwiYW5hbHlzaXNcIixcbiAgICByZXF1aXJlc0F1dGg6IGZhbHNlLFxuICB9LFxuXG4gIGRldGVjdF9vdXRfb2Zfb3JkZXJfcGFja2V0czoge1xuICAgIGlucHV0OiB7XG4gICAgICB0eXBlOiBcIm9iamVjdFwiLFxuICAgICAgcHJvcGVydGllczoge1xuICAgICAgICBjYXB0dXJlX2lkOiBjYXB0dXJlSWRTY2hlbWEsXG4gICAgICAgIHRhcmdldF9ob3N0OiB0YXJnZXRIb3N0U2NoZW1hLFxuICAgICAgICBzZW5zaXRpdml0eTogeyB0eXBlOiBcInN0cmluZ1wiLCBlbnVtOiBbXCJsb3dcIiwgXCJtZWRpdW1cIiwgXCJoaWdoXCJdLCBkZWZhdWx0OiBcIm1lZGl1bVwiLCBkZXNjcmlwdGlvbjogXCJEZXRlY3Rpb24gc2Vuc2l0aXZpdHlcIiB9LFxuICAgICAgfSxcbiAgICAgIHJlcXVpcmVkOiBbXCJjYXB0dXJlX2lkXCJdLFxuICAgICAgYWRkaXRpb25hbFByb3BlcnRpZXM6IGZhbHNlLFxuICAgIH0sXG4gICAgb3V0cHV0OiB7XG4gICAgICB0eXBlOiBcIm9iamVjdFwiLFxuICAgICAgcHJvcGVydGllczoge1xuICAgICAgICBvdXRfb2Zfb3JkZXJfY291bnQ6IHsgdHlwZTogXCJpbnRlZ2VyXCIgfSxcbiAgICAgICAgdG90YWxfcGFja2V0czogeyB0eXBlOiBcImludGVnZXJcIiB9LFxuICAgICAgICByYXRlX3BlcmNlbnQ6IHsgdHlwZTogXCJudW1iZXJcIiB9LFxuICAgICAgICBhZmZlY3RlZF9zdHJlYW1zOiB7XG4gICAgICAgICAgdHlwZTogXCJhcnJheVwiLFxuICAgICAgICAgIGl0ZW1zOiB7XG4gICAgICAgICAgICB0eXBlOiBcIm9iamVjdFwiLFxuICAgICAgICAgICAgcHJvcGVydGllczoge1xuICAgICAgICAgICAgICBzdHJlYW1faWQ6IHsgdHlwZTogXCJzdHJpbmdcIiB9LFxuICAgICAgICAgICAgICBvdXRfb2Zfb3JkZXJfY291bnQ6IHsgdHlwZTogXCJpbnRlZ2VyXCIgfSxcbiAgICAgICAgICAgICAgdG90YWxfcGFja2V0czogeyB0eXBlOiBcImludGVnZXJcIiB9LFxuICAgICAgICAgICAgfSxcbiAgICAgICAgICAgIHJlcXVpcmVkOiBbXCJzdHJlYW1faWRcIiwgXCJvdXRfb2Zfb3JkZXJfY291bnRcIl0sXG4gICAgICAgICAgfSxcbiAgICAgICAgfSxcbiAgICAgIH0sXG4gICAgICByZXF1aXJlZDogW1wib3V0X29mX29yZGVyX2NvdW50XCIsIFwidG90YWxfcGFja2V0c1wiLCBcInJhdGVfcGVyY2VudFwiXSxcbiAgICB9LFxuICAgIGNhdGVnb3J5OiBcImFuYWx5c2lzXCIsXG4gICAgcmVxdWlyZXNBdXRoOiBmYWxzZSxcbiAgfSxcblxuICBkZXRlY3RfemVyb193aW5kb3c6IHtcbiAgICBpbnB1dDoge1xuICAgICAgdHlwZTogXCJvYmplY3RcIixcbiAgICAgIHByb3BlcnRpZXM6IHtcbiAgICAgICAgY2FwdHVyZV9pZDogY2FwdHVyZUlkU2NoZW1hLFxuICAgICAgICB0YXJnZXRfaG9zdDogdGFyZ2V0SG9zdFNjaGVtYSxcbiAgICAgICAgbWluX2R1cmF0aW9uX21zOiB7IHR5cGU6IFwibnVtYmVyXCIsIG1pbmltdW06IDAsIGRlZmF1bHQ6IDEwMCwgZGVzY3JpcHRpb246IFwiTWluaW11bSB6ZXJvLXdpbmRvdyBkdXJhdGlvbiB0byByZXBvcnQgKG1zKVwiIH0sXG4gICAgICB9LFxuICAgICAgcmVxdWlyZWQ6IFtcImNhcHR1cmVfaWRcIl0sXG4gICAgICBhZGRpdGlvbmFsUHJvcGVydGllczogZmFsc2UsXG4gICAgfSxcbiAgICBvdXRwdXQ6IHtcbiAgICAgIHR5cGU6IFwib2JqZWN0XCIsXG4gICAgICBwcm9wZXJ0aWVzOiB7XG4gICAgICAgIHplcm9fd2luZG93X2V2ZW50czoge1xuICAgICAgICAgIHR5cGU6IFwiYXJyYXlcIixcbiAgICAgICAgICBpdGVtczoge1xuICAgICAgICAgICAgdHlwZTogXCJvYmplY3RcIixcbiAgICAgICAgICAgIHByb3BlcnRpZXM6IHtcbiAgICAgICAgICAgICAgc3RyZWFtX2lkOiB7IHR5cGU6IFwic3RyaW5nXCIgfSxcbiAgICAgICAgICAgICAgc291cmNlX2lwOiB7IHR5cGU6IFwic3RyaW5nXCIgfSxcbiAgICAgICAgICAgICAgZHVyYXRpb25fbXM6IHsgdHlwZTogXCJudW1iZXJcIiB9LFxuICAgICAgICAgICAgICB0aW1lc3RhbXA6IHRpbWVzdGFtcFNjaGVtYSxcbiAgICAgICAgICAgICAgcmVjb3ZlcmVkOiB7IHR5cGU6IFwiYm9vbGVhblwiIH0sXG4gICAgICAgICAgICB9LFxuICAgICAgICAgICAgcmVxdWlyZWQ6IFtcInN0cmVhbV9pZFwiLCBcInNvdXJjZV9pcFwiLCBcImR1cmF0aW9uX21zXCJdLFxuICAgICAgICAgIH0sXG4gICAgICAgIH0sXG4gICAgICAgIHRvdGFsX2V2ZW50czogeyB0eXBlOiBcImludGVnZXJcIiB9LFxuICAgICAgICBhZmZlY3RlZF9zdHJlYW1zOiB7IHR5cGU6IFwiaW50ZWdlclwiIH0sXG4gICAgICB9LFxuICAgICAgcmVxdWlyZWQ6IFtcInplcm9fd2luZG93X2V2ZW50c1wiLCBcInRvdGFsX2V2ZW50c1wiXSxcbiAgICB9LFxuICAgIGNhdGVnb3J5OiBcImFuYWx5c2lzXCIsXG4gICAgcmVxdWlyZXNBdXRoOiBmYWxzZSxcbiAgfSxcblxuICBhbmFseXplX3RjcF9vcHRpb25zOiB7XG4gICAgaW5wdXQ6IHtcbiAgICAgIHR5cGU6IFwib2JqZWN0XCIsXG4gICAgICBwcm9wZXJ0aWVzOiB7XG4gICAgICAgIGNhcHR1cmVfaWQ6IGNhcHR1cmVJZFNjaGVtYSxcbiAgICAgICAgdGFyZ2V0X2hvc3Q6IHRhcmdldEhvc3RTY2hlbWEsXG4gICAgICAgIHN0cmVhbV9pZDogeyB0eXBlOiBcInN0cmluZ1wiLCBkZXNjcmlwdGlvbjogXCJTcGVjaWZpYyBzdHJlYW0gdG8gYW5hbHl6ZVwiIH0sXG4gICAgICB9LFxuICAgICAgcmVxdWlyZWQ6IFtcImNhcHR1cmVfaWRcIl0sXG4gICAgICBhZGRpdGlvbmFsUHJvcGVydGllczogZmFsc2UsXG4gICAgfSxcbiAgICBvdXRwdXQ6IHtcbiAgICAgIHR5cGU6IFwib2JqZWN0XCIsXG4gICAgICBwcm9wZXJ0aWVzOiB7XG4gICAgICAgIG9wdGlvbnNfc3VtbWFyeToge1xuICAgICAgICAgIHR5cGU6IFwiYXJyYXlcIixcbiAgICAgICAgICBpdGVtczoge1xuICAgICAgICAgICAgdHlwZTogXCJvYmplY3RcIixcbiAgICAgICAgICAgIHByb3BlcnRpZXM6IHtcbiAgICAgICAgICAgICAgc3RyZWFtX2lkOiB7IHR5cGU6IFwic3RyaW5nXCIgfSxcbiAgICAgICAgICAgICAgbXNzOiB7IHR5cGU6IFwiaW50ZWdlclwiLCBkZXNjcmlwdGlvbjogXCJNYXhpbXVtIFNlZ21lbnQgU2l6ZVwiIH0sXG4gICAgICAgICAgICAgIHdpbmRvd19zY2FsZTogeyB0eXBlOiBcImludGVnZXJcIiB9LFxuICAgICAgICAgICAgICBzYWNrX3Blcm1pdHRlZDogeyB0eXBlOiBcImJvb2xlYW5cIiB9LFxuICAgICAgICAgICAgICB0aW1lc3RhbXBzOiB7IHR5cGU6IFwiYm9vbGVhblwiIH0sXG4gICAgICAgICAgICAgIGVjbjogeyB0eXBlOiBcImJvb2xlYW5cIiB9LFxuICAgICAgICAgICAgfSxcbiAgICAgICAgICAgIHJlcXVpcmVkOiBbXCJzdHJlYW1faWRcIl0sXG4gICAgICAgICAgfSxcbiAgICAgICAgfSxcbiAgICAgICAgbWlzbWF0Y2hfd2FybmluZ3M6IHtcbiAgICAgICAgICB0eXBlOiBcImFycmF5XCIsXG4gICAgICAgICAgaXRlbXM6IHsgdHlwZTogXCJzdHJpbmdcIiB9LFxuICAgICAgICAgIGRlc2NyaXB0aW9uOiBcIldhcm5pbmdzIGFib3V0IFRDUCBvcHRpb24gbWlzbWF0Y2hlcyBiZXR3ZWVuIHBlZXJzXCIsXG4gICAgICAgIH0sXG4gICAgICB9LFxuICAgICAgcmVxdWlyZWQ6IFtcIm9wdGlvbnNfc3VtbWFyeVwiXSxcbiAgICB9LFxuICAgIGNhdGVnb3J5OiBcImFuYWx5c2lzXCIsXG4gICAgcmVxdWlyZXNBdXRoOiBmYWxzZSxcbiAgfSxcblxuICBnZXRfcnR0X2Rpc3RyaWJ1dGlvbjoge1xuICAgIGlucHV0OiB7XG4gICAgICB0eXBlOiBcIm9iamVjdFwiLFxuICAgICAgcHJvcGVydGllczoge1xuICAgICAgICBjYXB0dXJlX2lkOiBjYXB0dXJlSWRTY2hlbWEsXG4gICAgICAgIHRhcmdldF9ob3N0OiB0YXJnZXRIb3N0U2NoZW1hLFxuICAgICAgICBidWNrZXRzOiB7IHR5cGU6IFwiaW50ZWdlclwiLCBtaW5pbXVtOiA1LCBtYXhpbXVtOiAxMDAsIGRlZmF1bHQ6IDIwLCBkZXNjcmlwdGlvbjogXCJOdW1iZXIgb2YgaGlzdG9ncmFtIGJ1Y2tldHNcIiB9LFxuICAgICAgfSxcbiAgICAgIHJlcXVpcmVkOiBbXCJjYXB0dXJlX2lkXCJdLFxuICAgICAgYWRkaXRpb25hbFByb3BlcnRpZXM6IGZhbHNlLFxuICAgIH0sXG4gICAgb3V0cHV0OiB7XG4gICAgICB0eXBlOiBcIm9iamVjdFwiLFxuICAgICAgcHJvcGVydGllczoge1xuICAgICAgICBkaXN0cmlidXRpb246IHtcbiAgICAgICAgICB0eXBlOiBcImFycmF5XCIsXG4gICAgICAgICAgaXRlbXM6IHtcbiAgICAgICAgICAgIHR5cGU6IFwib2JqZWN0XCIsXG4gICAgICAgICAgICBwcm9wZXJ0aWVzOiB7XG4gICAgICAgICAgICAgIHJhbmdlX21zOiB7IHR5cGU6IFwic3RyaW5nXCIsIGRlc2NyaXB0aW9uOiBcIkJ1Y2tldCByYW5nZSAoZS5nLiwgJzAtMTBtcycpXCIgfSxcbiAgICAgICAgICAgICAgY291bnQ6IHsgdHlwZTogXCJpbnRlZ2VyXCIgfSxcbiAgICAgICAgICAgICAgcGVyY2VudDogeyB0eXBlOiBcIm51bWJlclwiIH0sXG4gICAgICAgICAgICB9LFxuICAgICAgICAgICAgcmVxdWlyZWQ6IFtcInJhbmdlX21zXCIsIFwiY291bnRcIiwgXCJwZXJjZW50XCJdLFxuICAgICAgICAgIH0sXG4gICAgICAgIH0sXG4gICAgICAgIHN0YXRpc3RpY3M6IHtcbiAgICAgICAgICB0eXBlOiBcIm9iamVjdFwiLFxuICAgICAgICAgIHByb3BlcnRpZXM6IHtcbiAgICAgICAgICAgIG1pbl9tczogeyB0eXBlOiBcIm51bWJlclwiIH0sXG4gICAgICAgICAgICBtYXhfbXM6IHsgdHlwZTogXCJudW1iZXJcIiB9LFxuICAgICAgICAgICAgYXZnX21zOiB7IHR5cGU6IFwibnVtYmVyXCIgfSxcbiAgICAgICAgICAgIHA1MF9tczogeyB0eXBlOiBcIm51bWJlclwiIH0sXG4gICAgICAgICAgICBwOTVfbXM6IHsgdHlwZTogXCJudW1iZXJcIiB9LFxuICAgICAgICAgICAgcDk5X21zOiB7IHR5cGU6IFwibnVtYmVyXCIgfSxcbiAgICAgICAgICB9LFxuICAgICAgICAgIHJlcXVpcmVkOiBbXCJtaW5fbXNcIiwgXCJtYXhfbXNcIiwgXCJhdmdfbXNcIiwgXCJwNTBfbXNcIiwgXCJwOTVfbXNcIiwgXCJwOTlfbXNcIl0sXG4gICAgICAgIH0sXG4gICAgICAgIHNhbXBsZV9jb3VudDogeyB0eXBlOiBcImludGVnZXJcIiB9LFxuICAgICAgfSxcbiAgICAgIHJlcXVpcmVkOiBbXCJkaXN0cmlidXRpb25cIiwgXCJzdGF0aXN0aWNzXCIsIFwic2FtcGxlX2NvdW50XCJdLFxuICAgIH0sXG4gICAgY2F0ZWdvcnk6IFwiYW5hbHlzaXNcIixcbiAgICByZXF1aXJlc0F1dGg6IGZhbHNlLFxuICB9LFxuXG4gIGdldF9yZXF1ZXN0X3Jlc3BvbnNlX2xhdGVuY3k6IHtcbiAgICBpbnB1dDoge1xuICAgICAgdHlwZTogXCJvYmplY3RcIixcbiAgICAgIHByb3BlcnRpZXM6IHtcbiAgICAgICAgY2FwdHVyZV9pZDogY2FwdHVyZUlkU2NoZW1hLFxuICAgICAgICB0YXJnZXRfaG9zdDogdGFyZ2V0SG9zdFNjaGVtYSxcbiAgICAgICAgcHJvdG9jb2w6IHsgdHlwZTogXCJzdHJpbmdcIiwgZW51bTogW1wiaHR0cFwiLCBcImh0dHBzXCIsIFwiZG5zXCIsIFwiYXV0b1wiXSwgZGVmYXVsdDogXCJhdXRvXCIsIGRlc2NyaXB0aW9uOiBcIkFwcGxpY2F0aW9uIHByb3RvY29sIHRvIGFuYWx5emVcIiB9LFxuICAgICAgfSxcbiAgICAgIHJlcXVpcmVkOiBbXCJjYXB0dXJlX2lkXCJdLFxuICAgICAgYWRkaXRpb25hbFByb3BlcnRpZXM6IGZhbHNlLFxuICAgIH0sXG4gICAgb3V0cHV0OiB7XG4gICAgICB0eXBlOiBcIm9iamVjdFwiLFxuICAgICAgcHJvcGVydGllczoge1xuICAgICAgICBsYXRlbmNpZXM6IHtcbiAgICAgICAgICB0eXBlOiBcImFycmF5XCIsXG4gICAgICAgICAgaXRlbXM6IHtcbiAgICAgICAgICAgIHR5cGU6IFwib2JqZWN0XCIsXG4gICAgICAgICAgICBwcm9wZXJ0aWVzOiB7XG4gICAgICAgICAgICAgIHJlcXVlc3RfaWQ6IHsgdHlwZTogXCJzdHJpbmdcIiB9LFxuICAgICAgICAgICAgICByZXF1ZXN0X3RpbWU6IHRpbWVzdGFtcFNjaGVtYSxcbiAgICAgICAgICAgICAgcmVzcG9uc2VfdGltZTogdGltZXN0YW1wU2NoZW1hLFxuICAgICAgICAgICAgICBsYXRlbmN5X21zOiB7IHR5cGU6IFwibnVtYmVyXCIgfSxcbiAgICAgICAgICAgICAgcHJvdG9jb2w6IHsgdHlwZTogXCJzdHJpbmdcIiB9LFxuICAgICAgICAgICAgICBlbmRwb2ludDogeyB0eXBlOiBcInN0cmluZ1wiIH0sXG4gICAgICAgICAgICB9LFxuICAgICAgICAgICAgcmVxdWlyZWQ6IFtcInJlcXVlc3RfaWRcIiwgXCJsYXRlbmN5X21zXCJdLFxuICAgICAgICAgIH0sXG4gICAgICAgIH0sXG4gICAgICAgIHN0YXRpc3RpY3M6IHtcbiAgICAgICAgICB0eXBlOiBcIm9iamVjdFwiLFxuICAgICAgICAgIHByb3BlcnRpZXM6IHtcbiAgICAgICAgICAgIGF2Z19tczogeyB0eXBlOiBcIm51bWJlclwiIH0sXG4gICAgICAgICAgICBwNTBfbXM6IHsgdHlwZTogXCJudW1iZXJcIiB9LFxuICAgICAgICAgICAgcDk1X21zOiB7IHR5cGU6IFwibnVtYmVyXCIgfSxcbiAgICAgICAgICAgIHA5OV9tczogeyB0eXBlOiBcIm51bWJlclwiIH0sXG4gICAgICAgICAgICBtYXhfbXM6IHsgdHlwZTogXCJudW1iZXJcIiB9LFxuICAgICAgICAgIH0sXG4gICAgICAgICAgcmVxdWlyZWQ6IFtcImF2Z19tc1wiLCBcInA1MF9tc1wiLCBcInA5NV9tc1wiXSxcbiAgICAgICAgfSxcbiAgICAgICAgdG90YWxfcGFpcnM6IHsgdHlwZTogXCJpbnRlZ2VyXCIgfSxcbiAgICAgIH0sXG4gICAgICByZXF1aXJlZDogW1wibGF0ZW5jaWVzXCIsIFwic3RhdGlzdGljc1wiLCBcInRvdGFsX3BhaXJzXCJdLFxuICAgIH0sXG4gICAgY2F0ZWdvcnk6IFwiYW5hbHlzaXNcIixcbiAgICByZXF1aXJlc0F1dGg6IGZhbHNlLFxuICB9LFxuXG4gIGRpYWdub3NlX3RjcF9zdHJlYW06IHtcbiAgICBpbnB1dDoge1xuICAgICAgdHlwZTogXCJvYmplY3RcIixcbiAgICAgIHByb3BlcnRpZXM6IHtcbiAgICAgICAgY2FwdHVyZV9pZDogY2FwdHVyZUlkU2NoZW1hLFxuICAgICAgICBzdHJlYW1faWQ6IHsgdHlwZTogXCJzdHJpbmdcIiwgZGVzY3JpcHRpb246IFwiU3BlY2lmaWMgc3RyZWFtIElEIHRvIGRpYWdub3NlXCIgfSxcbiAgICAgICAgdGFyZ2V0X2hvc3Q6IHRhcmdldEhvc3RTY2hlbWEsXG4gICAgICAgIGFuYWx5c2lzX2ZvY3VzOiBhbmFseXNpc0ZvY3VzU2NoZW1hLFxuICAgICAgfSxcbiAgICAgIHJlcXVpcmVkOiBbXCJjYXB0dXJlX2lkXCJdLFxuICAgICAgYWRkaXRpb25hbFByb3BlcnRpZXM6IGZhbHNlLFxuICAgIH0sXG4gICAgb3V0cHV0OiB7XG4gICAgICB0eXBlOiBcIm9iamVjdFwiLFxuICAgICAgcHJvcGVydGllczoge1xuICAgICAgICBkaWFnbm9zaXM6IHtcbiAgICAgICAgICB0eXBlOiBcIm9iamVjdFwiLFxuICAgICAgICAgIHByb3BlcnRpZXM6IHtcbiAgICAgICAgICAgIHN0cmVhbV9pZDogeyB0eXBlOiBcInN0cmluZ1wiIH0sXG4gICAgICAgICAgICBpc3N1ZXNfZm91bmQ6IHtcbiAgICAgICAgICAgICAgdHlwZTogXCJhcnJheVwiLFxuICAgICAgICAgICAgICBpdGVtczoge1xuICAgICAgICAgICAgICAgIHR5cGU6IFwib2JqZWN0XCIsXG4gICAgICAgICAgICAgICAgcHJvcGVydGllczoge1xuICAgICAgICAgICAgICAgICAgdHlwZTogeyB0eXBlOiBcInN0cmluZ1wiIH0sXG4gICAgICAgICAgICAgICAgICBzZXZlcml0eTogeyB0eXBlOiBcInN0cmluZ1wiLCBlbnVtOiBbXCJjcml0aWNhbFwiLCBcIndhcm5pbmdcIiwgXCJpbmZvXCJdIH0sXG4gICAgICAgICAgICAgICAgICBkZXNjcmlwdGlvbjogeyB0eXBlOiBcInN0cmluZ1wiIH0sXG4gICAgICAgICAgICAgICAgICBldmlkZW5jZTogeyB0eXBlOiBcIm9iamVjdFwiIH0sXG4gICAgICAgICAgICAgICAgfSxcbiAgICAgICAgICAgICAgICByZXF1aXJlZDogW1widHlwZVwiLCBcInNldmVyaXR5XCIsIFwiZGVzY3JpcHRpb25cIl0sXG4gICAgICAgICAgICAgIH0sXG4gICAgICAgICAgICB9LFxuICAgICAgICAgICAgaGVhbHRoX3Njb3JlOiB7IHR5cGU6IFwibnVtYmVyXCIsIG1pbmltdW06IDAsIG1heGltdW06IDEwMCB9LFxuICAgICAgICAgICAgcmVjb21tZW5kYXRpb25zOiB7IHR5cGU6IFwiYXJyYXlcIiwgaXRlbXM6IHsgdHlwZTogXCJzdHJpbmdcIiB9IH0sXG4gICAgICAgICAgfSxcbiAgICAgICAgICByZXF1aXJlZDogW1wiaXNzdWVzX2ZvdW5kXCIsIFwiaGVhbHRoX3Njb3JlXCJdLFxuICAgICAgICB9LFxuICAgICAgICBhbmFseXplZF9wYWNrZXRzOiB7IHR5cGU6IFwiaW50ZWdlclwiIH0sXG4gICAgICAgIGFuYWx5c2lzX2R1cmF0aW9uX21zOiB7IHR5cGU6IFwibnVtYmVyXCIgfSxcbiAgICAgIH0sXG4gICAgICByZXF1aXJlZDogW1wiZGlhZ25vc2lzXCIsIFwiYW5hbHl6ZWRfcGFja2V0c1wiXSxcbiAgICB9LFxuICAgIGNhdGVnb3J5OiBcImFuYWx5c2lzXCIsXG4gICAgcmVxdWlyZXNBdXRoOiBmYWxzZSxcbiAgfSxcblxuICAvLyDilIDilIAgTmV0d29yayBEaWFnbm9zdGljcyBBY3Rpb25zIOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgFxuICAvLyBBZGRlZCBieSBuZXR3b3JrLWRpYWdub3N0aWNzLWludGVncmF0aW9uIChSZXEgMi4xLTIuNSkuIElucHV0IGNvbnN0cmFpbnRzIGFyZVxuICAvLyBkZXJpdmVkIG9uZS1mb3Itb25lIGZyb20gYWdlbnRzL25ldHdvcmstYWdlbnQvZGlhZ25vc3RpY3NfdmFsaWRhdGlvbi5weTsgb3V0cHV0XG4gIC8vIHNjaGVtYXMgYXJlIGRlcml2ZWQgZnJvbSB0aGUgcmVzcG9uc2UgZGljdCBsaXRlcmFscyBidWlsdCBpbiB0aGUgY29ycmVzcG9uZGluZ1xuICAvLyBoYW5kbGVycyBpbiBhZ2VudHMvbmV0d29yay1hZ2VudC9tYWluLnB5LlxuXG4gIHRjcF90cmFjZXJvdXRlOiB7XG4gICAgaW5wdXQ6IHtcbiAgICAgIHR5cGU6IFwib2JqZWN0XCIsXG4gICAgICBwcm9wZXJ0aWVzOiB7XG4gICAgICAgIGluc3RhbmNlX2lkOiB7XG4gICAgICAgICAgdHlwZTogXCJzdHJpbmdcIixcbiAgICAgICAgICBwYXR0ZXJuOiBcIl5pLVswLTlhLWZdezgsMTd9JFwiLFxuICAgICAgICAgIGRlc2NyaXB0aW9uOiBcIkVDMiBpbnN0YW5jZSBJRCB0byBydW4gdGhlIHRyYWNlcm91dGUgZnJvbVwiLFxuICAgICAgICB9LFxuICAgICAgICBkZXN0aW5hdGlvbl9ob3N0OiB7XG4gICAgICAgICAgdHlwZTogXCJzdHJpbmdcIixcbiAgICAgICAgICBtaW5MZW5ndGg6IDEsXG4gICAgICAgICAgbWF4TGVuZ3RoOiAyNTMsXG4gICAgICAgICAgZGVzY3JpcHRpb246IFwiSG9zdG5hbWUgb3IgSVAgYWRkcmVzcyB0byB0cmFjZSB0b1wiLFxuICAgICAgICB9LFxuICAgICAgICBkZXN0aW5hdGlvbl9wb3J0OiB7XG4gICAgICAgICAgdHlwZTogXCJpbnRlZ2VyXCIsXG4gICAgICAgICAgbWluaW11bTogMSxcbiAgICAgICAgICBtYXhpbXVtOiA2NTUzNSxcbiAgICAgICAgICBkZWZhdWx0OiA0NDMsXG4gICAgICAgICAgZGVzY3JpcHRpb246IFwiVENQIHBvcnQgdG8gcHJvYmUgKGRlZmF1bHQgNDQzKVwiLFxuICAgICAgICB9LFxuICAgICAgICBtYXhfaG9wczoge1xuICAgICAgICAgIHR5cGU6IFwiaW50ZWdlclwiLFxuICAgICAgICAgIG1pbmltdW06IDEsXG4gICAgICAgICAgbWF4aW11bTogMzAsXG4gICAgICAgICAgZGVmYXVsdDogMzAsXG4gICAgICAgICAgZGVzY3JpcHRpb246IFwiTWF4aW11bSBUVEwgaG9wcyB0byBwcm9iZSAoZGVmYXVsdCAzMClcIixcbiAgICAgICAgfSxcbiAgICAgICAgcHJvYmVfdGltZW91dDoge1xuICAgICAgICAgIHR5cGU6IFwiaW50ZWdlclwiLFxuICAgICAgICAgIG1pbmltdW06IDEsXG4gICAgICAgICAgbWF4aW11bTogNSxcbiAgICAgICAgICBkZWZhdWx0OiAyLFxuICAgICAgICAgIGRlc2NyaXB0aW9uOiBcIlNlY29uZHMgdG8gd2FpdCBwZXIgaG9wIChkZWZhdWx0IDIpXCIsXG4gICAgICAgIH0sXG4gICAgICB9LFxuICAgICAgcmVxdWlyZWQ6IFtcImluc3RhbmNlX2lkXCIsIFwiZGVzdGluYXRpb25faG9zdFwiXSxcbiAgICAgIGFkZGl0aW9uYWxQcm9wZXJ0aWVzOiBmYWxzZSxcbiAgICB9LFxuICAgIG91dHB1dDoge1xuICAgICAgdHlwZTogXCJvYmplY3RcIixcbiAgICAgIHByb3BlcnRpZXM6IHtcbiAgICAgICAgc291cmNlX2luc3RhbmNlX2lkOiB7IHR5cGU6IFwic3RyaW5nXCIgfSxcbiAgICAgICAgc291cmNlX2lwOiB7IHR5cGU6IFwic3RyaW5nXCIgfSxcbiAgICAgICAgZGVzdGluYXRpb25faG9zdDogeyB0eXBlOiBcInN0cmluZ1wiIH0sXG4gICAgICAgIGRlc3RpbmF0aW9uX3BvcnQ6IHsgdHlwZTogXCJpbnRlZ2VyXCIgfSxcbiAgICAgICAgZGVzdGluYXRpb25faXA6IHsgdHlwZTogXCJzdHJpbmdcIiB9LFxuICAgICAgICBkZXN0aW5hdGlvbl9yZWFjaGVkOiB7IHR5cGU6IFwiYm9vbGVhblwiIH0sXG4gICAgICAgIGRlc3RpbmF0aW9uX3N0YXR1czogeyB0eXBlOiBcInN0cmluZ1wiIH0sXG4gICAgICAgIHRvdGFsX2hvcHM6IHsgdHlwZTogXCJpbnRlZ2VyXCIgfSxcbiAgICAgICAgdHJhY2VfZHVyYXRpb25fbXM6IHsgdHlwZTogXCJudW1iZXJcIiB9LFxuICAgICAgICBtYXhfaG9wczogeyB0eXBlOiBcImludGVnZXJcIiB9LFxuICAgICAgICBwcm9iZV90aW1lb3V0OiB7IHR5cGU6IFwiaW50ZWdlclwiIH0sXG4gICAgICAgIGhvcHM6IHtcbiAgICAgICAgICB0eXBlOiBcImFycmF5XCIsXG4gICAgICAgICAgaXRlbXM6IHtcbiAgICAgICAgICAgIHR5cGU6IFwib2JqZWN0XCIsXG4gICAgICAgICAgICBwcm9wZXJ0aWVzOiB7XG4gICAgICAgICAgICAgIGhvcDogeyB0eXBlOiBcImludGVnZXJcIiB9LFxuICAgICAgICAgICAgICBpcDogeyB0eXBlOiBcInN0cmluZ1wiIH0sXG4gICAgICAgICAgICAgIHJ0dF9tczogeyB0eXBlOiBcIm51bWJlclwiIH0sXG4gICAgICAgICAgICB9LFxuICAgICAgICAgICAgcmVxdWlyZWQ6IFtcImhvcFwiXSxcbiAgICAgICAgICB9LFxuICAgICAgICB9LFxuICAgICAgfSxcbiAgICAgIHJlcXVpcmVkOiBbXCJzb3VyY2VfaW5zdGFuY2VfaWRcIiwgXCJkZXN0aW5hdGlvbl9ob3N0XCIsIFwiZGVzdGluYXRpb25fcmVhY2hlZFwiLCBcImhvcHNcIl0sXG4gICAgfSxcbiAgICBjYXRlZ29yeTogXCJhbmFseXNpc1wiLFxuICAgIHJlcXVpcmVzQXV0aDogZmFsc2UsXG4gIH0sXG5cbiAgdGxzX3RyYWNlcm91dGU6IHtcbiAgICBpbnB1dDoge1xuICAgICAgdHlwZTogXCJvYmplY3RcIixcbiAgICAgIHByb3BlcnRpZXM6IHtcbiAgICAgICAgaW5zdGFuY2VfaWQ6IHtcbiAgICAgICAgICB0eXBlOiBcInN0cmluZ1wiLFxuICAgICAgICAgIHBhdHRlcm46IFwiXmktWzAtOWEtZl17OCwxN30kXCIsXG4gICAgICAgICAgZGVzY3JpcHRpb246IFwiRUMyIGluc3RhbmNlIElEIHRvIHJ1biB0aGUgVExTIHRyYWNlcm91dGUgZnJvbVwiLFxuICAgICAgICB9LFxuICAgICAgICBkZXN0aW5hdGlvbl9ob3N0OiB7XG4gICAgICAgICAgdHlwZTogXCJzdHJpbmdcIixcbiAgICAgICAgICBtaW5MZW5ndGg6IDEsXG4gICAgICAgICAgbWF4TGVuZ3RoOiAyNTMsXG4gICAgICAgICAgZGVzY3JpcHRpb246IFwiSG9zdG5hbWUgb3IgSVAgYWRkcmVzcyB0byB0cmFjZSB0b1wiLFxuICAgICAgICB9LFxuICAgICAgICBkZXN0aW5hdGlvbl9wb3J0OiB7XG4gICAgICAgICAgdHlwZTogXCJpbnRlZ2VyXCIsXG4gICAgICAgICAgbWluaW11bTogMSxcbiAgICAgICAgICBtYXhpbXVtOiA2NTUzNSxcbiAgICAgICAgICBkZWZhdWx0OiA0NDMsXG4gICAgICAgICAgZGVzY3JpcHRpb246IFwiVENQIHBvcnQgdG8gcHJvYmUgKGRlZmF1bHQgNDQzKVwiLFxuICAgICAgICB9LFxuICAgICAgICBtYXhfaG9wczoge1xuICAgICAgICAgIHR5cGU6IFwiaW50ZWdlclwiLFxuICAgICAgICAgIG1pbmltdW06IDEsXG4gICAgICAgICAgbWF4aW11bTogMzAsXG4gICAgICAgICAgZGVmYXVsdDogMzAsXG4gICAgICAgICAgZGVzY3JpcHRpb246IFwiTWF4aW11bSBUVEwgaG9wcyB0byBwcm9iZSAoZGVmYXVsdCAzMClcIixcbiAgICAgICAgfSxcbiAgICAgICAgcHJvYmVfdGltZW91dDoge1xuICAgICAgICAgIHR5cGU6IFwiaW50ZWdlclwiLFxuICAgICAgICAgIG1pbmltdW06IDEsXG4gICAgICAgICAgbWF4aW11bTogNSxcbiAgICAgICAgICBkZWZhdWx0OiAyLFxuICAgICAgICAgIGRlc2NyaXB0aW9uOiBcIlNlY29uZHMgdG8gd2FpdCBwZXIgaG9wIChkZWZhdWx0IDIpXCIsXG4gICAgICAgIH0sXG4gICAgICAgIHNuaV9vdmVycmlkZToge1xuICAgICAgICAgIHR5cGU6IFwic3RyaW5nXCIsXG4gICAgICAgICAgbWluTGVuZ3RoOiAxLFxuICAgICAgICAgIG1heExlbmd0aDogMjUzLFxuICAgICAgICAgIGRlc2NyaXB0aW9uOiBcIk9wdGlvbmFsIFNOSSBob3N0bmFtZSB0byBzZW5kIGR1cmluZyB0aGUgVExTIGhhbmRzaGFrZSBpbnN0ZWFkIG9mIGRlc3RpbmF0aW9uX2hvc3RcIixcbiAgICAgICAgfSxcbiAgICAgIH0sXG4gICAgICByZXF1aXJlZDogW1wiaW5zdGFuY2VfaWRcIiwgXCJkZXN0aW5hdGlvbl9ob3N0XCJdLFxuICAgICAgYWRkaXRpb25hbFByb3BlcnRpZXM6IGZhbHNlLFxuICAgIH0sXG4gICAgb3V0cHV0OiB7XG4gICAgICB0eXBlOiBcIm9iamVjdFwiLFxuICAgICAgcHJvcGVydGllczoge1xuICAgICAgICBzb3VyY2VfaW5zdGFuY2VfaWQ6IHsgdHlwZTogXCJzdHJpbmdcIiB9LFxuICAgICAgICBzb3VyY2VfaXA6IHsgdHlwZTogXCJzdHJpbmdcIiB9LFxuICAgICAgICBkZXN0aW5hdGlvbl9ob3N0OiB7IHR5cGU6IFwic3RyaW5nXCIgfSxcbiAgICAgICAgZGVzdGluYXRpb25fcG9ydDogeyB0eXBlOiBcImludGVnZXJcIiB9LFxuICAgICAgICBkZXN0aW5hdGlvbl9yZWFjaGVkOiB7IHR5cGU6IFwiYm9vbGVhblwiIH0sXG4gICAgICAgIGRlc3RpbmF0aW9uX3N0YXR1czogeyB0eXBlOiBcInN0cmluZ1wiIH0sXG4gICAgICAgIHRvdGFsX2hvcHM6IHsgdHlwZTogXCJpbnRlZ2VyXCIgfSxcbiAgICAgICAgdHJhY2VfZHVyYXRpb25fbXM6IHsgdHlwZTogXCJudW1iZXJcIiB9LFxuICAgICAgICBob3BzOiB7XG4gICAgICAgICAgdHlwZTogXCJhcnJheVwiLFxuICAgICAgICAgIGl0ZW1zOiB7XG4gICAgICAgICAgICB0eXBlOiBcIm9iamVjdFwiLFxuICAgICAgICAgICAgcHJvcGVydGllczoge1xuICAgICAgICAgICAgICBob3A6IHsgdHlwZTogXCJpbnRlZ2VyXCIgfSxcbiAgICAgICAgICAgICAgaXA6IHsgdHlwZTogXCJzdHJpbmdcIiB9LFxuICAgICAgICAgICAgICBydHRfbXM6IHsgdHlwZTogXCJudW1iZXJcIiB9LFxuICAgICAgICAgICAgfSxcbiAgICAgICAgICAgIHJlcXVpcmVkOiBbXCJob3BcIl0sXG4gICAgICAgICAgfSxcbiAgICAgICAgfSxcbiAgICAgICAgdGxzOiB7XG4gICAgICAgICAgdHlwZTogW1wib2JqZWN0XCIsIFwibnVsbFwiXSxcbiAgICAgICAgICBwcm9wZXJ0aWVzOiB7XG4gICAgICAgICAgICBoYW5kc2hha2Vfc3VjY2VzczogeyB0eXBlOiBcImJvb2xlYW5cIiB9LFxuICAgICAgICAgICAgcHJvdG9jb2xfdmVyc2lvbjogeyB0eXBlOiBbXCJzdHJpbmdcIiwgXCJudWxsXCJdIH0sXG4gICAgICAgICAgICBjaXBoZXJfc3VpdGU6IHsgdHlwZTogW1wic3RyaW5nXCIsIFwibnVsbFwiXSB9LFxuICAgICAgICAgICAgY2VydGlmaWNhdGVfc3ViamVjdDogeyB0eXBlOiBbXCJzdHJpbmdcIiwgXCJudWxsXCJdIH0sXG4gICAgICAgICAgICBjZXJ0aWZpY2F0ZV9pc3N1ZXI6IHsgdHlwZTogW1wic3RyaW5nXCIsIFwibnVsbFwiXSB9LFxuICAgICAgICAgICAgY2VydGlmaWNhdGVfbm90X2FmdGVyOiB7IHR5cGU6IFtcInN0cmluZ1wiLCBcIm51bGxcIl0gfSxcbiAgICAgICAgICAgIGhhbmRzaGFrZV90aW1lX21zOiB7IHR5cGU6IFtcIm51bWJlclwiLCBcIm51bGxcIl0gfSxcbiAgICAgICAgICAgIGVycm9yX3R5cGU6IHsgdHlwZTogW1wic3RyaW5nXCIsIFwibnVsbFwiXSB9LFxuICAgICAgICAgICAgZXJyb3JfZGV0YWlsOiB7IHR5cGU6IFtcInN0cmluZ1wiLCBcIm51bGxcIl0gfSxcbiAgICAgICAgICB9LFxuICAgICAgICB9LFxuICAgICAgICB0bHNfc2tpcHBlZF9yZWFzb246IHsgdHlwZTogW1wic3RyaW5nXCIsIFwibnVsbFwiXSwgZGVzY3JpcHRpb246IFwiU2V0IHdoZW4gdGhlIFRMUyBwaGFzZSB3YXMgc2tpcHBlZCAoZS5nLiBkbnNfcmVzb2x1dGlvbl9mYWlsZWQsIGRlc3RpbmF0aW9uX3VucmVhY2hhYmxlKVwiIH0sXG4gICAgICB9LFxuICAgICAgcmVxdWlyZWQ6IFtcInNvdXJjZV9pbnN0YW5jZV9pZFwiLCBcImRlc3RpbmF0aW9uX2hvc3RcIiwgXCJkZXN0aW5hdGlvbl9yZWFjaGVkXCIsIFwiaG9wc1wiXSxcbiAgICB9LFxuICAgIGNhdGVnb3J5OiBcImFuYWx5c2lzXCIsXG4gICAgcmVxdWlyZXNBdXRoOiBmYWxzZSxcbiAgfSxcblxuICBkbnNfcmVzb2x2ZToge1xuICAgIGlucHV0OiB7XG4gICAgICB0eXBlOiBcIm9iamVjdFwiLFxuICAgICAgcHJvcGVydGllczoge1xuICAgICAgICBpbnN0YW5jZV9pZDoge1xuICAgICAgICAgIHR5cGU6IFwic3RyaW5nXCIsXG4gICAgICAgICAgcGF0dGVybjogXCJeaS1bMC05YS1mXXs4LDE3fSRcIixcbiAgICAgICAgICBkZXNjcmlwdGlvbjogXCJFQzIgaW5zdGFuY2UgSUQgdG8gcmVzb2x2ZSB0aGUgaG9zdG5hbWUgZnJvbVwiLFxuICAgICAgICB9LFxuICAgICAgICBob3N0bmFtZToge1xuICAgICAgICAgIHR5cGU6IFwic3RyaW5nXCIsXG4gICAgICAgICAgbWluTGVuZ3RoOiAxLFxuICAgICAgICAgIG1heExlbmd0aDogMjUzLFxuICAgICAgICAgIGRlc2NyaXB0aW9uOiBcIkhvc3RuYW1lIHRvIHJlc29sdmVcIixcbiAgICAgICAgfSxcbiAgICAgICAgcmVjb3JkX3R5cGU6IHtcbiAgICAgICAgICB0eXBlOiBcInN0cmluZ1wiLFxuICAgICAgICAgIGVudW06IFtcIkFcIiwgXCJBQUFBXCIsIFwiQ05BTUVcIiwgXCJNWFwiLCBcIlRYVFwiLCBcIlNSVlwiLCBcIlBUUlwiXSxcbiAgICAgICAgICBkZWZhdWx0OiBcIkFcIixcbiAgICAgICAgICBkZXNjcmlwdGlvbjogXCJETlMgcmVjb3JkIHR5cGUgdG8gcXVlcnkgKGRlZmF1bHQgQSlcIixcbiAgICAgICAgfSxcbiAgICAgIH0sXG4gICAgICByZXF1aXJlZDogW1wiaW5zdGFuY2VfaWRcIiwgXCJob3N0bmFtZVwiXSxcbiAgICAgIGFkZGl0aW9uYWxQcm9wZXJ0aWVzOiBmYWxzZSxcbiAgICB9LFxuICAgIG91dHB1dDoge1xuICAgICAgdHlwZTogXCJvYmplY3RcIixcbiAgICAgIHByb3BlcnRpZXM6IHtcbiAgICAgICAgaG9zdG5hbWU6IHsgdHlwZTogXCJzdHJpbmdcIiB9LFxuICAgICAgICByZWNvcmRfdHlwZTogeyB0eXBlOiBcInN0cmluZ1wiIH0sXG4gICAgICAgIGluc3RhbmNlX2lkOiB7IHR5cGU6IFwic3RyaW5nXCIgfSxcbiAgICAgICAgaW5zdGFuY2VfcmVzb2x1dGlvbjoge1xuICAgICAgICAgIHR5cGU6IFwib2JqZWN0XCIsXG4gICAgICAgICAgcHJvcGVydGllczoge1xuICAgICAgICAgICAgcmVzb2x2ZXJfYWRkcmVzczogeyB0eXBlOiBcInN0cmluZ1wiIH0sXG4gICAgICAgICAgICByZWNvcmRzOiB7IHR5cGU6IFwiYXJyYXlcIiwgaXRlbXM6IHsgdHlwZTogXCJzdHJpbmdcIiB9IH0sXG4gICAgICAgICAgICByZXNvbHV0aW9uX3RpbWVfbXM6IHsgdHlwZTogXCJudW1iZXJcIiB9LFxuICAgICAgICAgICAgZXJyb3I6IHsgdHlwZTogXCJzdHJpbmdcIiB9LFxuICAgICAgICAgIH0sXG4gICAgICAgICAgcmVxdWlyZWQ6IFtcInJlc29sdmVyX2FkZHJlc3NcIiwgXCJyZWNvcmRzXCIsIFwicmVzb2x1dGlvbl90aW1lX21zXCJdLFxuICAgICAgICB9LFxuICAgICAgICBhZ2VudF9yZXNvbHV0aW9uOiB7XG4gICAgICAgICAgdHlwZTogXCJvYmplY3RcIixcbiAgICAgICAgICBwcm9wZXJ0aWVzOiB7XG4gICAgICAgICAgICByZWNvcmRzOiB7IHR5cGU6IFwiYXJyYXlcIiwgaXRlbXM6IHsgdHlwZTogXCJzdHJpbmdcIiB9IH0sXG4gICAgICAgICAgICByZXNvbHV0aW9uX3RpbWVfbXM6IHsgdHlwZTogXCJudW1iZXJcIiB9LFxuICAgICAgICAgICAgZXJyb3I6IHsgdHlwZTogXCJzdHJpbmdcIiB9LFxuICAgICAgICAgIH0sXG4gICAgICAgICAgcmVxdWlyZWQ6IFtcInJlY29yZHNcIiwgXCJyZXNvbHV0aW9uX3RpbWVfbXNcIl0sXG4gICAgICAgIH0sXG4gICAgICAgIHNwbGl0X2hvcml6b25fZGV0ZWN0ZWQ6IHsgdHlwZTogXCJib29sZWFuXCIgfSxcbiAgICAgIH0sXG4gICAgICByZXF1aXJlZDogW1wiaG9zdG5hbWVcIiwgXCJyZWNvcmRfdHlwZVwiLCBcImluc3RhbmNlX2lkXCIsIFwiaW5zdGFuY2VfcmVzb2x1dGlvblwiLCBcImFnZW50X3Jlc29sdXRpb25cIiwgXCJzcGxpdF9ob3Jpem9uX2RldGVjdGVkXCJdLFxuICAgIH0sXG4gICAgY2F0ZWdvcnk6IFwiYW5hbHlzaXNcIixcbiAgICByZXF1aXJlc0F1dGg6IGZhbHNlLFxuICB9LFxuXG4gIGRiX2Nvbm5lY3Rpdml0eV9wcm9iZToge1xuICAgIGlucHV0OiB7XG4gICAgICB0eXBlOiBcIm9iamVjdFwiLFxuICAgICAgcHJvcGVydGllczoge1xuICAgICAgICBpbnN0YW5jZV9pZDoge1xuICAgICAgICAgIHR5cGU6IFwic3RyaW5nXCIsXG4gICAgICAgICAgcGF0dGVybjogXCJeaS1bMC05YS1mXXs4LDE3fSRcIixcbiAgICAgICAgICBkZXNjcmlwdGlvbjogXCJFQzIgaW5zdGFuY2UgSUQgdG8gcnVuIHRoZSBwcm9iZSBmcm9tXCIsXG4gICAgICAgIH0sXG4gICAgICAgIGVuZHBvaW50OiB7XG4gICAgICAgICAgdHlwZTogXCJzdHJpbmdcIixcbiAgICAgICAgICBtaW5MZW5ndGg6IDEsXG4gICAgICAgICAgbWF4TGVuZ3RoOiAyNTMsXG4gICAgICAgICAgZGVzY3JpcHRpb246IFwiRGF0YWJhc2UgaG9zdG5hbWUgb3IgSVAgYWRkcmVzc1wiLFxuICAgICAgICB9LFxuICAgICAgICBwb3J0OiB7XG4gICAgICAgICAgdHlwZTogXCJpbnRlZ2VyXCIsXG4gICAgICAgICAgbWluaW11bTogMSxcbiAgICAgICAgICBtYXhpbXVtOiA2NTUzNSxcbiAgICAgICAgICBkZXNjcmlwdGlvbjogXCJEYXRhYmFzZSBUQ1AgcG9ydFwiLFxuICAgICAgICB9LFxuICAgICAgICBlbmdpbmU6IHtcbiAgICAgICAgICB0eXBlOiBcInN0cmluZ1wiLFxuICAgICAgICAgIGVudW06IFtcIm15c3FsXCIsIFwicG9zdGdyZXNxbFwiXSxcbiAgICAgICAgICBkZXNjcmlwdGlvbjogXCJEYXRhYmFzZSBlbmdpbmUgZm9yIHRoZSBwcm90b2NvbC1sZXZlbCBhdXRoIGhhbmRzaGFrZSBwaGFzZSAob3B0aW9uYWwg4oCUIG9taXQgdG8gcnVuIG9ubHkgdGhlIFRDUCArIFRMUyBwaGFzZXMpXCIsXG4gICAgICAgIH0sXG4gICAgICB9LFxuICAgICAgcmVxdWlyZWQ6IFtcImluc3RhbmNlX2lkXCIsIFwiZW5kcG9pbnRcIiwgXCJwb3J0XCJdLFxuICAgICAgYWRkaXRpb25hbFByb3BlcnRpZXM6IGZhbHNlLFxuICAgIH0sXG4gICAgb3V0cHV0OiB7XG4gICAgICB0eXBlOiBcIm9iamVjdFwiLFxuICAgICAgcHJvcGVydGllczoge1xuICAgICAgICBzb3VyY2VfaW5zdGFuY2VfaWQ6IHsgdHlwZTogXCJzdHJpbmdcIiB9LFxuICAgICAgICBzb3VyY2VfaXA6IHsgdHlwZTogXCJzdHJpbmdcIiB9LFxuICAgICAgICBlbmRwb2ludDogeyB0eXBlOiBcInN0cmluZ1wiIH0sXG4gICAgICAgIHBvcnQ6IHsgdHlwZTogXCJpbnRlZ2VyXCIgfSxcbiAgICAgICAgZW5naW5lOiB7IHR5cGU6IFtcInN0cmluZ1wiLCBcIm51bGxcIl0gfSxcbiAgICAgICAgdGNwOiB7XG4gICAgICAgICAgdHlwZTogW1wib2JqZWN0XCIsIFwibnVsbFwiXSxcbiAgICAgICAgICBwcm9wZXJ0aWVzOiB7XG4gICAgICAgICAgICBjb25uZWN0ZWQ6IHsgdHlwZTogXCJib29sZWFuXCIgfSxcbiAgICAgICAgICAgIGNvbm5lY3RfdGltZV9tczogeyB0eXBlOiBbXCJudW1iZXJcIiwgXCJudWxsXCJdIH0sXG4gICAgICAgICAgICBlcnJvcjogeyB0eXBlOiBbXCJzdHJpbmdcIiwgXCJudWxsXCJdIH0sXG4gICAgICAgICAgfSxcbiAgICAgICAgfSxcbiAgICAgICAgdGxzOiB7XG4gICAgICAgICAgdHlwZTogW1wib2JqZWN0XCIsIFwibnVsbFwiXSxcbiAgICAgICAgICBwcm9wZXJ0aWVzOiB7XG4gICAgICAgICAgICBjb25uZWN0ZWQ6IHsgdHlwZTogXCJib29sZWFuXCIgfSxcbiAgICAgICAgICAgIHRsc192ZXJzaW9uOiB7IHR5cGU6IFtcInN0cmluZ1wiLCBcIm51bGxcIl0gfSxcbiAgICAgICAgICAgIGVycm9yOiB7IHR5cGU6IFtcInN0cmluZ1wiLCBcIm51bGxcIl0gfSxcbiAgICAgICAgICB9LFxuICAgICAgICB9LFxuICAgICAgICBhdXRoOiB7XG4gICAgICAgICAgdHlwZTogW1wib2JqZWN0XCIsIFwibnVsbFwiXSxcbiAgICAgICAgICBwcm9wZXJ0aWVzOiB7XG4gICAgICAgICAgICBzdWNjZXNzOiB7IHR5cGU6IFwiYm9vbGVhblwiIH0sXG4gICAgICAgICAgICBkZXRhaWxzOiB7IHR5cGU6IFwib2JqZWN0XCIgfSxcbiAgICAgICAgICAgIGVycm9yOiB7IHR5cGU6IFtcInN0cmluZ1wiLCBcIm51bGxcIl0gfSxcbiAgICAgICAgICB9LFxuICAgICAgICB9LFxuICAgICAgICB2ZXJkaWN0OiB7XG4gICAgICAgICAgdHlwZTogXCJzdHJpbmdcIixcbiAgICAgICAgICBlbnVtOiBbXCJ0Y3BfZmFpbGVkXCIsIFwidGxzX2ZhaWxlZFwiLCBcImF1dGhfZmFpbGVkXCIsIFwiYWxsX3BoYXNlc19wYXNzZWRcIl0sXG4gICAgICAgIH0sXG4gICAgICB9LFxuICAgICAgcmVxdWlyZWQ6IFtcInNvdXJjZV9pbnN0YW5jZV9pZFwiLCBcImVuZHBvaW50XCIsIFwicG9ydFwiLCBcInZlcmRpY3RcIl0sXG4gICAgfSxcbiAgICBjYXRlZ29yeTogXCJhbmFseXNpc1wiLFxuICAgIHJlcXVpcmVzQXV0aDogZmFsc2UsXG4gIH0sXG5cbiAgYWdlbnRpY19yZWFjaGFiaWxpdHlfYW5hbHl6ZToge1xuICAgIGlucHV0OiB7XG4gICAgICB0eXBlOiBcIm9iamVjdFwiLFxuICAgICAgcHJvcGVydGllczoge1xuICAgICAgICBzb3VyY2U6IHtcbiAgICAgICAgICB0eXBlOiBcInN0cmluZ1wiLFxuICAgICAgICAgIHBhdHRlcm46IFwiXihpLVswLTlhLWZdezgsMTd9fGVuaS1bMC05YS1mXXs4LDE3fXxpZ3ctWzAtOWEtZl17OCwxN318dGd3LWF0dGFjaC1bMC05YS1mXXsxN318dGd3LVswLTlhLWZdezE3fXx2cGNlLXN2Yy1bMC05YS1mXXsxN318dnBjZS1bMC05YS1mXXs4LDE3fXxwY3gtWzAtOWEtZl17OCwxN318dmd3LVswLTlhLWZdezgsMTd9KSRcIixcbiAgICAgICAgICBkZXNjcmlwdGlvbjogXCJTb3VyY2UgVlBDIHJlc291cmNlIElEIG9ubHkg4oCUIGluc3RhbmNlLCBFTkksIGdhdGV3YXksIG9yIGF0dGFjaG1lbnQuIElQdjQgYWRkcmVzc2VzIGFyZSByZWplY3RlZCBhcyBzb3VyY2VzLlwiLFxuICAgICAgICB9LFxuICAgICAgICBkZXN0aW5hdGlvbjoge1xuICAgICAgICAgIHR5cGU6IFwic3RyaW5nXCIsXG4gICAgICAgICAgcGF0dGVybjogXCJeKGktWzAtOWEtZl17OCwxN318ZW5pLVswLTlhLWZdezgsMTd9fGlndy1bMC05YS1mXXs4LDE3fXx0Z3ctYXR0YWNoLVswLTlhLWZdezE3fXx0Z3ctWzAtOWEtZl17MTd9fHZwY2Utc3ZjLVswLTlhLWZdezE3fXx2cGNlLVswLTlhLWZdezgsMTd9fHBjeC1bMC05YS1mXXs4LDE3fXx2Z3ctWzAtOWEtZl17OCwxN318KFswLTldezEsM31cXFxcLil7M31bMC05XXsxLDN9KSRcIixcbiAgICAgICAgICBkZXNjcmlwdGlvbjogXCJEZXN0aW5hdGlvbiBWUEMgcmVzb3VyY2UgSUQgT1IgYW4gSVB2NCBhZGRyZXNzXCIsXG4gICAgICAgIH0sXG4gICAgICAgIGRlc3RpbmF0aW9uX3BvcnQ6IHtcbiAgICAgICAgICB0eXBlOiBcImludGVnZXJcIixcbiAgICAgICAgICBtaW5pbXVtOiAxLFxuICAgICAgICAgIG1heGltdW06IDY1NTM1LFxuICAgICAgICAgIGRlZmF1bHQ6IDQ0MyxcbiAgICAgICAgICBkZXNjcmlwdGlvbjogXCJEZXN0aW5hdGlvbiBwb3J0IHRvIGFuYWx5emUgKGRlZmF1bHQgNDQzKVwiLFxuICAgICAgICB9LFxuICAgICAgICBwcm90b2NvbDoge1xuICAgICAgICAgIHR5cGU6IFwic3RyaW5nXCIsXG4gICAgICAgICAgZW51bTogW1widGNwXCIsIFwidWRwXCJdLFxuICAgICAgICAgIGRlZmF1bHQ6IFwidGNwXCIsXG4gICAgICAgICAgZGVzY3JpcHRpb246IFwiUHJvdG9jb2wgdG8gYW5hbHl6ZVwiLFxuICAgICAgICB9LFxuICAgICAgfSxcbiAgICAgIHJlcXVpcmVkOiBbXCJzb3VyY2VcIiwgXCJkZXN0aW5hdGlvblwiXSxcbiAgICAgIGFkZGl0aW9uYWxQcm9wZXJ0aWVzOiBmYWxzZSxcbiAgICB9LFxuICAgIG91dHB1dDoge1xuICAgICAgdHlwZTogXCJvYmplY3RcIixcbiAgICAgIHByb3BlcnRpZXM6IHtcbiAgICAgICAgcmVhY2hhYmxlOiB7IHR5cGU6IFwiYm9vbGVhblwiIH0sXG4gICAgICAgIHNvdXJjZTogeyB0eXBlOiBcInN0cmluZ1wiIH0sXG4gICAgICAgIGRlc3RpbmF0aW9uOiB7IHR5cGU6IFwic3RyaW5nXCIgfSxcbiAgICAgICAgZGVzdGluYXRpb25fcG9ydDogeyB0eXBlOiBcImludGVnZXJcIiB9LFxuICAgICAgICBwcm90b2NvbDogeyB0eXBlOiBcInN0cmluZ1wiIH0sXG4gICAgICAgIHBhdGhfY29tcG9uZW50czogeyB0eXBlOiBcImFycmF5XCIsIGl0ZW1zOiB7IHR5cGU6IFwib2JqZWN0XCIgfSB9LFxuICAgICAgICBsaW1pdGF0aW9uczogeyB0eXBlOiBcImFycmF5XCIsIGl0ZW1zOiB7IHR5cGU6IFwib2JqZWN0XCIgfSB9LFxuICAgICAgICBibG9ja2luZ19jb21wb25lbnQ6IHsgdHlwZTogXCJvYmplY3RcIiB9LFxuICAgICAgICBleHBsYW5hdGlvbjogeyB0eXBlOiBcInN0cmluZ1wiIH0sXG4gICAgICAgIHJlbWVkaWF0aW9uOiB7IHR5cGU6IFwic3RyaW5nXCIgfSxcbiAgICAgIH0sXG4gICAgICByZXF1aXJlZDogW1wicmVhY2hhYmxlXCIsIFwic291cmNlXCIsIFwiZGVzdGluYXRpb25cIl0sXG4gICAgfSxcbiAgICBjYXRlZ29yeTogXCJhbmFseXNpc1wiLFxuICAgIHJlcXVpcmVzQXV0aDogZmFsc2UsXG4gIH0sXG5cbiAgc3NtX2hlYWx0aF9jaGVjazoge1xuICAgIGlucHV0OiB7XG4gICAgICB0eXBlOiBcIm9iamVjdFwiLFxuICAgICAgcHJvcGVydGllczoge1xuICAgICAgICBpbnN0YW5jZV9pZDoge1xuICAgICAgICAgIHR5cGU6IFwic3RyaW5nXCIsXG4gICAgICAgICAgcGF0dGVybjogXCJeaS1bMC05YS1mXXs4LDE3fSRcIixcbiAgICAgICAgICBkZXNjcmlwdGlvbjogXCJFQzIgaW5zdGFuY2UgSUQgdG8gY2hlY2sgU1NNIGFnZW50IGhlYWx0aCBmb3JcIixcbiAgICAgICAgfSxcbiAgICAgIH0sXG4gICAgICByZXF1aXJlZDogW1wiaW5zdGFuY2VfaWRcIl0sXG4gICAgICBhZGRpdGlvbmFsUHJvcGVydGllczogZmFsc2UsXG4gICAgfSxcbiAgICBvdXRwdXQ6IHtcbiAgICAgIHR5cGU6IFwib2JqZWN0XCIsXG4gICAgICBwcm9wZXJ0aWVzOiB7XG4gICAgICAgIGluc3RhbmNlX2lkOiB7IHR5cGU6IFwic3RyaW5nXCIgfSxcbiAgICAgICAgc3NtX21hbmFnZWQ6IHsgdHlwZTogXCJib29sZWFuXCIgfSxcbiAgICAgICAgYWdlbnRfdmVyc2lvbjogeyB0eXBlOiBcInN0cmluZ1wiIH0sXG4gICAgICAgIHBpbmdfc3RhdHVzOiB7IHR5cGU6IFwic3RyaW5nXCIgfSxcbiAgICAgICAgbGFzdF9waW5nX3RpbWU6IHsgdHlwZTogW1wic3RyaW5nXCIsIFwibnVsbFwiXSB9LFxuICAgICAgICBwbGF0Zm9ybV90eXBlOiB7IHR5cGU6IFwic3RyaW5nXCIgfSxcbiAgICAgICAgcGxhdGZvcm1fbmFtZTogeyB0eXBlOiBcInN0cmluZ1wiIH0sXG4gICAgICAgIHBsYXRmb3JtX3ZlcnNpb246IHsgdHlwZTogXCJzdHJpbmdcIiB9LFxuICAgICAgICBpcF9hZGRyZXNzOiB7IHR5cGU6IFwic3RyaW5nXCIgfSxcbiAgICAgICAgY29tcHV0ZXJfbmFtZTogeyB0eXBlOiBcInN0cmluZ1wiIH0sXG4gICAgICAgIGFzc29jaWF0aW9uX3N0YXR1czogeyB0eXBlOiBcInN0cmluZ1wiIH0sXG4gICAgICAgIGRpYWdub3N0aWNfaGludHM6IHtcbiAgICAgICAgICB0eXBlOiBcImFycmF5XCIsXG4gICAgICAgICAgaXRlbXM6IHsgdHlwZTogXCJzdHJpbmdcIiB9LFxuICAgICAgICAgIGRlc2NyaXB0aW9uOiBcIlByZXNlbnQgb25seSB3aGVuIHNzbV9tYW5hZ2VkIGlzIGZhbHNlXCIsXG4gICAgICAgIH0sXG4gICAgICB9LFxuICAgICAgcmVxdWlyZWQ6IFtcImluc3RhbmNlX2lkXCIsIFwic3NtX21hbmFnZWRcIl0sXG4gICAgfSxcbiAgICBjYXRlZ29yeTogXCJhbmFseXNpc1wiLFxuICAgIHJlcXVpcmVzQXV0aDogZmFsc2UsXG4gIH0sXG5cbiAgLy8g4pSA4pSAIENvbXBvc2l0ZSBBY3Rpb24g4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSAXG5cbiAgZnVsbF9kaWFnbm9zdGljOiB7XG4gICAgaW5wdXQ6IHtcbiAgICAgIHR5cGU6IFwib2JqZWN0XCIsXG4gICAgICBwcm9wZXJ0aWVzOiB7XG4gICAgICAgIGVuaV9pZHM6IGVuaUlkc1NjaGVtYSxcbiAgICAgICAgZHVyYXRpb25fbWludXRlczogZHVyYXRpb25NaW51dGVzU2NoZW1hLFxuICAgICAgICB0YXJnZXRfaG9zdDogdGFyZ2V0SG9zdFNjaGVtYSxcbiAgICAgICAgYW5hbHlzaXNfZm9jdXM6IGFuYWx5c2lzRm9jdXNTY2hlbWEsXG4gICAgICB9LFxuICAgICAgcmVxdWlyZWQ6IFtcImVuaV9pZHNcIl0sXG4gICAgICBhZGRpdGlvbmFsUHJvcGVydGllczogZmFsc2UsXG4gICAgfSxcbiAgICBvdXRwdXQ6IHtcbiAgICAgIHR5cGU6IFwib2JqZWN0XCIsXG4gICAgICBwcm9wZXJ0aWVzOiB7XG4gICAgICAgIGNhcHR1cmVfaWQ6IHsgdHlwZTogXCJzdHJpbmdcIiwgZGVzY3JpcHRpb246IFwiQ2FwdHVyZSBJRCBmb3IgZm9sbG93LXVwIHF1ZXJpZXNcIiB9LFxuICAgICAgICBzdGF0dXM6IHsgdHlwZTogXCJzdHJpbmdcIiwgZW51bTogW1wiY29tcGxldGVkXCIsIFwicGFydGlhbFwiLCBcImZhaWxlZFwiXSB9LFxuICAgICAgICBzdW1tYXJ5OiB7IHR5cGU6IFwic3RyaW5nXCIsIG1heExlbmd0aDogNTAwLCBkZXNjcmlwdGlvbjogXCJOYXR1cmFsIGxhbmd1YWdlIGRpYWdub3N0aWMgc3VtbWFyeVwiIH0sXG4gICAgICAgIGFmZmVjdGVkX3N0cmVhbXM6IHtcbiAgICAgICAgICB0eXBlOiBcImFycmF5XCIsXG4gICAgICAgICAgaXRlbXM6IHtcbiAgICAgICAgICAgIHR5cGU6IFwib2JqZWN0XCIsXG4gICAgICAgICAgICBwcm9wZXJ0aWVzOiB7XG4gICAgICAgICAgICAgIHN0cmVhbV9pZDogeyB0eXBlOiBcInN0cmluZ1wiIH0sXG4gICAgICAgICAgICAgIHNvdXJjZTogeyB0eXBlOiBcInN0cmluZ1wiIH0sXG4gICAgICAgICAgICAgIGRlc3RpbmF0aW9uOiB7IHR5cGU6IFwic3RyaW5nXCIgfSxcbiAgICAgICAgICAgICAgaXNzdWVfdHlwZTogeyB0eXBlOiBcInN0cmluZ1wiIH0sXG4gICAgICAgICAgICB9LFxuICAgICAgICAgICAgcmVxdWlyZWQ6IFtcInN0cmVhbV9pZFwiXSxcbiAgICAgICAgICB9LFxuICAgICAgICAgIG1heEl0ZW1zOiA1MCxcbiAgICAgICAgfSxcbiAgICAgICAgcm9vdF9jYXVzZV9pbmRpY2F0b3JzOiB7XG4gICAgICAgICAgdHlwZTogXCJhcnJheVwiLFxuICAgICAgICAgIGl0ZW1zOiB7XG4gICAgICAgICAgICB0eXBlOiBcIm9iamVjdFwiLFxuICAgICAgICAgICAgcHJvcGVydGllczoge1xuICAgICAgICAgICAgICBpbmRpY2F0b3I6IHsgdHlwZTogXCJzdHJpbmdcIiB9LFxuICAgICAgICAgICAgICBjb25maWRlbmNlOiB7IHR5cGU6IFwic3RyaW5nXCIsIGVudW06IFtcImhpZ2hcIiwgXCJtZWRpdW1cIiwgXCJsb3dcIl0gfSxcbiAgICAgICAgICAgICAgZXZpZGVuY2VfcmVmczogeyB0eXBlOiBcImFycmF5XCIsIGl0ZW1zOiB7IHR5cGU6IFwic3RyaW5nXCIgfSB9LFxuICAgICAgICAgICAgfSxcbiAgICAgICAgICAgIHJlcXVpcmVkOiBbXCJpbmRpY2F0b3JcIiwgXCJjb25maWRlbmNlXCJdLFxuICAgICAgICAgIH0sXG4gICAgICAgICAgbWF4SXRlbXM6IDEwLFxuICAgICAgICB9LFxuICAgICAgICByZWNvbW1lbmRlZF9hY3Rpb25zOiB7XG4gICAgICAgICAgdHlwZTogXCJhcnJheVwiLFxuICAgICAgICAgIGl0ZW1zOiB7IHR5cGU6IFwic3RyaW5nXCIgfSxcbiAgICAgICAgICBtYXhJdGVtczogMTAsXG4gICAgICAgIH0sXG4gICAgICAgIGNvbmZpZGVuY2VfbGV2ZWw6IHsgdHlwZTogXCJzdHJpbmdcIiwgZW51bTogW1wiaGlnaFwiLCBcIm1lZGl1bVwiLCBcImxvd1wiXSB9LFxuICAgICAgICBjb21wYXJpc29uX3dpdGhfcmVhY2hhYmlsaXR5X2FuYWx5emVyOiB7IHR5cGU6IFwic3RyaW5nXCIgfSxcbiAgICAgICAgZGF0YV9zdWZmaWNpZW5jeToge1xuICAgICAgICAgIHR5cGU6IFwib2JqZWN0XCIsXG4gICAgICAgICAgcHJvcGVydGllczoge1xuICAgICAgICAgICAgc3VmZmljaWVudDogeyB0eXBlOiBcImJvb2xlYW5cIiB9LFxuICAgICAgICAgICAgd2FybmluZzogeyB0eXBlOiBcInN0cmluZ1wiIH0sXG4gICAgICAgICAgICByZWNvbW1lbmRlZF9kdXJhdGlvbl9taW51dGVzOiB7IHR5cGU6IFwiaW50ZWdlclwiIH0sXG4gICAgICAgICAgfSxcbiAgICAgICAgfSxcbiAgICAgICAgc3RlcHNfY29tcGxldGVkOiB7XG4gICAgICAgICAgdHlwZTogXCJhcnJheVwiLFxuICAgICAgICAgIGl0ZW1zOiB7IHR5cGU6IFwic3RyaW5nXCIsIGVudW06IFtcInN0YXJ0X2NhcHR1cmVcIiwgXCJzdG9wX2NhcHR1cmVcIiwgXCJ0cmFuc2Zvcm1fY2FwdHVyZVwiLCBcImRpYWdub3NlX3RjcF9zdHJlYW1cIl0gfSxcbiAgICAgICAgfSxcbiAgICAgICAgZXJyb3I6IHtcbiAgICAgICAgICB0eXBlOiBcIm9iamVjdFwiLFxuICAgICAgICAgIHByb3BlcnRpZXM6IHtcbiAgICAgICAgICAgIGZhaWxlZF9zdGVwOiB7IHR5cGU6IFwic3RyaW5nXCIgfSxcbiAgICAgICAgICAgIG1lc3NhZ2U6IHsgdHlwZTogXCJzdHJpbmdcIiB9LFxuICAgICAgICAgIH0sXG4gICAgICAgIH0sXG4gICAgICB9LFxuICAgICAgcmVxdWlyZWQ6IFtcInN0YXR1c1wiLCBcInN0ZXBzX2NvbXBsZXRlZFwiXSxcbiAgICB9LFxuICAgIGNhdGVnb3J5OiBcImFuYWx5c2lzXCIsXG4gICAgcmVxdWlyZXNBdXRoOiB0cnVlLFxuICB9LFxufTtcblxuLy8g4pSA4pSA4pSAIEhlbHBlciBGdW5jdGlvbnMg4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSAXG5cbi8qKlxuICogR2V0IHRoZSBzY2hlbWEgZW50cnkgZm9yIGEgZ2l2ZW4gYWN0aW9uIG5hbWUuXG4gKiBAcGFyYW0gYWN0aW9uTmFtZSAtIFRoZSBhY3Rpb24gbmFtZSB0byBsb29rIHVwXG4gKiBAcmV0dXJucyBUaGUgc2NoZW1hIGVudHJ5IG9yIHVuZGVmaW5lZCBpZiBub3QgZm91bmRcbiAqL1xuZXhwb3J0IGZ1bmN0aW9uIGdldEFjdGlvblNjaGVtYShhY3Rpb25OYW1lOiBzdHJpbmcpOiBBY3Rpb25TY2hlbWFFbnRyeSB8IHVuZGVmaW5lZCB7XG4gIHJldHVybiBBQ1RJT05fU0NIRU1BU1thY3Rpb25OYW1lXTtcbn1cblxuLyoqXG4gKiBHZXQgYWxsIHJlZ2lzdGVyZWQgYWN0aW9uIG5hbWVzLlxuICogQHJldHVybnMgQXJyYXkgb2YgYWxsIGFjdGlvbiBuYW1lcyBpbiB0aGUgcmVnaXN0cnlcbiAqL1xuZXhwb3J0IGZ1bmN0aW9uIGdldEFjdGlvbk5hbWVzKCk6IHN0cmluZ1tdIHtcbiAgcmV0dXJuIE9iamVjdC5rZXlzKEFDVElPTl9TQ0hFTUFTKTtcbn1cblxuLyoqXG4gKiBHZXQgYWN0aW9uIG5hbWVzIGZpbHRlcmVkIGJ5IGNhdGVnb3J5LlxuICogQHBhcmFtIGNhdGVnb3J5IC0gVGhlIGNhdGVnb3J5IHRvIGZpbHRlciBieVxuICogQHJldHVybnMgQXJyYXkgb2YgYWN0aW9uIG5hbWVzIGluIHRoZSBnaXZlbiBjYXRlZ29yeVxuICovXG5leHBvcnQgZnVuY3Rpb24gZ2V0QWN0aW9uc0J5Q2F0ZWdvcnkoY2F0ZWdvcnk6IEFjdGlvbkNhdGVnb3J5KTogc3RyaW5nW10ge1xuICByZXR1cm4gT2JqZWN0LmVudHJpZXMoQUNUSU9OX1NDSEVNQVMpXG4gICAgLmZpbHRlcigoWywgc2NoZW1hXSkgPT4gc2NoZW1hLmNhdGVnb3J5ID09PSBjYXRlZ29yeSlcbiAgICAubWFwKChbbmFtZV0pID0+IG5hbWUpO1xufVxuXG4vKipcbiAqIEdldCBhY3Rpb24gbmFtZXMgdGhhdCByZXF1aXJlIGF1dGhvcml6YXRpb24uXG4gKiBAcmV0dXJucyBBcnJheSBvZiBhY3Rpb24gbmFtZXMgcmVxdWlyaW5nIGF1dGhcbiAqL1xuZXhwb3J0IGZ1bmN0aW9uIGdldEF1dGhSZXF1aXJlZEFjdGlvbnMoKTogc3RyaW5nW10ge1xuICByZXR1cm4gT2JqZWN0LmVudHJpZXMoQUNUSU9OX1NDSEVNQVMpXG4gICAgLmZpbHRlcigoWywgc2NoZW1hXSkgPT4gc2NoZW1hLnJlcXVpcmVzQXV0aClcbiAgICAubWFwKChbbmFtZV0pID0+IG5hbWUpO1xufVxuXG4vKipcbiAqIENoZWNrIHdoZXRoZXIgYSBnaXZlbiBhY3Rpb24gbmFtZSBpcyB2YWxpZCAoZXhpc3RzIGluIHJlZ2lzdHJ5KS5cbiAqIEBwYXJhbSBhY3Rpb25OYW1lIC0gVGhlIGFjdGlvbiBuYW1lIHRvIHZhbGlkYXRlXG4gKiBAcmV0dXJucyB0cnVlIGlmIHRoZSBhY3Rpb24gZXhpc3RzIGluIHRoZSByZWdpc3RyeVxuICovXG5leHBvcnQgZnVuY3Rpb24gaXNWYWxpZEFjdGlvbihhY3Rpb25OYW1lOiBzdHJpbmcpOiBib29sZWFuIHtcbiAgcmV0dXJuIGFjdGlvbk5hbWUgaW4gQUNUSU9OX1NDSEVNQVM7XG59XG4iXX0=