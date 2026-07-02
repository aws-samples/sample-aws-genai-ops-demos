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

/** JSON Schema type definition (draft-07 compatible with ajv) */
export interface JSONSchema {
  $schema?: string;
  type?: string | string[];
  properties?: Record<string, JSONSchema>;
  required?: string[];
  additionalProperties?: boolean;
  items?: JSONSchema;
  minItems?: number;
  maxItems?: number;
  minimum?: number;
  maximum?: number;
  minLength?: number;
  maxLength?: number;
  pattern?: string;
  enum?: (string | number | boolean | null)[];
  description?: string;
  default?: unknown;
  format?: string;
  oneOf?: JSONSchema[];
  anyOf?: JSONSchema[];
  allOf?: JSONSchema[];
}

/** Action category classification */
export type ActionCategory = "capture" | "analysis" | "utility";

/** Schema registry entry for a single action */
export interface ActionSchemaEntry {
  input: JSONSchema;
  output: JSONSchema;
  category: ActionCategory;
  requiresAuth: boolean;
  /** Optional per-entry MCP tool description override. When set, takes highest priority in the description resolution chain. */
  mcpDescription?: string;
}

/** The complete action schema registry type */
export type ActionSchemaRegistry = Record<string, ActionSchemaEntry>;

// ─── Shared Schema Fragments ────────────────────────────────────────────────

const eniIdsSchema: JSONSchema = {
  type: "array",
  items: { type: "string", pattern: "^eni-[a-f0-9]{8,17}$", description: "ENI identifier" },
  minItems: 1,
  maxItems: 5,
  description: "List of ENI IDs to target (1-5)",
};

const captureIdSchema: JSONSchema = {
  type: "string",
  minLength: 1,
  description: "Capture session identifier",
};

const targetHostSchema: JSONSchema = {
  type: "string",
  description: "Optional target host for filtering (IP or hostname)",
};

const analysisFocusSchema: JSONSchema = {
  type: "string",
  enum: ["tls", "tcp_health", "dns", "general"],
  default: "general",
  description: "Analysis focus area",
};

const durationMinutesSchema: JSONSchema = {
  type: "integer",
  minimum: 1,
  maximum: 10,
  default: 2,
  description: "Capture duration in minutes (1-10)",
};

const timestampSchema: JSONSchema = {
  type: "string",
  format: "date-time",
  description: "ISO 8601 timestamp",
};

const statusResponseSchema: JSONSchema = {
  type: "object",
  properties: {
    status: { type: "string", enum: ["success", "error"] },
    message: { type: "string", description: "Human-readable status message" },
  },
  required: ["status", "message"],
};

// ─── Action Schema Registry ─────────────────────────────────────────────────

export const ACTION_SCHEMAS: ActionSchemaRegistry = {
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
export function getActionSchema(actionName: string): ActionSchemaEntry | undefined {
  return ACTION_SCHEMAS[actionName];
}

/**
 * Get all registered action names.
 * @returns Array of all action names in the registry
 */
export function getActionNames(): string[] {
  return Object.keys(ACTION_SCHEMAS);
}

/**
 * Get action names filtered by category.
 * @param category - The category to filter by
 * @returns Array of action names in the given category
 */
export function getActionsByCategory(category: ActionCategory): string[] {
  return Object.entries(ACTION_SCHEMAS)
    .filter(([, schema]) => schema.category === category)
    .map(([name]) => name);
}

/**
 * Get action names that require authorization.
 * @returns Array of action names requiring auth
 */
export function getAuthRequiredActions(): string[] {
  return Object.entries(ACTION_SCHEMAS)
    .filter(([, schema]) => schema.requiresAuth)
    .map(([name]) => name);
}

/**
 * Check whether a given action name is valid (exists in registry).
 * @param actionName - The action name to validate
 * @returns true if the action exists in the registry
 */
export function isValidAction(actionName: string): boolean {
  return actionName in ACTION_SCHEMAS;
}
