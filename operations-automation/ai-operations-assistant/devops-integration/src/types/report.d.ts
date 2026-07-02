/**
 * Diagnostic report interfaces for LLM-optimized output.
 * These types define the structured format that DevOps Agent consumes
 * for packet-level findings and troubleshooting conclusions.
 *
 * Requirements: 3.1, 3.2, 3.3, 3.4, 3.6
 */
/**
 * Information about an affected TCP/UDP stream identified during diagnosis.
 */
export interface StreamInfo {
    /** Source IP address */
    source_ip: string;
    /** Destination IP address */
    destination_ip: string;
    /** Source port number */
    source_port: number;
    /** Destination port number */
    destination_port: number;
    /** Protocol (e.g., "TCP", "UDP") */
    protocol: string;
    /** Brief description of the stream's issue */
    description: string;
    /** Number of packets observed in this stream */
    packet_count: number;
}
/**
 * A root cause indicator identified from packet analysis.
 */
export interface RootCause {
    /** Brief description of the root cause */
    description: string;
    /**
     * Confidence level based on corroborating evidence:
     * - "high": ≥3 independent packet indicators
     * - "medium": 2 independent packet indicators
     * - "low": 1 indicator or inference from absence of expected traffic
     */
    confidence_level: "high" | "medium" | "low";
    /** Category of the root cause */
    category: "tls" | "tcp_health" | "dns" | "connectivity" | "performance" | "general";
    /** Supporting evidence references for this root cause */
    evidence_refs: string[];
    /** TLS-specific details, present when category is "tls" */
    tls_details?: TlsDetails;
    /** Connection drop details, present when issue involves connection drops */
    connection_drop_details?: ConnectionDropDetails;
}
/**
 * TLS-specific diagnostic details included when TLS handshake issues are identified.
 * Requirement 3.2: Must include Client Hello size, key exchange algorithm,
 * fragmentation evidence, and middlebox behavior.
 */
export interface TlsDetails {
    /** Client Hello message size in bytes */
    client_hello_size_bytes: number;
    /** Key exchange algorithm name (e.g., "X25519", "P-256") */
    key_exchange_algorithm: string;
    /** Fragmentation evidence */
    fragmentation: {
        /** Number of fragments the Client Hello was split into */
        fragment_count: number;
        /** Size of each fragment in bytes */
        fragment_sizes: number[];
    };
    /** Observed middlebox behavior */
    middlebox_behavior: {
        /** Type of action taken by the middlebox */
        action: "drop" | "reset" | "modification";
        /** Type of network appliance if determinable */
        appliance_type?: string;
    };
}
/**
 * Connection drop diagnostic details.
 * Requirement 3.3: Must include TCP RST origin analysis, timing data,
 * and correlation with network appliance behavior.
 */
export interface ConnectionDropDetails {
    /** TCP RST origin analysis */
    rst_origin: {
        /** Source IP that sent the RST */
        source_ip: string;
        /** Classification of where the RST originated */
        origin_classification: "client" | "server" | "intermediate_device";
    };
    /** Timing data in milliseconds relative to connection initiation */
    timing_ms: number;
    /** Network appliance correlation */
    appliance_correlation: {
        /** Type of appliance implicated */
        appliance_type: string;
        /** Description of the appliance's role in the drop */
        behavior_description: string;
    };
}
/**
 * A reference to raw packet evidence supporting the diagnostic findings.
 */
export interface EvidenceRef {
    /** Type of evidence (e.g., "packet_capture", "flow_log", "athena_query") */
    type: string;
    /** Human-readable description of the evidence */
    description: string;
    /** Reference to where the evidence can be retrieved (S3 URI, query ID, etc.) */
    location: string;
    /** ISO 8601 timestamp when the evidence was captured */
    timestamp: string;
    /** Size in bytes of the evidence data */
    size_bytes?: number;
}
/**
 * Data sufficiency warning when capture data is insufficient for conclusive analysis.
 * Requirement 3.6: Warning when <10 relevant packets or duration < one connection lifecycle.
 */
export interface DataSufficiencyWarning {
    /** Reason the data is insufficient */
    reason: string;
    /** Number of relevant packets found */
    relevant_packet_count: number;
    /** Recommended minimum capture duration in minutes */
    recommended_duration_minutes: number;
    /** Traffic patterns needed to produce a conclusive diagnosis */
    required_traffic_patterns: string[];
}
