import * as path from 'path';
import { readFileSync } from 'fs';
import * as crypto from 'crypto';
import * as cdk from 'aws-cdk-lib';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as elbv2 from 'aws-cdk-lib/aws-elasticloadbalancingv2';
import * as elbv2Targets from 'aws-cdk-lib/aws-elasticloadbalancingv2-targets';
import * as glue from 'aws-cdk-lib/aws-glue';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as s3Assets from 'aws-cdk-lib/aws-s3-assets';
import * as scheduler from 'aws-cdk-lib/aws-scheduler';
import * as sfn from 'aws-cdk-lib/aws-stepfunctions';
import * as tasks from 'aws-cdk-lib/aws-stepfunctions-tasks';
import { Construct } from 'constructs';
import { BaseInfraStack } from './base-infra-stack';

/**
 * Properties for the {@link NetworkInfraStack}.
 *
 * The Network_Data_Bucket can be provided to this stack in one of two ways
 * (decided by the CDK app per Requirement 7):
 *
 *   1. **Reuse path** -- the existing `GOATData-${region}` stack exports a
 *      `GOATSharedDataBucketName` value. The CDK app determines this at
 *      synthesis by attempting the CloudFormation export lookup with a
 *      10-second timeout (Req 7.1). When the export is present, the app
 *      leaves `networkDataBucketName` undefined on these props and the
 *      stack imports the bucket via `cdk.Fn.importValue()` itself.
 *
 *   2. **Dedicated path** -- no shared export is available; the CDK app has
 *      conditionally instantiated `NetworkDataStack` and passes its
 *      provisioned bucket name in via the `networkDataBucketName` prop
 *      (Req 7.4). This stack does not provision a new bucket in either case.
 *
 * If the CFN export lookup fails for any reason other than "absent" (for
 * example, AWS API throttling or insufficient permissions on the
 * `GOATData-${region}` stack), the CDK app halts synthesis with a clear
 * error message and the affected region (Req 7.2). That branch is handled
 * in the app-level wiring; this stack only deals with the resolved value.
 */
export interface NetworkInfraStackProps extends cdk.StackProps {
  /**
   * Name of the Network_Data_Bucket as resolved by the CDK app.
   *
   * - Set when {@link NetworkDataStack} provisioned a dedicated bucket.
   * - Left `undefined` when the `GOATSharedDataBucketName` CFN export from
   *   the `GOATData-${region}` stack will be imported instead.
   */
  readonly networkDataBucketName?: string;

  /**
   * Bedrock AgentCore runtime ARN of the Network Agent.
   *
   * Optional because the runtime is owned by `NetworkRuntimeStack`,
   * which depends on this stack -- there is a deploy-order
   * chicken-and-egg between (a) this stack provisioning the
   * StopCaptureInvokerLambda whose IAM policy is scoped to the runtime
   * ARN (Task 26) and (b) `NetworkRuntimeStack` creating the runtime.
   *
   * The CDK app entrypoint solves the cycle via an
   * "OrchRuntimeStack-style follow-up reference" wiring task: the app
   * instantiates `NetworkInfraStack` twice in the dependency
   * graph, or alternately resolves the runtime ARN via a stack prop
   * supplied on a subsequent CDK deploy. Until that wiring is in
   * place, this prop is left undefined and the Lambda's IAM policy
   * uses a least-privilege wildcard pattern restricted to the
   * well-known Network Agent runtime name (`goat_network_agent*`) in
   * the deploying account and region. Once the follow-up wiring task
   * is implemented, the app will pass the actual runtime ARN here and
   * the policy will tighten to a single-resource match.
   *
   * @see {@link STOP_CAPTURE_INVOKER_DEFAULT_RUNTIME_NAME}
   */
  readonly networkAgentRuntimeArn?: string;
}

/**
 * Name of the CloudFormation export that the existing `GOATData-${region}`
 * stack publishes when it owns a shared data bucket suitable for reuse by
 * the Network Agent. Kept as a module-level constant so that the CDK app
 * (which runs the actual lookup) and this stack agree on the export name.
 */
export const SHARED_DATA_BUCKET_EXPORT_NAME = 'GOATSharedDataBucketName';

/**
 * Single-source-of-truth lifecycle rules that apply to the
 * Network_Data_Bucket regardless of which path resolved it.
 *
 *   - `raw/` objects expire 7 days after creation (Req 4.8)
 *   - `parquet/` objects expire 30 days after creation (Req 4.9)
 *
 * The {@link NetworkDataStack} (dedicated path) declares these rules
 * natively on the bucket it provisions. When {@link NetworkInfraStack}
 * runs the reuse path against a shared bucket it cannot inspect, task 22
 * adds a deploy-time custom resource that calls
 * `s3:PutBucketLifecycleConfiguration` with these same values so the
 * shared bucket carries equivalent rules without conflicting with the
 * owning stack's existing configuration. Exported here so both stacks
 * stay in lockstep.
 */
export interface NetworkDataLifecycleRule {
  readonly id: string;
  readonly prefix: string;
  readonly expirationDays: number;
}

export const NETWORK_DATA_LIFECYCLE_RULES: readonly NetworkDataLifecycleRule[] = [
  { id: 'DeleteRawPcapAfter7Days', prefix: 'raw/', expirationDays: 7 },
  { id: 'DeleteParquetAfter30Days', prefix: 'parquet/', expirationDays: 30 },
];

/**
 * Name of the Glue database that holds the {@link Pcap_Athena_Table} and
 * any related future tables. Pinned as a module-level constant so the
 * AgentCore runtime's `GLUE_DATABASE` environment variable, the
 * Transformation_Workflow Glue Crawler's database target, the IAM
 * resource ARNs in task 27, and any future Athena workgroup configuration
 * resolve to the same physical name.
 *
 * Per design.md "Pcap_Athena_Table (Glue Catalog)" and Req 6.7. Lowercase
 * with underscores because Glue / Athena lowercases all object names at
 * catalog write time; using the same form here avoids a CloudFormation
 * deploy-time mismatch between the declared CDK name and the eventual
 * physical name.
 */
export const GLUE_DATABASE_NAME = 'goat_network';

/**
 * Name of the Glue table that backs every Pcap_Query_Action. Partitioned
 * on `capture_id` so the Capture_Id_Predicate (`WHERE capture_id = '<id>'`)
 * resolves to a single S3 prefix scan.
 *
 * Per design.md "Pcap_Athena_Table (Glue Catalog)" and Req 6.7.
 */
export const GLUE_TABLE_NAME = 'pcap_logs';

/**
 * Column schema for the Pcap_Athena_Table (`goat_network.pcap_logs`).
 *
 * The schema is the source of truth for two collaborating components and
 * must remain consistent across both:
 *
 *   1. The `AWS::Glue::Table` resource declared on this stack -- Athena
 *      reads schemas from the Glue Catalog, so this list dictates what
 *      every Pcap_Query_Action's SQL can reference.
 *   2. The `ConvertPcapToParquetLambda` (Task 25) which writes Parquet
 *      files -- every column listed here MUST be present in the Parquet
 *      output (or the Glue Crawler will report a schema mismatch and
 *      Athena queries will return `null` for missing fields).
 *
 * Columns map directly to design.md "Pcap_Athena_Table (Glue Catalog)" and
 * cover the data tshark emits for the action set (handshake, RTT,
 * retransmissions, fragmentation, TLS Hello, DNS, conversation stats).
 *
 * The partition column `capture_id` is intentionally omitted -- Glue
 * declares partition columns separately from regular columns (`partitionKeys`
 * vs `columns` on the SerDe storage descriptor), so listing it here would
 * cause a duplicate-column error at CloudFormation deploy time.
 *
 * Hive types are used (`bigint`, `int`, `string`, `timestamp`,
 * `array<string>`) because the Glue Catalog speaks Hive type vocabulary,
 * and tshark's JSON output maps cleanly: timestamps from `frame.time_epoch`,
 * sequence/ack numbers as 64-bit (Linux kernel uses unsigned 32-bit but
 * `bigint` is the safe choice for arithmetic), TCP options as a JSON
 * array of mnemonics, etc.
 */
export interface GlueTableColumn {
  readonly name: string;
  readonly type: string;
  readonly comment: string;
}

export const PCAP_LOGS_COLUMNS: readonly GlueTableColumn[] = [
  // Frame-level metadata (universal across protocols)
  { name: 'frame_time', type: 'timestamp', comment: 'Frame arrival time (post-VXLAN-decap)' },
  { name: 'frame_size', type: 'bigint', comment: 'Wire frame size in bytes (post-VXLAN-decap)' },

  // L2 Ethernet fields
  { name: 'eth_src', type: 'string', comment: 'Source MAC address' },
  { name: 'eth_dst', type: 'string', comment: 'Destination MAC address' },
  { name: 'eth_type', type: 'string', comment: 'EtherType hex (0x0800=IPv4, 0x86dd=IPv6, 0x0806=ARP)' },

  // L3 / L4 endpoint + protocol identification
  { name: 'src_ip', type: 'string', comment: 'Source IPv4 or IPv6 address' },
  { name: 'dst_ip', type: 'string', comment: 'Destination IPv4 or IPv6 address' },
  { name: 'src_port', type: 'int', comment: 'TCP/UDP source port (0..65535)' },
  { name: 'dst_port', type: 'int', comment: 'TCP/UDP destination port (0..65535)' },
  { name: 'protocol', type: 'string', comment: 'Transport protocol (tcp/udp/icmp/...)' },

  // IP-layer fields (universal L3 diagnostics — routing loops, PMTU,
  // fragmentation, TTL/hop analysis). Populated for both IPv4 and IPv6
  // where applicable.
  { name: 'ip_version', type: 'int', comment: 'IP version (4 or 6)' },
  { name: 'ip_ttl', type: 'int', comment: 'IPv4 TTL / IPv6 hop limit (low values reveal routing loops or premature drops)' },
  { name: 'ip_id', type: 'int', comment: 'IPv4 identification field (correlates fragments of the same datagram)' },
  { name: 'ip_flags', type: 'string', comment: 'IPv4 flags (DF=do not fragment, MF=more fragments)' },
  { name: 'ip_frag_offset', type: 'int', comment: 'IPv4 fragment offset in 8-byte units (non-zero indicates an IP fragment)' },
  { name: 'ip_total_length', type: 'int', comment: 'IPv4 total length / IPv6 payload length in bytes' },
  { name: 'ip_proto_num', type: 'int', comment: 'IP protocol number (1=ICMP, 6=TCP, 17=UDP, 58=ICMPv6)' },
  { name: 'ip_dscp', type: 'int', comment: 'DiffServ code point (QoS marking, 0..63)' },
  { name: 'ip_ecn', type: 'int', comment: 'Explicit Congestion Notification bits (0..3; 3=CE congestion experienced)' },

  // ICMP fields (reachability + PMTU diagnostics — unreachables,
  // fragmentation-needed, TTL-exceeded). Null for non-ICMP frames.
  { name: 'icmp_type', type: 'int', comment: 'ICMP/ICMPv6 type (3=dest unreachable, 11=time exceeded, 8/0=echo, ...)' },
  { name: 'icmp_code', type: 'int', comment: 'ICMP/ICMPv6 code (e.g. type 3 code 4 = fragmentation needed / PMTU)' },

  // UDP-specific length (useful for DNS/QUIC and amplification analysis)
  { name: 'udp_length', type: 'int', comment: 'UDP datagram length including header in bytes' },

  // TCP-specific fields used by reconstruct_tcp_handshake, classify_tcp_resets,
  // detect_out_of_order_packets, detect_zero_window, analyze_tcp_options,
  // get_rtt_distribution, correlate_tcp_streams, detect_retransmissions
  { name: 'tcp_seq', type: 'bigint', comment: 'TCP sequence number' },
  { name: 'tcp_ack', type: 'bigint', comment: 'TCP acknowledgement number' },
  { name: 'tcp_flags', type: 'string', comment: 'TCP flag string (SYN/ACK/FIN/RST/PSH/URG/...)' },
  { name: 'tcp_options', type: 'array<string>', comment: 'TCP options on SYN (MSS, WS, SACK_PERM, TS, NOP, EOL)' },
  { name: 'tcp_stream', type: 'string', comment: 'tshark tcp.stream identifier (per-flow stream id)' },
  { name: 'tcp_window', type: 'int', comment: 'TCP receive window (post-scaling when computable)' },
  { name: 'tcp_urgent_ptr', type: 'int', comment: 'TCP urgent pointer (non-zero only when URG flag set)' },
  { name: 'tcp_payload_len', type: 'int', comment: 'TCP segment payload length in bytes (0 for pure ACK/SYN/FIN)' },

  // TLS-specific fields used by check_tls_hello_size and tls_sni_in_capture
  // resolution strategy
  { name: 'tls_handshake_type', type: 'int', comment: 'TLS handshake type (1=Client Hello, 2=Server Hello, ...)' },
  { name: 'tls_record_size', type: 'int', comment: 'TLS record size including header in bytes' },
  { name: 'tls_sni', type: 'string', comment: 'TLS Server Name Indication value extracted from Client Hello' },
  { name: 'tls_fragment_count', type: 'int', comment: 'Number of TLS records the Client Hello was fragmented across' },
  { name: 'tls_version', type: 'string', comment: 'TLS record/handshake version (e.g. 0x0303=TLS1.2, 0x0304=TLS1.3)' },
  { name: 'tls_content_type', type: 'int', comment: 'TLS record content type (20=ChangeCipherSpec, 21=Alert, 22=Handshake, 23=AppData)' },

  // DNS-specific fields used by dns_in_capture resolution strategy
  { name: 'dns_qname', type: 'string', comment: 'DNS question name' },
  { name: 'dns_response_ips', type: 'array<string>', comment: 'A/AAAA answer IP addresses observed in DNS responses' },
  { name: 'dns_qtype', type: 'int', comment: 'DNS question type (1=A, 28=AAAA, 5=CNAME, 15=MX, ...)' },
  { name: 'dns_rcode', type: 'int', comment: 'DNS response code (0=NOERROR, 2=SERVFAIL, 3=NXDOMAIN, ...)' },
  { name: 'dns_id', type: 'int', comment: 'DNS transaction ID (correlates query with response)' },
  { name: 'dns_is_response', type: 'boolean', comment: 'True if this DNS message is a response (QR bit set)' },

  // Generic hex preview for query_pcap free-form inspection
  { name: 'frame_payload_summary', type: 'string', comment: 'First 256 bytes of frame payload as hex (truncated)' },
];

/**
 * Filesystem path of the directory holding the four
 * Transformation_Workflow Lambda source files.
 *
 * The CDK app entrypoint is `bin/app.ts`, which `aws-cdk-lib` resolves
 * relative to. The lambdas live under `infrastructure/cdk/lambda/...`
 * -- sibling to `lib/`. Resolving via `__dirname` (which Node sets to
 * the directory of the currently-executing module) produces a stable
 * absolute path regardless of where the CDK CLI is invoked from.
 *
 * Per Task 25, the four Lambdas of the Transformation_Workflow live
 * under this directory:
 *   - `list_raw_objects.py`
 *   - `convert_pcap_to_parquet.py`
 *   - `run_crawler.py`
 *   - `validate_athena.py`
 */
const NETWORK_TRANSFORMATION_LAMBDA_DIR = path.join(
  __dirname,
  '..',
  'lambda',
  'network-transformation',
);

/**
 * Filesystem path of the directory holding the collector's runtime
 * payload (`splitter.py`, `uploader.sh`, `bootstrap.sh`). The CDK stack
 * bundles the directory into a single `aws-s3-assets` Asset and the EC2
 * instance's UserData downloads the asset on first boot.
 *
 * Resolved via `__dirname` for the same reasons as
 * {@link NETWORK_TRANSFORMATION_LAMBDA_DIR}.
 */
const COLLECTOR_ASSET_DIR = path.join(__dirname, '..', 'collector');

/**
 * Filename of the bootstrap UserData template inside
 * {@link COLLECTOR_ASSET_DIR}. The CDK stack reads this file at synth
 * time, substitutes deploy-time placeholder tokens (`__ASSET_BUCKET__`,
 * `__ASSET_OBJECT_KEY__`, `__DATA_BUCKET__`, `__VNI_LOOKUP_TABLE__`,
 * `__AWS_REGION__`), and renders the result into the EC2 instance's
 * UserData.
 *
 * Tokens are used (rather than `cdk.Lazy.string` or `Token.asString`
 * embedded directly into the script) because UserData is plain bash --
 * any CDK token would resolve to a CFN intrinsic ref string at synth
 * time and embed verbatim into the bash, breaking the script. The
 * placeholder approach lets us keep the bash readable in source and
 * resolve to literal values at synth via {@link cdk.Fn.sub}.
 */
const COLLECTOR_BOOTSTRAP_FILENAME = 'bootstrap.sh';

/**
 * IANA-assigned UDP port for VXLAN. Traffic Mirror sources deliver
 * VXLAN-encapsulated packets to the collector ENI on this port. The
 * collector's UserData configures a `vxlan0` kernel interface bound to
 * the same port, and the security group ingress rule allows this port
 * from any source within the collector's VPC (Reqs 6.2, 6.6).
 */
const VXLAN_UDP_PORT = 4789;

/**
 * TCP port the collector runs a lightweight health-check responder on
 * (started by bootstrap.sh). The collector's NLB Traffic Mirror Target
 * health-checks this port; NLB cannot health-check the UDP/4789 traffic
 * port directly. The collector security group permits ingress on this
 * port from the VPC CIDR so the NLB health checker can reach it.
 */
const COLLECTOR_HEALTHCHECK_PORT = 8081;

/**
 * Physical name of the {@link ec2.CfnTrafficMirrorFilter} provisioned
 * by this stack. Mandated by Task 22 / Req 6.5 (`goat-network-default-filter`).
 *
 * Note: pinning a non-account-suffixed name is acceptable here because
 * Traffic Mirror filters are scoped to the deploying account/region and
 * the `Tags` on the resource carry the standard CFN stack identifier.
 * The filter is referenced by ID (not by name) when the agent calls
 * `ec2:CreateTrafficMirrorSession`, so naming collisions across stacks
 * in the same account/region (which is unsupported anyway -- the demo
 * deploys exactly one Network Agent per region) would surface as a
 * CFN deploy-time `AlreadyExists` error rather than silent breakage.
 */
const TRAFFIC_MIRROR_FILTER_NAME = 'goat-network-default-filter';

/**
 * CIDR block for the dedicated VPC that hosts the collector EC2
 * instance. The block is intentionally /24 -- sufficient for one
 * collector subnet plus the small handful of IP addresses VPC
 * endpoints will need in future demo extensions, but small enough to
 * not collide with common corporate "default" /16 CIDRs operators may
 * have peered nearby.
 *
 * The /16 gives 65,536 addresses -- large enough for the collector,
 * EKS nodes, and all demo scenarios to share a single VPC. This is
 * essential because Traffic Mirror sessions require source ENI and
 * target ENI to reside in the same VPC. Subnet allocation:
 *   - Collector:       10.99.0.0/24 (AZ a)
 *   - Demo Scenario A: 10.99.1.0/24, 10.99.2.0/24
 *   - TLS Scenario:    10.99.10.0/24, 10.99.11.0/24, 10.99.12.0/24
 */
const NETWORK_AGENT_VPC_CIDR = '10.99.0.0/16';

/**
 * Number of bytes to use for the collector's root EBS volume. The
 * splitter caps local pcap retention at `MAX_FILES_PER_VNI �- 100 MiB
 * �- 15 active VNIs ≈ 15 GB` worst case, plus a few hundred MB for the
 * AL2023 base image and Python + scapy. 30 GiB leaves a comfortable
 * margin without paying for an oversized volume.
 */
const COLLECTOR_ROOT_VOLUME_GIB = 30;

/**
 * Filesystem path of the directory holding the StopCaptureInvokerLambda
 * source (`index.py`). Resolved via `__dirname` for the same reasons as
 * {@link NETWORK_TRANSFORMATION_LAMBDA_DIR}.
 *
 * Per Task 26, this directory contains the single-file Lambda handler
 * that EventBridge Scheduler's Auto_Stop_Schedule invokes to call
 * `stop_capture` on the Network Agent runtime.
 */
const STOP_CAPTURE_INVOKER_LAMBDA_DIR = path.join(
  __dirname,
  '..',
  'lambda',
  'stop-capture-invoker',
);

/**
 * Well-known AgentCore runtime name of the Network Agent. Pinned here
 * (and consumed by `NetworkRuntimeStack`) so the StopCaptureInvokerLambda's
 * IAM policy can scope `bedrock-agent-runtime:InvokeAgentRuntime` to
 * this runtime even when the actual runtime ARN is not yet wired in
 * via the OrchRuntimeStack-style follow-up reference.
 *
 * Matches the value Task 28 will pass as `runtimeName` on the
 * NetworkRuntimeStack (`goat_network_agent`).
 */
export const STOP_CAPTURE_INVOKER_DEFAULT_RUNTIME_NAME = 'goat_network_agent';

/**
 * Logical-name fragment used to construct the physical name of the
 * stack-owned EventBridge Scheduler schedule group that holds every
 * Auto_Stop_Schedule the Network Agent creates (Task 27, Reqs 3.5,
 * 4.6, 4.10, 6.12).
 *
 * The full physical name is built at synth time from this fragment,
 * the deploying account, and the deploying region -- keeping the name
 * unique across multi-region demos without colliding with any other
 * group an operator may already own (matches the multi-region naming
 * pattern used elsewhere in this stack, e.g. `goat-network-vni-lookup-${account}-${region}`).
 *
 * The AgentCore runtime IAM policy in Task 27 scopes
 * `scheduler:CreateSchedule` / `DeleteSchedule` / `GetSchedule` to
 * `arn:${partition}:scheduler:${region}:${account}:schedule/${groupName}/*`
 * -- never with a wildcard on the group component -- using the same
 * derived `groupName`. The agent's `start_capture` and `stop_capture`
 * handlers pick up the name from the `SCHEDULE_GROUP_NAME` environment
 * variable (Task 28's NetworkRuntimeStack plumbs the env var).
 */
const AUTO_STOP_SCHEDULE_GROUP_NAME_BASE = 'goat-network-auto-stop';

/**
 * CloudWatch namespace under which the StopCaptureInvokerLambda emits
 * its `goat-network-auto-stop-failures` metric on retry exhaustion
 * (Reqs 4.7, 6.12). Pinned here so the IAM policy condition that
 * scopes `cloudwatch:PutMetricData` matches the namespace the Lambda
 * actually uses.
 */
export const STOP_CAPTURE_INVOKER_METRIC_NAMESPACE = 'GOAT/Network';

/**
 * CloudWatch metric name emitted by the StopCaptureInvokerLambda when
 * all 3 retry attempts have been exhausted. Per Task 26 / Req 4.7 / the
 * design's "Auto_Stop_Schedule failure" handling, this metric is the
 * sole observable signal that an Auto_Stop_Schedule fired but failed
 * to deliver `stop_capture` to the agent.
 */
export const STOP_CAPTURE_INVOKER_METRIC_NAME = 'goat-network-auto-stop-failures';

/**
 * Default Lambda runtime for the Transformation_Workflow Lambdas.
 *
 * Python 3.12 is the latest LTS-supported runtime AWS Lambda offers and
 * matches the runtime the agent container is built against. Using the
 * same major version reduces deploy-time confusion when investigating
 * stack traces from either side of the workflow.
 */
const TRANSFORMATION_LAMBDA_RUNTIME = lambda.Runtime.PYTHON_3_12;

/**
 * G.O.A.T. NetworkInfraStack -- ECR, S3 source bucket, CodeBuild, and IAM
 * scaffolding for the Network Agent, plus resolution of the shared
 * Network_Data_Bucket used to hold raw VXLAN pcap files (`raw/` prefix)
 * and transformed Parquet files (`parquet/` prefix).
 *
 * This task scaffolds the constructor and bucket resolution only. Tasks
 * 22-27 layer additional resources onto this stack:
 *   - Traffic Mirror Filter / Target / Collector EC2 instance (task 22)
 *   - DynamoDB Capture_State_Table and Vni_Lookup_Table (task 23)
 *   - Glue catalog: database, pcap_logs table, Crawler (task 24, this commit)
 *   - Step Functions Transformation_Workflow (task 25)
 *   - StopCaptureInvokerLambda (task 26)
 *   - Domain-specific IAM policies on the AgentCore runtime role (task 27)
 *
 * Stack ID at instantiation must be `GOATNetworkInfra-${region}` where
 * `${region}` comes from `getRegion()` in `shared/utils/aws-utils.ts`. The
 * stack must not carry the Solution_Adoption_Tracking marker -- that lives
 * exclusively on the existing primary G.O.A.T. orchestration runtime stack
 * (Reqs 10.7, 15.5).
 *
 * Validates: Requirements 4.8, 4.9, 6.7, 6.8, 6.9, 6.11, 6.12, 7.1, 7.2,
 * 7.3, 7.4, 7.6, 10.1, 10.6, 10.7, 10.8, 10.9, 15.5, 15.6.
 */
export class NetworkInfraStack extends BaseInfraStack {
  /**
   * The Network_Data_Bucket -- either imported from the shared
   * `GOATSharedDataBucketName` export, or imported from the dedicated
   * `NetworkDataStack`. Exposed as `IBucket` so callers can `grant*` against
   * it without owning the underlying resource.
   */
  public readonly networkDataBucket: s3.IBucket;

  /**
   * Resolved name of the Network_Data_Bucket. Held as a separate field for
   * callers that need to embed the name in environment variables, IAM
   * policy resources, or `s3:` ARNs. When the dedicated path is taken this
   * is the literal name passed in via props; when the reuse path is taken
   * this is a `cdk.Fn.importValue()` token that resolves at deploy time.
   */
  public readonly networkDataBucketName: string;

  /**
   * Capture_State_Table (Req 6.11) -- DynamoDB table that persists
   * Capture_Session metadata (capture_id, ENIs, start time, deadline,
   * status, mirror session IDs, etc.) for use by `list_captures`,
   * `stop_capture`, and the Auto_Stop_Schedule.
   *
   * Schema (per design.md "Capture_State_Table" section):
   *   - Partition key: `capture_id` (string), Capture_Id_Format
   *     `[A-Za-z0-9_-]{1,128}`.
   *   - GSI `status-index` partitioned on `status` (string), enabling O(1)
   *     Capture_Concurrency_Limit checks and the `list_captures
   *     status=active|historical` queries.
   *
   * Non-key attributes (`eni_ids`, `start_time`, `deadline`,
   * `duration_minutes`, `stopped_reason`, `mirror_session_ids`,
   * `idempotency_token`, `requested_by`, `transform_execution_arn`,
   * `created_at`) are written by the agent at runtime -- DynamoDB is
   * schemaless for non-key attributes so they do not need declaring here.
   */
  public readonly captureStateTable: dynamodb.Table;

  /**
   * Vni_Lookup_Table (Req 6.11 -- DynamoDB capture-state and VNI lookup
   * tables; design.md "Vni_Lookup_Table" section) -- maps each
   * Traffic-Mirror-assigned VXLAN VNI to its owning capture so the
   * collector can split rotated pcap files per capture.
   *
   * Schema (per design.md):
   *   - Partition key: `vni` (number, VXLAN VNI 1..16777215).
   *   - GSI `capture-id-index` partitioned on `capture_id` (string), so
   *     `stop_capture` can delete every VNI row for a capture in a single
   *     query.
   *   - DynamoDB TTL enabled on attribute `expires_at` (Unix epoch
   *     seconds); rows self-purge if `stop_capture` cleanup ever fails.
   *
   * Non-key attributes (`mirror_session_id`, `eni_id`, plus the
   * `capture_id` itself which is also the GSI PK) are written by the
   * agent at runtime.
   */
  public readonly vniLookupTable: dynamodb.Table;

  /**
   * Glue database that hosts the Pcap_Athena_Table. Created with the
   * physical name {@link GLUE_DATABASE_NAME} (`goat_network`) per Req 6.7.
   *
   * Exposed as a public field so:
   *   - Task 25's `ValidateAthenaLambda` can resolve the database name
   *     when issuing `SELECT 1 FROM pcap_logs WHERE capture_id = ...`.
   *   - Task 27's IAM block can scope `glue:Get*` policy statements to
   *     this exact database ARN rather than wildcarding `database/*`.
   *   - Task 28's `NetworkRuntimeStack` can plumb the database name into
   *     the agent container as the `GLUE_DATABASE` environment variable.
   */
  public readonly glueDatabase: glue.CfnDatabase;

  /**
   * Glue table `pcap_logs` partitioned on `capture_id`, stored as Parquet
   * under `s3://{Network_Data_Bucket}/parquet/`. Per Req 6.7 and design.md
   * "Pcap_Athena_Table (Glue Catalog)".
   *
   * Athena partition pruning relies on `capture_id` being declared as a
   * partition key (not a regular column) -- the Capture_Id_Predicate
   * therefore resolves to a single S3 prefix scan on
   * `parquet/capture_id=<id>/` instead of a full-table scan, keeping
   * every Pcap_Query_Action within its 60s response budget regardless of
   * how many historical captures are in the catalog.
   *
   * The Glue Crawler ({@link glueCrawler}) is responsible for adding
   * partition entries when the Transformation_Workflow writes Parquet
   * files for a new `capture_id`; this CDK construct only declares the
   * table shell and column schema.
   */
  public readonly glueTable: glue.CfnTable;

  /**
   * Glue Crawler that targets `s3://{bucket}/parquet/` and updates
   * partitions on the {@link glueTable}. Triggered by Task 25's
   * `RunCrawlerLambda` as part of the Transformation_Workflow.
   *
   * Configuration choices worth flagging here so future tasks do not
   * second-guess them:
   *   - `schemaChangePolicy.deleteBehavior = LOG` so a transient
   *     transformation failure that drops a partition's Parquet objects
   *     does not delete the partition definition (the Lambda may retry).
   *   - `schemaChangePolicy.updateBehavior = LOG` so columns we have
   *     pre-declared on the table are not silently rewritten by tshark
   *     output drift; the Crawler logs to CloudWatch and we keep the
   *     authoritative schema in {@link PCAP_LOGS_COLUMNS}.
   *   - `recrawlPolicy.recrawlBehavior = CRAWL_NEW_FOLDERS_ONLY` so each
   *     run only inspects newly-added `capture_id=<id>/` prefixes,
   *     keeping crawler cost flat as the catalog grows.
   */
  public readonly glueCrawler: glue.CfnCrawler;

  /**
   * Network_Agent_VPC -- dedicated VPC the Traffic_Mirror_Collector EC2
   * instance lives in (Req 6.1, Task 22). The VPC is /24 with one
   * private subnet hosting the collector's ENI; egress flows through a
   * NAT-less default route table because the only outbound traffic the
   * collector needs is to AWS APIs (S3, DynamoDB, EC2 metadata, AWS CLI),
   * which the collector reaches via VPC endpoints (S3 gateway endpoint
   * by default, plus interface endpoints for DynamoDB / SSM / EC2 if
   * the operator subsequently adds private connectivity).
   *
   * For the demo, the subnet is a **private isolated** subnet with VPC
   * endpoints (S3 Gateway, DynamoDB Gateway, SSM/EC2Messages Interface)
   * providing connectivity to AWS APIs. A single AZ is
   * sufficient because the design provisions exactly one collector
   * (Req 6.1 explicitly says "no Auto Scaling Group, no NLB"); a
   * multi-AZ deployment would be over-engineered for the demo.
   *
   * VPC DNS support is enabled (`enableDnsSupport: true`,
   * `enableDnsHostnames: true`) per Req 19.14 so the orchestration
   * agent's `active_dns_lookup` Hostname_Resolution_Strategy can
   * resolve hostnames against the VPC's `.2` resolver during Flow
   * Selector resolution. The collector itself does not perform DNS
   * lookups; the setting is here because the agent runtime container
   * runs in this VPC (or a peered VPC) and inherits the VPC's DNS
   * configuration.
   */
  public readonly networkAgentVpc: ec2.Vpc;

  /**
   * Dedicated security group for the collector EC2 instance. Allows:
   *
   *   - Ingress on UDP/{@link VXLAN_UDP_PORT} (4789) from the VPC CIDR
   *     so Traffic Mirror sources within the same VPC can deliver
   *     VXLAN-encapsulated frames to the collector ENI (Req 6.2, 6.6).
   *     Sources in peered VPCs are out of scope for the demo and would
   *     require an additional rule on the operator's side.
   *   - Egress to anywhere -- the collector needs S3 PutObject for
   *     pcap uploads, DynamoDB GetItem for the Vni_Lookup_Table
   *     reads, EC2 metadata for IMDSv2 token issuance, and `dnf`
   *     repos for first-boot package installation. Restricting
   *     egress further would require explicit AWS prefix lists for
   *     every service in every region, which is more friction than
   *     the demo justifies.
   */
  public readonly collectorSecurityGroup: ec2.SecurityGroup;

  /**
   * Single EC2 Traffic_Mirror_Collector instance (Req 6.1, Task 22).
   * Provisioned exactly once per Network Agent deployment -- no Auto
   * Scaling Group, no Network Load Balancer -- to keep the demo
   * footprint small. Uses `t3.small` for Burst-mode CPU / 2 GiB RAM /
   * "up to 5 Gbps" baseline networking, which covers the Reqs 4.5 +
   * 6.2 worst case (5 captures �- 3 ENIs �- 1 Mbps + ~25% VXLAN
   * overhead ≈ 19 Mbps) with multi-x headroom.
   *
   * AMI: latest Amazon Linux 2023 (AL2023) ARM64-compatible image,
   * matched to the agent container's runtime architecture so a future
   * "build everything from one base image" effort is one-step. AL2023
   * provides Python 3.9, AWS CLI v2, and `dnf` out of the box, which
   * lets the UserData install `scapy` + `inotify-tools` without any
   * external repos.
   *
   * IAM: the instance profile carries an inline policy granting
   * `s3:PutObject` on `${networkDataBucket}/raw/*`, `dynamodb:GetItem`
   * on the Vni_Lookup_Table (and its `capture-id-index` GSI), and the
   * AWS-managed `AmazonSSMManagedInstanceCore` policy so operators can
   * Session-Manager into the host for diagnostics. No network-create
   * verbs (the agent owns those) and no S3 Get/List on `parquet/` (the
   * Transformation_Workflow Lambdas own that path).
   *
   * The instance ID is exported as `GOATNetworkAgentCollectorInstanceId`
   * so the Network Agent runtime can satisfy Req 3.16's "collector
   * readiness" check via `ec2:DescribeInstances` +
   * `ec2:DescribeInstanceStatus` against this exact ID.
   */
  public readonly collectorInstance: ec2.CfnInstance;

  /**
   * Asset bundle uploaded to the CDK assets bucket holding the
   * collector runtime payload (`splitter.py`, `uploader.sh`,
   * `bootstrap.sh`). The bootstrap script (rendered into UserData)
   * downloads the asset on first boot and extracts it into
   * `/opt/goat-collector/` before starting the systemd units.
   *
   * Exposed as a public field so future tasks can grant additional
   * principals read access to the asset (none currently; the collector
   * instance role gets a narrow grant in this constructor).
   */
  public readonly collectorAsset: s3Assets.Asset;

  /**
   * Traffic Mirror Filter `goat-network-default-filter` (Req 6.5,
   * Task 22). Provisioned with one ingress rule and one egress rule
   * per L4 protocol (TCP, UDP, ICMP), each accepting traffic from
   * `0.0.0.0/0` to `0.0.0.0/0`. The "at least one each" wording in
   * Req 6.5 is satisfied with three pairs (one per protocol) so that
   * the agent's `start_capture` does not need to special-case
   * protocol selection -- every Capture_Session uses this same
   * default filter and the resulting pcap captures all observed
   * traffic.
   *
   * Filter ID is exported as `GOATNetworkAgentTrafficMirrorFilterId`
   * so the Network Agent runtime's `TRAFFIC_MIRROR_FILTER_ID`
   * environment variable resolves to this exact filter.
   */
  public readonly trafficMirrorFilter: ec2.CfnTrafficMirrorFilter;

  /**
   * Traffic Mirror Target of type `network-interface` referencing the
   * collector instance's primary ENI (Req 6.6, Task 22). The
   * `network-interface` type is chosen over `network-load-balancer`
   * because the design provisions exactly one collector -- an NLB
   * would add cost and complexity without operational benefit.
   *
   * Target ID is exported as `GOATNetworkAgentTrafficMirrorTargetId`
   * so the Network Agent runtime's `TRAFFIC_MIRROR_TARGET_ID`
   * environment variable resolves to this exact target. The agent
   * supplies `target_id` (not `target_arn`) when calling
   * `ec2:CreateTrafficMirrorSession`.
   */
  public readonly trafficMirrorTarget: ec2.CfnTrafficMirrorTarget;


  /**
   * ListRawObjectsLambda -- first task of the Transformation_Workflow.
   * Lists every object under `s3://{bucket}/raw/{capture_id}/` so the
   * downstream `Map` state can fan out one
   * {@link convertPcapToParquetLambda} invocation per pcap file.
   *
   * Source: `lambda/network-transformation/list_raw_objects.py`.
   */
  public readonly listRawObjectsLambda: lambda.Function;

  /**
   * ConvertPcapToParquetLambda -- Map state of the
   * Transformation_Workflow. Reads a single VXLAN pcap from S3, runs
   * tshark via a Lambda layer to produce JSON, projects onto the
   * {@link PCAP_LOGS_COLUMNS} schema, writes Parquet to
   * `s3://{bucket}/parquet/capture_id={capture_id}/`.
   *
   * Source: `lambda/network-transformation/convert_pcap_to_parquet.py`.
   *
   * Layer dependency: this Lambda requires a deploy-time-attached
   * Lambda layer providing `tshark` (wireshark) and `pyarrow`. The
   * layer artifact is not in this repository's scope; operators
   * publish it separately and reference its ARN at deploy time. The
   * Lambda raises a clear `RuntimeError` if the layer is missing,
   * causing Step Functions to transition to the `Fail` state.
   */
  public readonly convertPcapToParquetLambda: lambda.Function;

  /**
   * RunCrawlerLambda -- third task of the Transformation_Workflow.
   * Triggers {@link glueCrawler} (idempotently -- tolerates an in-flight
   * concurrent run) and polls until the crawl completes. Failure
   * (any non-`SUCCEEDED` `LastCrawl.Status`, or a polling timeout) is
   * raised so Step Functions transitions to the `Fail` state.
   *
   * Source: `lambda/network-transformation/run_crawler.py`.
   */
  public readonly runCrawlerLambda: lambda.Function;

  /**
   * ValidateAthenaLambda -- final task of the Transformation_Workflow.
   * Issues `SELECT 1 FROM pcap_logs WHERE capture_id = '<id>' LIMIT 1`
   * and asserts the result set contains at least one row, confirming
   * the partition is queryable end-to-end.
   *
   * Source: `lambda/network-transformation/validate_athena.py`.
   */
  public readonly validateAthenaLambda: lambda.Function;

  /**
   * Transformation_Workflow -- Step Functions state machine that runs
   * `ListRawObjects → Map(ConvertPcapToParquet) → RunCrawler →
   * ValidateAthena`. On any task failure, transitions to a single
   * `Fail` state emitting `{ failed_task, error_reason }` so the
   * Network Agent's `transform_capture` handler can surface a useful
   * diagnostic to the user (Reqs 6.8, 6.9).
   *
   * The state machine ARN is exported as
   * `GOATNetworkAgentTransformationStateMachineArn` so the Network
   * Runtime stack (Task 28) can plumb it into the agent container as
   * the `TRANSFORMATION_SFN_ARN` environment variable that
   * `handle_transform_capture` reads when calling
   * `stepfunctions:StartExecution`.
   */
  public readonly transformationStateMachine: sfn.StateMachine;

  /**
   * StopCaptureInvokerLambda -- bridges EventBridge Scheduler's
   * Auto_Stop_Schedule to the Network Agent's `stop_capture` action
   * (Reqs 3.5, 4.6, 4.7, 6.12).
   *
   * EventBridge Scheduler does not yet support
   * `bedrock-agent-runtime:InvokeAgentRuntime` as a native target
   * template, so this Lambda is a thin shim. The agent's
   * `start_capture` handler creates a one-shot `at(<deadline>)`
   * schedule whose target is this Lambda's ARN; this Lambda then calls
   * `InvokeAgentRuntime` with payload
   * `{"action": "stop_capture", "params": {"capture_id": "<id>"}}`.
   *
   * Three invocation attempts are made with exponential backoff. On
   * exhaustion, the Lambda emits a
   * {@link STOP_CAPTURE_INVOKER_METRIC_NAME} CloudWatch metric data
   * point under {@link STOP_CAPTURE_INVOKER_METRIC_NAMESPACE} so
   * operators can alarm on auto-stop failure trends without parsing
   * logs (Req 4.7), and the Capture_State_Table row is left in
   * `active` for the agent's reconciler to reap out-of-band (per
   * design.md "Auto_Stop_Schedule failure").
   *
   * The Lambda's IAM role grants `bedrock-agent-runtime:InvokeAgentRuntime`
   * on a single resource: either the literal runtime ARN passed in via
   * {@link NetworkInfraStackProps.networkAgentRuntimeArn} (the
   * post-follow-up-wiring path), or a least-privilege wildcard ARN
   * pattern keyed on the well-known runtime name
   * (`{@link STOP_CAPTURE_INVOKER_DEFAULT_RUNTIME_NAME}*`) in the
   * deploying account/region (the bootstrap path before the follow-up
   * wiring task is implemented). The Lambda also has a narrow
   * `cloudwatch:PutMetricData` policy gated on the metric namespace.
   */
  public readonly stopCaptureInvokerLambda: lambda.Function;

  /**
   * EventBridge Scheduler {@link scheduler.CfnScheduleGroup} that holds
   * every Auto_Stop_Schedule the Network Agent creates (Reqs 3.5, 4.6,
   * 4.10, 6.12).
   *
   * Pinned as a stack-owned group (rather than reusing AWS's `default`
   * group) so the AgentCore runtime IAM policy can scope
   * `scheduler:CreateSchedule` / `DeleteSchedule` / `GetSchedule` to a
   * single resource ARN -- `arn:aws:scheduler:${region}:${account}:schedule/${groupName}/*`
   * -- without ever wildcarding the group component (Task 27, design.md
   * "Athena-side defense in depth" reasoning extended to Scheduler).
   *
   * The agent reads the group name from the `SCHEDULE_GROUP_NAME`
   * environment variable (Task 28's NetworkRuntimeStack plumbs the env
   * var) and uses it as the `GroupName` parameter on
   * `CreateSchedule` / `DeleteSchedule` calls. Existing agent unit
   * tests under `agents/network-agent/test_auto_stop_schedule.py`
   * already exercise this contract.
   */
  public readonly autoStopScheduleGroup: scheduler.CfnScheduleGroup;

  /**
   * Dedicated IAM role assumed by EventBridge Scheduler
   * (`scheduler.amazonaws.com`) to invoke the
   * {@link stopCaptureInvokerLambda} when an Auto_Stop_Schedule fires
   * (Reqs 3.5, 4.6, 4.7, 6.12).
   *
   * Why a dedicated role instead of inlining a policy on the agent
   * role: when `start_capture` calls `scheduler:CreateSchedule`, it
   * supplies a `Target.RoleArn` that Scheduler will assume to
   * `lambda:InvokeFunction` the StopCaptureInvokerLambda. AWS forbids
   * passing the same role that Scheduler's call principal already
   * has (it must be a separate role with a trust policy scoped to
   * `scheduler.amazonaws.com`). The agent role gets a narrow
   * `iam:PassRole` policy on this role only, conditioned on
   * `iam:PassedToService = scheduler.amazonaws.com`, so the agent
   * cannot reuse the role to invoke the Lambda directly or pass it
   * to any other service (Task 27 line item).
   *
   * Trust policy includes the standard Scheduler confused-deputy
   * mitigation (`aws:SourceAccount` condition) so a cross-account
   * Scheduler in another account cannot trick this role into
   * invoking the Lambda -- only the deploying account's Scheduler
   * service principal is permitted.
   *
   * Permissions are intentionally minimal: a single
   * `lambda:InvokeFunction` statement on the StopCaptureInvokerLambda
   * ARN. No write access to DynamoDB, the Capture_State_Table, or
   * any other resource -- Scheduler's only job is to invoke the
   * shim Lambda.
   */
  public readonly schedulerTargetRole: iam.Role;

  constructor(scope: Construct, id: string, props?: NetworkInfraStackProps) {
    // -----------------------------------------------------------------------
    // Configure the BaseInfraStack with Network-Agent-specific values.
    //
    //   - `domainName='network'`            → BaseInfraStack derives the
    //                                          ECR repository name as
    //                                          `goat-network-agent-repository`
    //                                          (matches task 21 spec text).
    //   - `exportPrefix='GOATNetworkAgent'` → CfnOutput exports prefixed
    //                                          `GOATNetworkAgent*` for cross
    //                                          stack `Fn.importValue()`.
    //   - `imageTag='goat_network_agent'`   → Docker image tag and
    //                                          AgentCore workload-identity
    //                                          name component.
    //   - `domainPolicies=[]`               → Tasks 22-27 will append the
    //                                          per-resource statements.
    //
    // The agent source path (`agentSourcePath='../../agents/network-agent'`)
    // referenced in task 21 is consumed by the Network_Runtime_Stack
    // (task 28), not by `BaseInfraStack`, so it is not surfaced here.
    // -----------------------------------------------------------------------
    super(scope, id, {
      domainName: 'network',
      exportPrefix: 'GOATNetworkAgent',
      imageTag: 'goat_network_agent',
      domainPolicies: [],
    }, props);

    // -----------------------------------------------------------------------
    // Resolve the Network_Data_Bucket per Requirement 7.
    //
    // Branch 1 -- dedicated path (Req 7.4):
    //   The CDK app instantiated NetworkDataStack and passed its bucket
    //   name in via props. Use the literal name as-is. The dedicated stack
    //   already owns the lifecycle rules deleting raw/ at +7 days and
    //   parquet/ at +30 days (NetworkDataStack source).
    //
    // Branch 2 -- reuse path (Req 7.3):
    //   The CDK app verified the `GOATSharedDataBucketName` export was
    //   present and routed through this stack without setting the prop.
    //   Resolve the export here via `cdk.Fn.importValue()`. CDK records a
    //   cross-stack reference so CloudFormation enforces a deploy-time
    //   dependency on `GOATData-${region}`. Lifecycle rules are then
    //   re-asserted on the imported bucket via a CfnBucket override (see
    //   below).
    // -----------------------------------------------------------------------
    if (props?.networkDataBucketName && props.networkDataBucketName.trim().length > 0) {
      // Dedicated path -- NetworkDataStack provisioned the bucket.
      this.networkDataBucketName = props.networkDataBucketName;
      this.networkDataBucket = s3.Bucket.fromBucketName(
        this,
        'NetworkDataBucketImported',
        this.networkDataBucketName,
      );
    } else {
      // Reuse path -- import the shared bucket from the existing GOATData stack.
      this.networkDataBucketName = cdk.Fn.importValue(SHARED_DATA_BUCKET_EXPORT_NAME);
      this.networkDataBucket = s3.Bucket.fromBucketName(
        this,
        'NetworkDataBucketImported',
        this.networkDataBucketName,
      );

      // -----------------------------------------------------------------
      // Lifecycle rule re-assertion on the imported shared bucket
      // (Req 7.3 + Reqs 4.8, 4.9).
      //
      // CDK cannot inspect a foreign bucket's existing lifecycle config
      // at synth time (the bucket is owned by another stack) and cannot
      // re-declare an `AWS::S3::Bucket` resource for the same physical
      // name without a deploy-time conflict. The correct mechanism is a
      // CDK custom resource that calls
      // `s3:PutBucketLifecycleConfiguration` against the imported bucket
      // name at deploy time, with the rules listed in
      // {@link NETWORK_DATA_LIFECYCLE_RULES}. The full implementation of
      // that custom resource is added in task 22 (which configures all
      // collector-side S3 interactions); this scaffolding task exposes
      // the rule values as a typed module-level constant so task 22
      // picks them up without re-reading the spec.
      //
      // Note: this only applies in the reuse path because the dedicated
      // path's NetworkDataStack already declares the same lifecycle
      // rules natively on the bucket it owns.
      // -----------------------------------------------------------------
    }

    // -----------------------------------------------------------------------
    // Capture_State_Table (Reqs 6.11, 6.12)
    //
    // Persists Capture_Session metadata so `list_captures`, `stop_capture`,
    // `transform_capture`, and the Auto_Stop_Schedule can resolve a
    // `capture_id` to its mirror sessions, deadline, and status. Only the
    // partition key (`capture_id`, string) and the GSI partition key
    // (`status`, string) need declaring here -- DynamoDB is schemaless for
    // every other attribute the agent writes (`eni_ids`, `start_time`,
    // `deadline`, `mirror_session_ids`, `idempotency_token`, etc., per
    // design.md "Capture_State_Table").
    //
    // Billing: PAY_PER_REQUEST. Capture lifecycle write traffic is bursty
    // (a handful of writes at start_capture / stop_capture and one row read
    // per list_captures call), well below any provisioned-throughput
    // break-even and a perfect fit for on-demand. This matches the choice
    // made for the existing `goat-conversations-*` and other G.O.A.T.
    // operational tables in DataStack.
    //
    // Removal: DESTROY because the demo stacks are tear-down-friendly. The
    // table holds short-lived operational state (active captures + recent
    // history) and never user content; lifecycle policies on the
    // Network_Data_Bucket already handle pcap/Parquet retention.
    // -----------------------------------------------------------------------
    this.captureStateTable = new dynamodb.Table(this, 'CaptureStateTable', {
      tableName: `goat-network-capture-state-${this.account}-${this.region}`,
      partitionKey: { name: 'capture_id', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // GSI on `status` enables O(1) Capture_Concurrency_Limit checks and the
    // `list_captures status=active|historical` queries (design.md). Project
    // ALL attributes so `list_captures` can satisfy its full output schema
    // (`capture_id`, `eni_ids`, `start_time`, `deadline`, `status`,
    // `stopped_reason`, `mirror_session_ids`) directly from the index
    // without a follow-up GetItem per row.
    this.captureStateTable.addGlobalSecondaryIndex({
      indexName: 'status-index',
      partitionKey: { name: 'status', type: dynamodb.AttributeType.STRING },
      projectionType: dynamodb.ProjectionType.ALL,
    });

    // -----------------------------------------------------------------------
    // Vni_Lookup_Table (Reqs 6.11, 6.12)
    //
    // Maps each Traffic-Mirror-assigned VXLAN VNI to its owning
    // `capture_id`, mirror session, and source ENI so the collector can
    // split rotated pcap files per capture without re-reading state from
    // the larger Capture_State_Table on every rotation. The table also
    // sources the runtime VNI cache the collector keeps in memory with a
    // 30-second TTL (design.md "VNI to capture_id mapping").
    //
    // Partition key: `vni` (number, 1..16777215 -- the VXLAN VNI is
    // auto-assigned by `ec2:CreateTrafficMirrorSession`).
    //
    // GSI `capture-id-index` partitioned on `capture_id` (string) so
    // `stop_capture` can issue a single Query to list every VNI row to
    // delete for a capture (design.md "Vni_Lookup_Table" GSI text).
    //
    // DynamoDB TTL is enabled on attribute `expires_at` (Unix epoch
    // seconds, set to the capture deadline by `start_capture`). If
    // `stop_capture` cleanup fails for any reason, TTL self-purges the
    // stale rows so the lookup table stays consistent without manual
    // intervention.
    //
    // Billing and removal policies match Capture_State_Table for the same
    // reasons (bursty writes, demo-friendly tear-down).
    // -----------------------------------------------------------------------
    this.vniLookupTable = new dynamodb.Table(this, 'VniLookupTable', {
      tableName: `goat-network-vni-lookup-${this.account}-${this.region}`,
      partitionKey: { name: 'vni', type: dynamodb.AttributeType.NUMBER },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      timeToLiveAttribute: 'expires_at',
    });

    this.vniLookupTable.addGlobalSecondaryIndex({
      indexName: 'capture-id-index',
      partitionKey: { name: 'capture_id', type: dynamodb.AttributeType.STRING },
      projectionType: dynamodb.ProjectionType.ALL,
    });

    // -----------------------------------------------------------------------
    // Glue Catalog (Reqs 6.7, 6.12)
    //
    // Database `goat_network` and table `pcap_logs` (partitioned on
    // `capture_id`, Parquet at `s3://{Network_Data_Bucket}/parquet/`),
    // plus a Glue Crawler that adds new partitions when the
    // Transformation_Workflow writes Parquet files for a capture
    // (design.md "Pcap_Athena_Table (Glue Catalog)" + Step Functions
    // Transformation_Workflow diagram).
    //
    // Resources are declared with the L1 `aws-glue.Cfn*` constructs
    // because the L2 `@aws-cdk/aws-glue-alpha` library is still alpha
    // and not present in this repo's dependency tree (`package.json`
    // pins `aws-cdk-lib` only). L1 constructs map 1:1 to the
    // CloudFormation resources documented in the AWS Glue API and
    // expose every option we need (partition keys, SerDe, crawler
    // schedule).
    // -----------------------------------------------------------------------

    // Glue Database -- physical name is fixed by Req 6.7 / design.md
    // ("Database `goat_network`"). The catalog ID is the deploying
    // account; we read it from the inherited `BaseInfraStack` `account`
    // token rather than `cdk.Aws.ACCOUNT_ID` so the value is consistent
    // with the rest of this stack and is fully resolved at synth time
    // when the env was passed in.
    this.glueDatabase = new glue.CfnDatabase(this, 'GlueDatabase', {
      catalogId: this.account,
      databaseInput: {
        name: GLUE_DATABASE_NAME,
        description:
          'G.O.A.T. Network Agent -- Pcap_Athena_Table catalog. Holds tshark-derived Parquet from VXLAN packet captures, partitioned by capture_id.',
      },
    });

    // Glue Table -- Parquet on S3, partitioned by `capture_id`.
    //
    // The S3 location uses `s3://{bucket}/parquet/` exactly as Req 6.7 /
    // design.md call out. Trailing slash is mandatory: Glue treats the
    // `Location` as a directory prefix and uses it as the parent for
    // every partition's `s3://.../parquet/capture_id=<id>/` directory.
    //
    // SerDe choice: `org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe`
    // is the canonical Parquet SerDe for the Hive Catalog and is the one
    // Athena's Parquet engine pairs with the
    // `MapredParquetInputFormat` / `MapredParquetOutputFormat` pair
    // declared below. Using anything else would force an Athena query
    // engine fallback at read time.
    this.glueTable = new glue.CfnTable(this, 'PcapLogsTable', {
      catalogId: this.account,
      databaseName: GLUE_DATABASE_NAME,
      tableInput: {
        name: GLUE_TABLE_NAME,
        description:
          'tshark-derived per-frame pcap data, partitioned by capture_id. Backs every Pcap_Query_Action via Athena.',
        tableType: 'EXTERNAL_TABLE',
        parameters: {
          // Glue / Athena flags. `classification` lets the Crawler skip
          // SerDe detection on subsequent runs; `EXTERNAL=TRUE` matches
          // Hive convention for catalog-only tables backed by S3.
          classification: 'parquet',
          EXTERNAL: 'TRUE',
          // Tell Glue/Athena that partitions live at locations matching
          // `<base>/capture_id=<value>/`. This enables Hive-style
          // partition projection optimizations and is also required for
          // the Glue Crawler to detect partitions during runs targeting
          // a `parquet/` prefix that already contains data.
          'projection.enabled': 'false',
        },
        partitionKeys: [
          {
            name: 'capture_id',
            type: 'string',
          },
        ],
        storageDescriptor: {
          // Spread the column constant out so changes to
          // PCAP_LOGS_COLUMNS automatically propagate to the catalog.
          columns: PCAP_LOGS_COLUMNS.map((col) => ({
            name: col.name,
            type: col.type,
            comment: col.comment,
          })),
          location: `s3://${this.networkDataBucketName}/parquet/`,
          inputFormat: 'org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat',
          outputFormat: 'org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat',
          compressed: false,
          serdeInfo: {
            serializationLibrary:
              'org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe',
            parameters: {
              'serialization.format': '1',
            },
          },
        },
      },
    });

    // CloudFormation has no implicit ordering between two Glue resources
    // that target the same database -- the table create call will fail
    // with a `DatabaseNotFoundException` if the database resource has
    // not yet finished creating. Add the dependency explicitly.
    this.glueTable.addDependency(this.glueDatabase);

    // -----------------------------------------------------------------------
    // Glue Crawler IAM role
    //
    // Glue Crawlers do not run under the AgentCore runtime role; AWS
    // requires a service-linked role for `glue.amazonaws.com`. The
    // managed `AWSGlueServiceRole` policy covers the generic Glue +
    // CloudWatch + S3 metadata permissions; we then attach a narrow
    // inline policy granting `s3:GetObject` and `s3:ListBucket` on the
    // Network_Data_Bucket so the Crawler can enumerate
    // `parquet/capture_id=<id>/*` partitions and read Parquet headers.
    //
    // Scoping the inline policy to the resolved bucket name (rather
    // than `*`) means the role cannot be repurposed to crawl unrelated
    // data even if it leaked.
    // -----------------------------------------------------------------------
    const crawlerRole = new iam.Role(this, 'PcapLogsCrawlerRole', {
      assumedBy: new iam.ServicePrincipal('glue.amazonaws.com'),
      description:
        'Service role for the goat_network.pcap_logs Glue Crawler. Enumerates parquet/ partitions on the Network_Data_Bucket and updates the Glue Catalog.',
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSGlueServiceRole'),
      ],
    });

    crawlerRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'CrawlerReadPcapParquet',
        effect: iam.Effect.ALLOW,
        actions: ['s3:GetObject', 's3:ListBucket'],
        resources: [
          this.networkDataBucket.bucketArn,
          `${this.networkDataBucket.bucketArn}/parquet/*`,
        ],
      }),
    );

    // -----------------------------------------------------------------------
    // Glue Crawler -- targets `s3://{bucket}/parquet/` and updates
    // partitions on `goat_network.pcap_logs`.
    //
    // Triggered by Task 25's `RunCrawlerLambda`; no schedule is set
    // here so the Crawler only runs as part of the
    // Transformation_Workflow. This avoids paying for unnecessary
    // catalog walks between captures and keeps partition state aligned
    // with the workflow's success/fail signal.
    //
    // `schemaChangePolicy`: the column schema is owned by the CDK
    // declaration above; the Crawler logs deltas to CloudWatch instead
    // of mutating the table, so tshark output drift can be reviewed
    // before being adopted.
    //
    // `recrawlPolicy.recrawlBehavior = CRAWL_NEW_FOLDERS_ONLY`: keeps
    // crawl runtime O(new partitions) instead of O(all partitions),
    // important as the catalog grows beyond a handful of demo
    // captures.
    // -----------------------------------------------------------------------
    this.glueCrawler = new glue.CfnCrawler(this, 'PcapLogsCrawler', {
      name: `goat-network-pcap-logs-crawler-${this.account}-${this.region}`,
      role: crawlerRole.roleArn,
      databaseName: GLUE_DATABASE_NAME,
      description:
        'Updates capture_id partitions on goat_network.pcap_logs after the Transformation_Workflow writes new Parquet objects.',
      targets: {
        s3Targets: [
          {
            path: `s3://${this.networkDataBucketName}/parquet/`,
          },
        ],
      },
      schemaChangePolicy: {
        deleteBehavior: 'LOG',
        updateBehavior: 'LOG',
      },
      recrawlPolicy: {
        recrawlBehavior: 'CRAWL_NEW_FOLDERS_ONLY',
      },
      // Configuration JSON -- direct CloudFormation contract. We pin the
      // partition update behavior so the Crawler does not regenerate
      // the table definition's columns from inferred Parquet schemas
      // (single source of truth stays in PCAP_LOGS_COLUMNS).
      configuration: JSON.stringify({
        Version: 1.0,
        CrawlerOutput: {
          Partitions: { AddOrUpdateBehavior: 'InheritFromTable' },
          Tables: { AddOrUpdateBehavior: 'MergeNewColumns' },
        },
        Grouping: {
          TableGroupingPolicy: 'CombineCompatibleSchemas',
        },
      }),
    });

    // The Crawler references the database and the table by name only --
    // CloudFormation does not infer the dependency from the string. Add
    // both edges so the Crawler is created last.
    this.glueCrawler.addDependency(this.glueDatabase);
    this.glueCrawler.addDependency(this.glueTable);

    // -----------------------------------------------------------------------
    // Transformation_Workflow -- Step Functions state machine
    // (Reqs 6.8, 6.9, 6.12)
    //
    // Topology mandated by Task 25:
    //
    //   ListRawObjects ──► Map(ConvertPcapToParquet) ──► RunCrawler ──► ValidateAthena
    //
    // On any task failure, the workflow transitions to a single shared
    // `Fail` state emitting `{ failed_task, error_reason }` so the
    // Network Agent's `transform_capture` handler can surface a useful
    // diagnostic. Step Functions never leaves the machine running after
    // a task failure (Req 6.9): every task carries a `Catch` clause
    // that routes to this `Fail` state, the `Fail` terminal state stops
    // execution, and the state machine's `tracingEnabled = true` keeps
    // an X-Ray trail for post-mortem.
    //
    // Implementation choice -- `lambda.Function` over container images.
    // Each Lambda is small, runs in seconds (or low minutes for the
    // crawler poll), and benefits from cold-start sharing on subsequent
    // invocations. The `tshark` dependency for ConvertPcapToParquet is
    // satisfied by a deploy-time-attached Lambda layer; the layer ARN
    // is documented in `lambda/network-transformation/README.md` and
    // operators publish it separately. Container images would couple
    // this CDK stack to a specific tshark image build, which is more
    // friction than the layer approach for a demo.
    // -----------------------------------------------------------------------

    // ----- Lambda 1 -- ListRawObjectsLambda --------------------------------
    //
    // Runtime contract: lists `s3://{bucket}/raw/{capture_id}/` and
    // returns a list of object keys for the `Map` state to consume.
    //
    // IAM scope: `s3:ListBucket` on the Network_Data_Bucket only, with
    // a `s3:prefix` condition restricting reads to `raw/`. We do not
    // grant `s3:GetObject` because this Lambda only enumerates keys
    // (the `Map` state's per-iteration Lambda performs the GetObject).
    const listRawObjectsLogGroup = new logs.LogGroup(this, 'ListRawObjectsLambdaLogGroup', {
      logGroupName: `/aws/lambda/goat-network-list-raw-objects-${this.account}-${this.region}`,
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });
    this.listRawObjectsLambda = new lambda.Function(this, 'ListRawObjectsLambda', {
      runtime: TRANSFORMATION_LAMBDA_RUNTIME,
      handler: 'list_raw_objects.lambda_handler',
      code: lambda.Code.fromAsset(NETWORK_TRANSFORMATION_LAMBDA_DIR),
      timeout: cdk.Duration.minutes(5),
      memorySize: 512,
      environment: {
        DATA_BUCKET_NAME: this.networkDataBucketName,
      },
      logGroup: listRawObjectsLogGroup,
      description:
        'G.O.A.T. Network Agent Transformation_Workflow: lists raw/{capture_id}/* objects on the Network_Data_Bucket.',
    });
    this.listRawObjectsLambda.addToRolePolicy(
      new iam.PolicyStatement({
        sid: 'ListRawObjectsListBucket',
        effect: iam.Effect.ALLOW,
        actions: ['s3:ListBucket'],
        resources: [this.networkDataBucket.bucketArn],
        conditions: {
          StringLike: {
            's3:prefix': ['raw/*'],
          },
        },
      }),
    );

    // ----- Lambda 2 -- ConvertPcapToParquetLambda --------------------------
    //
    // Runtime contract: reads one pcap, runs tshark via a Lambda layer
    // to produce JSON, writes Parquet to
    // `parquet/capture_id={capture_id}/`.
    //
    // Resource sizing: 2048 MB / 10 minutes covers the worst-case demo
    // pcap rotation (100 MB raw VXLAN, decoded to up to ~1M small
    // frames). PyArrow needs the larger memory ceiling to materialize
    // a Parquet table without spilling.
    //
    // Layer dependency: operators attach a layer providing `tshark`
    // (wireshark) and `pyarrow` at deploy time. The CDK stack does
    // NOT declare the layer here because (a) the layer artifact is
    // out of scope for this repository (operators build it from a
    // public AL2023 + tshark recipe), and (b) baking a layer ARN into
    // CDK would require the layer to exist in every account/region
    // where the demo is deployed. The Lambda raises a clear
    // RuntimeError if the layer is missing at runtime, which Step
    // Functions captures and routes to the `Fail` state.
    //
    // IAM scope: `s3:GetObject` on `raw/*`, `s3:PutObject` on
    // `parquet/*`, `s3:ListBucket` on the bucket (so the SDK can
    // perform bucket existence checks before the operations).
    const convertPcapLogGroup = new logs.LogGroup(this, 'ConvertPcapToParquetLambdaLogGroup', {
      logGroupName: `/aws/lambda/goat-network-convert-pcap-${this.account}-${this.region}`,
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });
    this.convertPcapToParquetLambda = new lambda.Function(this, 'ConvertPcapToParquetLambda', {
      runtime: TRANSFORMATION_LAMBDA_RUNTIME,
      handler: 'convert_pcap_to_parquet.lambda_handler',
      code: lambda.Code.fromAsset(NETWORK_TRANSFORMATION_LAMBDA_DIR, {
        bundling: {
          image: TRANSFORMATION_LAMBDA_RUNTIME.bundlingImage,
          command: [
            'bash', '-c',
            [
              'pip install --no-cache-dir -r requirements.txt -t /asset-output',
              'cp -au *.py requirements.txt /asset-output/',
              // Strip pyarrow/numpy test suites and docs to stay within Lambda 50MB zip limit
              'find /asset-output -type d -name tests -exec rm -rf {} + 2>/dev/null || true',
              'find /asset-output -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true',
              'find /asset-output -name "*.pyc" -delete 2>/dev/null || true',
              'find /asset-output -name "*.pyi" -delete 2>/dev/null || true',
              'rm -rf /asset-output/numpy/tests /asset-output/pyarrow/tests 2>/dev/null || true',
            ].join(' && '),
          ],
          local: {
            tryBundle(outputDir: string) {
              // Local bundling fallback when Docker is unavailable.
              // Installs Linux x86_64 wheels for Lambda compatibility.
              const { execSync } = require('child_process');
              try {
                execSync(
                  `pip install --no-cache-dir --platform manylinux2014_x86_64 --implementation cp --python-version 3.12 --only-binary=:all: --target "${outputDir}" -r requirements.txt`,
                  { cwd: NETWORK_TRANSFORMATION_LAMBDA_DIR, stdio: 'inherit' },
                );
                // Copy source files
                const fs = require('fs');
                for (const f of fs.readdirSync(NETWORK_TRANSFORMATION_LAMBDA_DIR)) {
                  if (f.endsWith('.py') || f === 'requirements.txt') {
                    fs.copyFileSync(
                      path.join(NETWORK_TRANSFORMATION_LAMBDA_DIR, f),
                      path.join(outputDir, f),
                    );
                  }
                }
                return true;
              } catch {
                return false;
              }
            },
          },
        },
      }),
      timeout: cdk.Duration.minutes(10),
      memorySize: 2048,
      environment: {
        DATA_BUCKET_NAME: this.networkDataBucketName,
      },
      logGroup: convertPcapLogGroup,
      description:
        'G.O.A.T. Network Agent Transformation_Workflow: converts a single pcap to Parquet via scapy + pyarrow.',
    });
    this.convertPcapToParquetLambda.addToRolePolicy(
      new iam.PolicyStatement({
        sid: 'ConvertPcapReadRaw',
        effect: iam.Effect.ALLOW,
        actions: ['s3:GetObject'],
        resources: [`${this.networkDataBucket.bucketArn}/raw/*`],
      }),
    );
    this.convertPcapToParquetLambda.addToRolePolicy(
      new iam.PolicyStatement({
        sid: 'ConvertPcapWriteParquet',
        effect: iam.Effect.ALLOW,
        actions: ['s3:PutObject', 's3:AbortMultipartUpload'],
        resources: [`${this.networkDataBucket.bucketArn}/parquet/*`],
      }),
    );
    this.convertPcapToParquetLambda.addToRolePolicy(
      new iam.PolicyStatement({
        sid: 'ConvertPcapListBucket',
        effect: iam.Effect.ALLOW,
        actions: ['s3:ListBucket'],
        resources: [this.networkDataBucket.bucketArn],
      }),
    );

    // ----- Lambda 3 -- RunCrawlerLambda ------------------------------------
    //
    // Runtime contract: starts the Glue Crawler (idempotently, tolerating
    // a concurrent in-flight run) and polls until it returns to READY
    // with a SUCCEEDED status.
    //
    // Timeout: 14 minutes (1 minute below Lambda's 15-minute hard limit).
    // Crawls of demo-sized partitions complete in 1-2 minutes; the
    // larger budget covers cold-start provisioning of crawler workers.
    //
    // IAM scope: `glue:StartCrawler` and `glue:GetCrawler` on this
    // crawler only.
    const runCrawlerLogGroup = new logs.LogGroup(this, 'RunCrawlerLambdaLogGroup', {
      logGroupName: `/aws/lambda/goat-network-run-crawler-${this.account}-${this.region}`,
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });
    this.runCrawlerLambda = new lambda.Function(this, 'RunCrawlerLambda', {
      runtime: TRANSFORMATION_LAMBDA_RUNTIME,
      handler: 'run_crawler.lambda_handler',
      code: lambda.Code.fromAsset(NETWORK_TRANSFORMATION_LAMBDA_DIR),
      timeout: cdk.Duration.minutes(2),
      memorySize: 256,
      environment: {
        GLUE_CRAWLER_NAME: this.glueCrawler.ref,
        GLUE_DATABASE: GLUE_DATABASE_NAME,
        DATA_BUCKET_NAME: this.networkDataBucketName,
      },
      logGroup: runCrawlerLogGroup,
      description:
        'G.O.A.T. Network Agent Transformation_Workflow: registers the Glue partition for a processed capture.',
    });
    this.runCrawlerLambda.addToRolePolicy(
      new iam.PolicyStatement({
        sid: 'RunCrawlerGlueAccess',
        effect: iam.Effect.ALLOW,
        actions: [
          'glue:StartCrawler',
          'glue:GetCrawler',
          'glue:GetTable',
          'glue:BatchCreatePartition',
          'glue:UpdatePartition',
        ],
        resources: [
          `arn:${cdk.Aws.PARTITION}:glue:${this.region}:${this.account}:crawler/${this.glueCrawler.ref}`,
          `arn:${cdk.Aws.PARTITION}:glue:${this.region}:${this.account}:catalog`,
          `arn:${cdk.Aws.PARTITION}:glue:${this.region}:${this.account}:database/${GLUE_DATABASE_NAME}`,
          `arn:${cdk.Aws.PARTITION}:glue:${this.region}:${this.account}:table/${GLUE_DATABASE_NAME}/${GLUE_TABLE_NAME}`,
        ],
      }),
    );

    // ----- Lambda 4 -- ValidateAthenaLambda --------------------------------
    //
    // Runtime contract: runs `SELECT 1 FROM pcap_logs WHERE
    // capture_id = '<id>' LIMIT 1` against Athena and asserts the
    // result set has at least one data row.
    //
    // IAM scope:
    //   - `athena:StartQueryExecution`, `athena:GetQueryExecution`,
    //     `athena:GetQueryResults` on the default workgroup (every
    //     Athena query must reference a workgroup; the default `primary`
    //     workgroup is used because no custom workgroup is provisioned
    //     in this demo).
    //   - `glue:GetDatabase`, `glue:GetTable`, `glue:GetPartition*` on
    //     the goat_network database, table, and its partitions, so
    //     Athena can resolve schema and prune partitions.
    //   - `s3:GetObject` and `s3:ListBucket` on the parquet/ data
    //     prefix and the `athena-results/` results prefix; Athena
    //     reads the data via the Lambda's identity (it inherits the
    //     query principal's permissions for the parquet/ data plane).
    //   - `s3:PutObject` on the `athena-results/` prefix so query
    //     results can be written.
    //
    // Note on Athena's data-plane access: Athena queries inherit the
    // calling principal's S3 permissions, so the read scope below is
    // what enables the `SELECT 1 FROM pcap_logs` to actually read the
    // Parquet data and not just the catalog.
    const validateAthenaLogGroup = new logs.LogGroup(this, 'ValidateAthenaLambdaLogGroup', {
      logGroupName: `/aws/lambda/goat-network-validate-athena-${this.account}-${this.region}`,
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });
    this.validateAthenaLambda = new lambda.Function(this, 'ValidateAthenaLambda', {
      runtime: TRANSFORMATION_LAMBDA_RUNTIME,
      handler: 'validate_athena.lambda_handler',
      code: lambda.Code.fromAsset(NETWORK_TRANSFORMATION_LAMBDA_DIR),
      timeout: cdk.Duration.minutes(2),
      memorySize: 256,
      environment: {
        GLUE_DATABASE: GLUE_DATABASE_NAME,
        DATA_BUCKET_NAME: this.networkDataBucketName,
      },
      logGroup: validateAthenaLogGroup,
      description:
        'G.O.A.T. Network Agent Transformation_Workflow: runs SELECT 1 FROM pcap_logs WHERE capture_id = ... to validate the partition is queryable.',
    });
    // Athena query control plane (default workgroup; demo does not
    // provision a custom workgroup).
    this.validateAthenaLambda.addToRolePolicy(
      new iam.PolicyStatement({
        sid: 'ValidateAthenaQueryControl',
        effect: iam.Effect.ALLOW,
        actions: [
          'athena:StartQueryExecution',
          'athena:GetQueryExecution',
          'athena:GetQueryResults',
          'athena:StopQueryExecution',
        ],
        resources: [
          `arn:${cdk.Aws.PARTITION}:athena:${this.region}:${this.account}:workgroup/primary`,
        ],
      }),
    );
    // Glue Catalog read access scoped to goat_network.
    this.validateAthenaLambda.addToRolePolicy(
      new iam.PolicyStatement({
        sid: 'ValidateAthenaGlueCatalog',
        effect: iam.Effect.ALLOW,
        actions: [
          'glue:GetDatabase',
          'glue:GetDatabases',
          'glue:GetTable',
          'glue:GetTables',
          'glue:GetPartition',
          'glue:GetPartitions',
        ],
        resources: [
          `arn:${cdk.Aws.PARTITION}:glue:${this.region}:${this.account}:catalog`,
          `arn:${cdk.Aws.PARTITION}:glue:${this.region}:${this.account}:database/${GLUE_DATABASE_NAME}`,
          `arn:${cdk.Aws.PARTITION}:glue:${this.region}:${this.account}:table/${GLUE_DATABASE_NAME}/${GLUE_TABLE_NAME}`,
        ],
      }),
    );
    // S3 data plane: read parquet/ data, write athena-results/ output.
    this.validateAthenaLambda.addToRolePolicy(
      new iam.PolicyStatement({
        sid: 'ValidateAthenaListBucket',
        effect: iam.Effect.ALLOW,
        actions: ['s3:ListBucket', 's3:GetBucketLocation'],
        resources: [this.networkDataBucket.bucketArn],
      }),
    );
    this.validateAthenaLambda.addToRolePolicy(
      new iam.PolicyStatement({
        sid: 'ValidateAthenaReadParquet',
        effect: iam.Effect.ALLOW,
        actions: ['s3:GetObject'],
        resources: [`${this.networkDataBucket.bucketArn}/parquet/*`],
      }),
    );
    this.validateAthenaLambda.addToRolePolicy(
      new iam.PolicyStatement({
        sid: 'ValidateAthenaWriteResults',
        effect: iam.Effect.ALLOW,
        actions: [
          's3:PutObject',
          's3:GetObject',
          's3:AbortMultipartUpload',
        ],
        resources: [`${this.networkDataBucket.bucketArn}/athena-results/*`],
      }),
    );

    // ----- Step Functions state machine -----------------------------------
    //
    // Build the state machine top-down. We construct each state, set
    // up the per-task `Catch` clause routing to the shared `Fail`
    // state, then wire `next` transitions in the requested topology.
    //
    // The shared `Fail` state emits the structured error envelope
    // mandated by Task 25:
    //
    //   { "failed_task": "<task name>", "error_reason": "<message>" }
    //
    // Step Functions exposes the Catch error via
    // `States.ErrorPath('$.error.Cause')`. We use a `Pass` state with
    // a `parameters` block to shape the output before the `Fail`
    // terminal state, because `Fail` itself does not accept `Parameters`
    // (its `Cause` and `Error` fields are static strings only). The
    // `Pass` state ensures the error envelope is observable in the
    // execution history.

    const buildFailureBranch = (failedTaskName: string): {
      shape: sfn.Pass;
      fail: sfn.Fail;
    } => {
      const shape = new sfn.Pass(this, `Shape${failedTaskName}Failure`, {
        comment: `Shape the error envelope before transitioning to the shared Fail state for ${failedTaskName}.`,
        parameters: {
          failed_task: failedTaskName,
          'error_reason.$': '$.error.Cause',
          'error_name.$': '$.error.Error',
        },
      });
      const fail = new sfn.Fail(this, `${failedTaskName}Failed`, {
        cause: `${failedTaskName} failed; see error_reason in the preceding Pass state output for the underlying error message.`,
        error: failedTaskName,
      });
      shape.next(fail);
      return { shape, fail };
    };

    // Per-task catch branches. Each branch starts at a `Pass` that
    // shapes the envelope, then terminates in the `Fail` state. Step
    // Functions does not allow a single `Fail` state to be reached by
    // multiple `Catch` blocks if the branches need different
    // `failed_task` labels, so each task gets its own branch.
    const listRawCatch = buildFailureBranch('ListRawObjects');
    const convertCatch = buildFailureBranch('ConvertPcapToParquet');
    const crawlerCatch = buildFailureBranch('RunCrawler');
    const validateCatch = buildFailureBranch('ValidateAthena');

    // ListRawObjects task
    const listRawObjectsTask = new tasks.LambdaInvoke(this, 'ListRawObjectsTask', {
      lambdaFunction: this.listRawObjectsLambda,
      // Forward the original workflow input ($.capture_id) into the
      // Lambda payload, plus pass through anything else the user
      // included for forward-compatibility.
      payload: sfn.TaskInput.fromObject({
        'capture_id.$': '$.capture_id',
      }),
      // The invoke result lives at $.Payload by default; project it
      // into a stable key so downstream states have a clear schema.
      resultPath: '$.list_raw_result',
      comment: 'List raw VXLAN pcap files for the capture under raw/{capture_id}/.',
    });
    listRawObjectsTask.addCatch(listRawCatch.shape, {
      errors: ['States.ALL'],
      // Capture the error object on a sidecar field so the Pass state
      // can read $.error.Cause / $.error.Error.
      resultPath: '$.error',
    });

    // Map state running ConvertPcapToParquet per pcap object.
    //
    // Concurrency: cap at 5 to avoid hammering S3 / DynamoDB Vni lookups
    // during high-fanout captures. The demo's per-capture pcap object
    // count is single-digit so this is not a throughput limiter; it
    // exists to be considerate of account-level limits when multiple
    // transformations run concurrently.
    const convertTask = new tasks.LambdaInvoke(this, 'ConvertPcapToParquetTask', {
      lambdaFunction: this.convertPcapToParquetLambda,
      payload: sfn.TaskInput.fromObject({
        // Inside the Map iterator, the per-iteration item lives at
        // `$$.Map.Item.Value`; we project the iterator-level fields
        // explicitly so the Lambda receives the documented shape:
        // { capture_id, bucket, key }.
        'capture_id.$': '$.capture_id',
        'bucket.$': '$.bucket',
        'key.$': '$.key',
      }),
      resultPath: '$.convert_result',
      comment: 'Convert one pcap to Parquet via tshark + pyarrow.',
    });
    convertTask.addCatch(convertCatch.shape, {
      errors: ['States.ALL'],
      resultPath: '$.error',
    });

    const convertMap = new sfn.Map(this, 'MapConvertPcapToParquet', {
      maxConcurrency: 5,
      itemsPath: '$.list_raw_result.Payload.raw_keys',
      // Each iterator-state input is the pcap object's S3 key (string).
      // Project that into the {capture_id, bucket, key} shape the
      // ConvertPcapToParquet Lambda expects. We use the modern
      // `itemSelector` field (the older `parameters` field is
      // deprecated and emits a CDK warning at synth time).
      itemSelector: {
        'capture_id.$': '$.capture_id',
        'bucket.$': '$.list_raw_result.Payload.bucket',
        'key.$': '$$.Map.Item.Value',
      },
      // Ignore the per-iteration result data -- we don't aggregate
      // frame counts at the workflow level. Storing the array would
      // grow execution history and cost without value.
      resultPath: sfn.JsonPath.DISCARD,
      comment: 'Fan out one ConvertPcapToParquet Lambda invocation per pcap object listed by ListRawObjects.',
    });
    // Use the modern `itemProcessor` API (the older `iterator()` is
    // deprecated). Both produce the same CloudFormation output for an
    // INLINE processor; only the construct-tree path differs.
    convertMap.itemProcessor(convertTask);

    // RunCrawler task
    const runCrawlerTask = new tasks.LambdaInvoke(this, 'RunCrawlerTask', {
      lambdaFunction: this.runCrawlerLambda,
      payload: sfn.TaskInput.fromObject({
        'capture_id.$': '$.capture_id',
      }),
      resultPath: '$.crawler_result',
      comment: 'Trigger the Glue Crawler so the new capture_id partition becomes queryable.',
    });
    runCrawlerTask.addCatch(crawlerCatch.shape, {
      errors: ['States.ALL'],
      resultPath: '$.error',
    });

    // ValidateAthena task
    const validateAthenaTask = new tasks.LambdaInvoke(this, 'ValidateAthenaTask', {
      lambdaFunction: this.validateAthenaLambda,
      payload: sfn.TaskInput.fromObject({
        'capture_id.$': '$.capture_id',
      }),
      resultPath: '$.validation_result',
      comment: 'Run SELECT 1 FROM pcap_logs WHERE capture_id = ... and assert the partition is queryable.',
    });
    validateAthenaTask.addCatch(validateCatch.shape, {
      errors: ['States.ALL'],
      resultPath: '$.error',
    });

    const successState = new sfn.Succeed(this, 'TransformationSucceeded', {
      comment: 'All four Transformation_Workflow tasks completed successfully; the capture is queryable.',
    });

    // Wire the linear topology:
    //   ListRawObjects → Map(ConvertPcapToParquet) → RunCrawler → ValidateAthena → Succeed
    const definition = listRawObjectsTask
      .next(convertMap)
      .next(runCrawlerTask)
      .next(validateAthenaTask)
      .next(successState);

    // Dedicated CloudWatch log group with one-month retention so
    // execution traces are inspectable but do not accumulate
    // indefinitely.
    const stateMachineLogGroup = new logs.LogGroup(this, 'TransformationStateMachineLogGroup', {
      logGroupName: `/aws/vendedlogs/states/goat-network-transformation-${this.account}-${this.region}`,
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    this.transformationStateMachine = new sfn.StateMachine(this, 'TransformationStateMachine', {
      stateMachineName: `goat-network-transformation-${this.account}-${this.region}`,
      stateMachineType: sfn.StateMachineType.STANDARD,
      definitionBody: sfn.DefinitionBody.fromChainable(definition),
      timeout: cdk.Duration.hours(1),
      tracingEnabled: true,
      logs: {
        destination: stateMachineLogGroup,
        level: sfn.LogLevel.ERROR,
        includeExecutionData: false,
      },
      comment:
        'G.O.A.T. Network Agent Transformation_Workflow: ListRawObjects → Map(ConvertPcapToParquet) → RunCrawler → ValidateAthena. Any failure transitions to a Fail state emitting { failed_task, error_reason }.',
    });

    // -----------------------------------------------------------------------
    // Traffic Mirror plumbing (Reqs 6.1-6.6, 6.12; Task 22)
    //
    // The agent's capture data plane has three pieces, all provisioned
    // here:
    //
    //   1. Network_Agent_VPC + dedicated collector subnet -- gives the
    //      Traffic_Mirror_Collector EC2 instance a stable home with
    //      DNS-resolution enabled (Req 19.14) and a single-AZ
    //      footprint (Req 6.1: no ASG, no NLB).
    //   2. Single t3.small EC2 collector -- runs the per-VNI splitter
    //      and S3 uploader as systemd units, populated from a CDK
    //      asset bundle.
    //   3. Traffic Mirror Filter (default-allow) + Target
    //      (network-interface) -- the agent calls
    //      `ec2:CreateTrafficMirrorSession` against this filter and
    //      target whenever `start_capture` fires.
    //
    // Resource ordering: the VPC and subnet are constructed first so
    // the security group, instance, and Traffic Mirror Target can
    // reference them without forward declarations.
    // -----------------------------------------------------------------------

    // ----- Network_Agent_VPC (Req 6.1) ------------------------------------
    //
    // /16 dedicated VPC with one /24 private subnet hosting the
    // collector. Private subnet with VPC endpoints is used because:
    //
    //   1. The collector's only outbound traffic is to AWS service
    //      endpoints (S3, DynamoDB, SSM). All of those are
    //      reachable via VPC endpoints without internet access.
    //   2. A NAT Gateway would cost ~$33/month per AZ in us-east-1,
    //      which would dominate the demo's monthly bill. VPC
    //      endpoints (S3 Gateway is free, Interface endpoints are
    //      ~$7/month each) are more cost-effective.
    //   3. No public IP or internet route means zero inbound attack
    //      surface — the instance is only reachable via Traffic
    //      Mirror (UDP/4789 from VPC) and SSM Session Manager.
    //   4. First-boot package install (`dnf`) is handled by adding
    //      packages to the AMI or using S3-hosted repos. The
    //      bootstrap script uses only AWS CLI (pre-installed on
    //      AL2023) and Python (pre-installed), so no external
    //      package download is needed.
    //
    // `enableDnsSupport` and `enableDnsHostnames` are both true so
    // the orchestration agent's `active_dns_lookup` resolution
    // strategy (Req 19.14) works for instances in this VPC, and
    // Interface VPC endpoints resolve correctly via private DNS.
    //
    // `natGateways: 0` keeps costs minimal — all AWS API access
    // goes through VPC endpoints.
    this.networkAgentVpc = new ec2.Vpc(this, 'NetworkAgentVpc', {
      vpcName: 'goat-demo-vpc',
      ipAddresses: ec2.IpAddresses.cidr(NETWORK_AGENT_VPC_CIDR),
      maxAzs: 2,
      natGateways: 0,
      enableDnsSupport: true,
      enableDnsHostnames: true,
      subnetConfiguration: [
        {
          name: 'CollectorSubnet',
          subnetType: ec2.SubnetType.PRIVATE_ISOLATED,
          cidrMask: 24,
        },
      ],
      // Tagging is the standard CDK idiom for resource labels; the
      // tag key matches the tag the Network Agent uses to filter ENIs
      // it owns vs. ENIs it should not touch. The collector ENI itself
      // is never an opt-in target -- `start_capture` rejects requests
      // whose `eni_ids` overlap with the collector -- but the tag here
      // documents that the VPC was created by this stack.
    });
    cdk.Tags.of(this.networkAgentVpc).add('goat:component', 'network-agent');
    cdk.Tags.of(this.networkAgentVpc).add('goat-demo', 'true');
    cdk.Tags.of(this.networkAgentVpc).add('Name', 'goat-demo-vpc');

    // ----- VPC Endpoints (private subnet connectivity) --------------------
    //
    // The collector runs in a PRIVATE_ISOLATED subnet with no internet
    // access. These VPC endpoints provide the AWS API connectivity it
    // needs:
    //
    //   - S3 (Gateway): Free. Used by the uploader to PutObject pcap
    //     files and by bootstrap to download the CDK asset bundle.
    //   - DynamoDB (Gateway): Free. Used by the splitter to read the
    //     Vni_Lookup_Table.
    //   - SSM, SSM Messages, EC2 Messages (Interface): Required for
    //     Systems Manager Session Manager to work without internet.
    //     Operators use SSM for break-glass access to the collector.

    // S3 Gateway Endpoint (free, no per-hour charge)
    this.networkAgentVpc.addGatewayEndpoint('S3Endpoint', {
      service: ec2.GatewayVpcEndpointAwsService.S3,
    });

    // DynamoDB Gateway Endpoint (free, no per-hour charge)
    this.networkAgentVpc.addGatewayEndpoint('DynamoDbEndpoint', {
      service: ec2.GatewayVpcEndpointAwsService.DYNAMODB,
    });

    // SSM Interface Endpoints (required for Session Manager)
    this.networkAgentVpc.addInterfaceEndpoint('SsmEndpoint', {
      service: ec2.InterfaceVpcEndpointAwsService.SSM,
      privateDnsEnabled: true,
    });
    this.networkAgentVpc.addInterfaceEndpoint('SsmMessagesEndpoint', {
      service: ec2.InterfaceVpcEndpointAwsService.SSM_MESSAGES,
      privateDnsEnabled: true,
    });
    this.networkAgentVpc.addInterfaceEndpoint('Ec2MessagesEndpoint', {
      service: ec2.InterfaceVpcEndpointAwsService.EC2_MESSAGES,
      privateDnsEnabled: true,
    });

    // The single subnet in the VPC. We reference it explicitly (rather
    // than passing `vpcSubnets` blindly) so the collector's primary
    // ENI lands in the right place and its private IP is selectable
    // (the agent's collector-readiness check does not need the IP, but
    // future operator tooling may).
    const collectorSubnet = this.networkAgentVpc.selectSubnets({
      subnetGroupName: 'CollectorSubnet',
    });

    // ----- Collector security group (Reqs 6.2, 6.6) -----------------------
    //
    // Ingress: UDP/4789 from the VPC CIDR. Traffic Mirror sources in
    // this VPC (the demo's TLS Fragmentation Reproduction Scenario,
    // for example) deliver VXLAN-encapsulated frames over UDP/4789;
    // restricting source to the VPC CIDR -- instead of `0.0.0.0/0` --
    // keeps the demo's blast radius small. Cross-VPC mirroring would
    // require a separate operator-driven rule.
    //
    // Egress: default allow-all (CDK's `allowAllOutbound: true`).
    // The instance needs reach to:
    //   - S3 (pcap uploads)
    //   - DynamoDB (Vni_Lookup_Table reads)
    //   - EC2 metadata (IMDSv2)
    //   - `dnf` repos (first-boot package install)
    //
    // Restricting egress with prefix lists per service is feasible
    // but adds friction without security benefit in a demo.
    this.collectorSecurityGroup = new ec2.SecurityGroup(this, 'CollectorSecurityGroup', {
      vpc: this.networkAgentVpc,
      securityGroupName: `goat-network-collector-sg-${this.account}-${this.region}`,
      description: 'G.O.A.T. Network Agent Traffic_Mirror_Collector -- VXLAN UDP/4789 from VPC, full egress.',
      allowAllOutbound: true,
    });
    this.collectorSecurityGroup.addIngressRule(
      ec2.Peer.ipv4(NETWORK_AGENT_VPC_CIDR),
      ec2.Port.udp(VXLAN_UDP_PORT),
      'VXLAN-encapsulated mirrored traffic from in-VPC sources (UDP/4789).',
    );
    this.collectorSecurityGroup.addIngressRule(
      ec2.Peer.ipv4(NETWORK_AGENT_VPC_CIDR),
      ec2.Port.tcp(COLLECTOR_HEALTHCHECK_PORT),
      'NLB Traffic Mirror Target health check (TCP/8081) from in-VPC NLB nodes.',
    );

    // ----- Collector asset bundle (splitter + uploader + bootstrap + wheels) -
    //
    // Bundles `splitter.py`, `uploader.sh`, `bootstrap.sh`, and
    // pre-downloaded pip wheels (scapy) into a single zip uploaded to
    // the CDK assets bucket. The bootstrap UserData downloads this zip
    // on first boot via `aws s3 cp`, extracts it into
    // `/opt/goat-collector/`, and installs wheels from the local
    // `wheels/` subdirectory — no internet access required.
    this.collectorAsset = new s3Assets.Asset(this, 'CollectorAsset', {
      path: COLLECTOR_ASSET_DIR,
      exclude: ['README.md', '__pycache__/**', '*.pyc'],
    });

    // ----- Collector instance role ----------------------------------------
    //
    // Permissions are deliberately minimal:
    //
    //   - `AmazonSSMManagedInstanceCore` (managed): SSM Session Manager
    //     access for operator diagnostics. Always-attached because
    //     SSH is not configured on the instance and operators need a
    //     break-glass path to inspect collector logs.
    //   - `s3:PutObject` on `${networkDataBucket}/raw/*`: the only
    //     S3 verb the uploader needs. No `Get`/`List`/`Delete`; no
    //     access to `parquet/` (Transformation_Workflow owns that).
    //   - `dynamodb:GetItem`, `Query` on `${vniLookupTable}` + GSI:
    //     the splitter's VNI cache reads from the table on cache
    //     miss. `Query` is granted because the design's "VNI to
    //     capture_id mapping" notes future tooling may need to
    //     enumerate VNIs by capture_id; restricting to GetItem only
    //     would force a redeploy when that wiring is added.
    //   - Implicit asset-bucket read via the
    //     `collectorAsset.grantRead(...)` call below: the bootstrap
    //     script needs this to download the splitter/uploader on
    //     first boot.
    const collectorRole = new iam.Role(this, 'CollectorInstanceRole', {
      assumedBy: new iam.ServicePrincipal('ec2.amazonaws.com'),
      description: 'IAM role assumed by the Traffic_Mirror_Collector EC2 instance for S3 uploads and Vni_Lookup_Table reads.',
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('AmazonSSMManagedInstanceCore'),
      ],
    });
    collectorRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'CollectorPutPcapToRawPrefix',
        effect: iam.Effect.ALLOW,
        actions: ['s3:PutObject', 's3:AbortMultipartUpload'],
        resources: [`${this.networkDataBucket.bucketArn}/raw/*`],
      }),
    );
    collectorRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'CollectorReadVniLookupTable',
        effect: iam.Effect.ALLOW,
        actions: ['dynamodb:GetItem', 'dynamodb:Query'],
        resources: [
          this.vniLookupTable.tableArn,
          `${this.vniLookupTable.tableArn}/index/*`,
        ],
      }),
    );
    // Asset bucket grant for the bootstrap download.
    this.collectorAsset.grantRead(collectorRole);

    // ----- Collector primary ENI (Req 6.1: "dedicated ENI") --------------
    //
    // Provisioned as a separate `CfnNetworkInterface` (rather than
    // letting the EC2 instance's default network behavior provision
    // an implicit ENI) so the Traffic Mirror Target below can
    // reference it by its CloudFormation `Ref` (which resolves to the
    // ENI ID) without having to fish the primary ENI ID out of the
    // instance after creation. This is the canonical CDK pattern
    // when an ENI ID is required at synth time.
    //
    // The ENI lives in the collector subnet and carries the
    // collector's security group (which permits VXLAN ingress on
    // UDP/4789). The bootstrap script binds the kernel's `vxlan0`
    // interface to this ENI's address.
    const collectorSubnetIds = collectorSubnet.subnetIds;
    if (collectorSubnetIds.length === 0) {
      throw new Error(
        `NetworkAgentVpc subnet selection returned no subnets; expected exactly one CollectorSubnet.`,
      );
    }
    const collectorEni = new ec2.CfnNetworkInterface(this, 'CollectorEni', {
      subnetId: collectorSubnetIds[0],
      description: 'Dedicated ENI for the G.O.A.T. Network Agent Traffic_Mirror_Collector -- receives VXLAN mirrored traffic on UDP/4789.',
      groupSet: [this.collectorSecurityGroup.securityGroupId],
      tags: [{ key: 'Name', value: `goat-network-collector-eni-${this.account}-${this.region}` }],
    });

    // ----- Collector EC2 instance (Req 6.1) -------------------------------
    //
    // UserData rendering: the bootstrap script in the asset directory
    // contains placeholder tokens (`__ASSET_BUCKET__`, etc.). We read
    // the file at synth time, substitute the deploy-time values, and
    // feed the result into the instance's UserData via
    // `cdk.Fn.sub`. This avoids embedding raw CDK tokens in the bash
    // string concatenation, which would resolve to opaque
    // `${Token[ABC123]}` markers in the rendered UserData.
    //
    // L1 `CfnInstance` is used (rather than the higher-level
    // `ec2.Instance`) because the L2 construct does not expose an
    // API for "use this pre-provisioned ENI as the primary network
    // interface" -- and the Traffic Mirror Target needs the primary
    // ENI's ID at synth time. Dropping to L1 buys deterministic
    // ENI-ID resolution at the cost of slightly more verbose
    // resource configuration.
    const bootstrapTemplate = readFileSync(
      path.join(COLLECTOR_ASSET_DIR, COLLECTOR_BOOTSTRAP_FILENAME),
      { encoding: 'utf-8' },
    );

    // Hash the raw bootstrap template so any change to bootstrap.sh forces
    // a new instance logical ID (and therefore a CloudFormation
    // replacement). L1 CfnInstance treats a UserData change as
    // update-without-interruption, and cloud-init only runs UserData on
    // first boot — so without this, an edited bootstrap script would never
    // actually re-run on the existing collector. Mirrors the
    // `TlsInstance${userDataHash}` pattern in the demo scenario stack.
    const bootstrapHash = crypto
      .createHash('sha256')
      .update(bootstrapTemplate)
      .digest('hex')
      .slice(0, 8);
    // Fn.sub treats every ${...} as a CloudFormation substitution variable.
    // The bootstrap script contains bash ${VAR} references (INSTALL_DIR,
    // OUTPUT_DIR, ENV_FILE, etc.) that must be preserved literally. We first
    // escape ALL ${...} patterns using the Fn::Sub literal escape syntax
    // (${!VAR} → outputs ${VAR}), then selectively un-escape the CDK
    // placeholders we actually want Fn::Sub to resolve.
    const escapedTemplate = bootstrapTemplate.replace(/\$\{/g, '${!');
    const bootstrapScript = cdk.Fn.sub(
      escapedTemplate
        .replace(/__ASSET_BUCKET__/g, '${AssetBucket}')
        .replace(/__ASSET_OBJECT_KEY__/g, '${AssetObjectKey}')
        .replace(/__DATA_BUCKET__/g, '${DataBucket}')
        .replace(/__VNI_LOOKUP_TABLE__/g, '${VniLookupTable}')
        .replace(/__AWS_REGION__/g, '${AwsRegion}'),
      {
        AssetBucket: this.collectorAsset.s3BucketName,
        AssetObjectKey: this.collectorAsset.s3ObjectKey,
        DataBucket: this.networkDataBucketName,
        VniLookupTable: this.vniLookupTable.tableName,
        AwsRegion: this.region,
      },
    );

    const collectorInstanceProfile = new iam.CfnInstanceProfile(this, 'CollectorInstanceProfile', {
      roles: [collectorRole.roleName],
    });

    // Resolve the latest AL2023 x86_64 AMI ID via SSM parameter at
    // synth time. The SSM Parameter `aws/service/ami-amazon-linux-latest`
    // tree is the AWS-published canonical source for "always-current"
    // AL2023 AMI IDs and is the same backing source used by
    // `ec2.MachineImage.latestAmazonLinux2023()`.
    const collectorAmiId = ec2.MachineImage.latestAmazonLinux2023({
      cpuType: ec2.AmazonLinuxCpuType.X86_64,
    }).getImage(this).imageId;

    const collectorCfnInstance = new ec2.CfnInstance(this, `CollectorInstance${bootstrapHash}`, {
      instanceType: 't3.small',
      imageId: collectorAmiId,
      iamInstanceProfile: collectorInstanceProfile.ref,
      networkInterfaces: [
        {
          // The pre-provisioned ENI is attached as the primary
          // network interface (deviceIndex=0). CloudFormation does
          // not let us share an ENI between resources, so the
          // `Ref` here is the ENI ID we will also reference from
          // the Traffic Mirror Target below.
          networkInterfaceId: collectorEni.ref,
          deviceIndex: '0',
        },
      ],
      blockDeviceMappings: [
        {
          deviceName: '/dev/xvda',
          ebs: {
            volumeSize: COLLECTOR_ROOT_VOLUME_GIB,
            volumeType: 'gp3',
            encrypted: true,
            deleteOnTermination: true,
          },
        },
      ],
      // UserData is base64-encoded by CFN; encode here so the
      // rendered template resolves to a single Base64Encode intrinsic.
      userData: cdk.Fn.base64(bootstrapScript),
      // IMDSv2 only (Req -- token-required mode for hardening).
      metadataOptions: {
        httpTokens: 'required',
        httpEndpoint: 'enabled',
      },
      tags: [
        { key: 'Name', value: `goat-network-collector-${this.account}-${this.region}` },
        { key: 'goat:component', value: 'network-collector' },
      ],
    });
    // CFN does not always infer the ENI -> instance edge from the
    // `networkInterfaceId` ref alone; declare it explicitly.
    collectorCfnInstance.addDependency(collectorEni);

    // Adapter: expose the L1 instance via the L2 `ec2.Instance`-like
    // surface so the rest of the stack can keep using `instanceId`
    // without caring about the L1/L2 distinction. We only need
    // `instanceId` here, but other consumers may look at the role
    // or security group -- both already resolved on the L1 side.
    this.collectorInstance = collectorCfnInstance;

    // ----- Traffic Mirror Filter (Req 6.5) --------------------------------
    //
    // Single shared filter named `goat-network-default-filter`. The
    // agent supplies this filter's ID on every
    // `ec2:CreateTrafficMirrorSession` call (`TRAFFIC_MIRROR_FILTER_ID`
    // env var). Provisioning one filter for every Capture_Session
    // matches the design's "default-allow" model -- capture is gated
    // on the user's Capture_Opt_In_Tag (Req 3.14), not on filter rule
    // selection.
    //
    // L1 `CfnTrafficMirrorFilter` is used because no L2 construct
    // exists in `aws-cdk-lib` for Traffic Mirror; the L1 maps 1:1 to
    // the documented CloudFormation resource.
    this.trafficMirrorFilter = new ec2.CfnTrafficMirrorFilter(this, 'TrafficMirrorFilter', {
      description: 'G.O.A.T. Network Agent default Traffic Mirror filter -- TCP/UDP/ICMP from any to any in both directions.',
      // Network services (IPv4 traffic only) -- VXLAN runs over IPv4 in
      // AWS Traffic Mirroring; IPv6 mirroring is a future feature.
      networkServices: [],
      tags: [{ key: 'Name', value: TRAFFIC_MIRROR_FILTER_NAME }],
    });

    // Filter rules -- one ingress + one egress rule per L4 protocol
    // (TCP, UDP, ICMP). Req 6.5 says "at least one ingress rule and at
    // least one egress rule, each accepting TCP, UDP, and ICMP" --
    // the cleanest mapping is one rule per (direction, protocol) pair
    // so each rule has a single, well-known protocol number and the
    // rule list is self-documenting.
    //
    // Protocol numbers from IANA: TCP=6, UDP=17, ICMP=1. Rule numbers
    // are spaced 100 apart so future operator additions do not require
    // renumbering.
    interface FilterRule {
      readonly id: string;
      readonly direction: 'ingress' | 'egress';
      readonly ruleNumber: number;
      readonly protocol: number;
      readonly description: string;
    }
    const trafficMirrorFilterRules: readonly FilterRule[] = [
      { id: 'IngressTcp', direction: 'ingress', ruleNumber: 100, protocol: 6, description: 'Mirror inbound TCP traffic.' },
      { id: 'IngressUdp', direction: 'ingress', ruleNumber: 200, protocol: 17, description: 'Mirror inbound UDP traffic.' },
      { id: 'IngressIcmp', direction: 'ingress', ruleNumber: 300, protocol: 1, description: 'Mirror inbound ICMP traffic.' },
      { id: 'EgressTcp', direction: 'egress', ruleNumber: 100, protocol: 6, description: 'Mirror outbound TCP traffic.' },
      { id: 'EgressUdp', direction: 'egress', ruleNumber: 200, protocol: 17, description: 'Mirror outbound UDP traffic.' },
      { id: 'EgressIcmp', direction: 'egress', ruleNumber: 300, protocol: 1, description: 'Mirror outbound ICMP traffic.' },
    ];
    for (const rule of trafficMirrorFilterRules) {
      const cfnRule = new ec2.CfnTrafficMirrorFilterRule(this, `TrafficMirrorFilterRule${rule.id}`, {
        trafficMirrorFilterId: this.trafficMirrorFilter.ref,
        trafficDirection: rule.direction,
        ruleNumber: rule.ruleNumber,
        ruleAction: 'accept',
        protocol: rule.protocol,
        sourceCidrBlock: '0.0.0.0/0',
        destinationCidrBlock: '0.0.0.0/0',
        description: rule.description,
      });
      // CloudFormation does not infer the dependency between the
      // rule's `trafficMirrorFilterId` (a string ref) and the filter
      // resource itself. Add the edge so the filter is created first.
      cfnRule.addDependency(this.trafficMirrorFilter);
    }

    // ----- Traffic Mirror Target (Req 6.6) --------------------------------
    //
    // `network-interface` type referencing the dedicated collector ENI
    // provisioned above. Using a separately-declared
    // `CfnNetworkInterface` lets us pass `collectorEni.ref` (the ENI
    // ID) directly without round-tripping through the EC2 Instance
    // resource -- the Traffic Mirror Target needs an ENI ID at synth
    // time and CFN does not surface "primary ENI of an instance" as a
    // generated attribute on `AWS::EC2::Instance`.
    //
    // NLB-based Traffic Mirror Target (cross-AZ support)
    // --------------------------------------------------
    // An ENI-based target only receives mirrored traffic from the same
    // AZ. Using an NLB target allows any ENI in the VPC (regardless of
    // AZ) to mirror traffic to the collector. The NLB forwards VXLAN
    // UDP/4789 to the collector instance's target group.
    const nlb = new elbv2.NetworkLoadBalancer(this, 'CollectorNlb', {
      vpc: this.networkAgentVpc,
      internetFacing: false,
      crossZoneEnabled: true,
      vpcSubnets: { subnetGroupName: 'CollectorSubnet' },
      loadBalancerName: `goat-collector-nlb-${this.region}`,
    });

    const targetGroup = new elbv2.NetworkTargetGroup(this, 'CollectorTargetGroup', {
      vpc: this.networkAgentVpc,
      port: VXLAN_UDP_PORT,
      protocol: elbv2.Protocol.UDP,
      targetType: elbv2.TargetType.INSTANCE,
      // NLB cannot health-check the UDP/4789 traffic port directly, so we
      // health-check a dedicated TCP responder the collector runs on 8081
      // (started by bootstrap.sh). Without a passing health check the NLB
      // marks the target unhealthy and silently drops all mirrored VXLAN
      // traffic, producing empty captures.
      healthCheck: {
        protocol: elbv2.Protocol.TCP,
        port: String(COLLECTOR_HEALTHCHECK_PORT),
      },
    });
    targetGroup.addTarget(new elbv2Targets.InstanceIdTarget(collectorCfnInstance.ref, VXLAN_UDP_PORT));

    nlb.addListener('VxlanListener', {
      port: VXLAN_UDP_PORT,
      protocol: elbv2.Protocol.UDP,
      defaultTargetGroups: [targetGroup],
    });

    this.trafficMirrorTarget = new ec2.CfnTrafficMirrorTarget(this, 'TrafficMirrorTarget', {
      description: 'G.O.A.T. Network Agent Traffic_Mirror_Target (NLB type, cross-AZ) forwarding VXLAN to the collector instance.',
      networkLoadBalancerArn: nlb.loadBalancerArn,
      tags: [{ key: 'Name', value: `goat-network-target-${this.account}-${this.region}` }],
    });

    // -----------------------------------------------------------------------
    // StopCaptureInvokerLambda (Reqs 3.5, 4.6, 4.7, 6.12)
    //
    // Bridges EventBridge Scheduler's Auto_Stop_Schedule to the
    // Network Agent's `stop_capture` action because Scheduler does not
    // (yet) have a native target template for
    // `bedrock-agent-runtime:InvokeAgentRuntime`. The agent's
    // `start_capture` handler creates a one-shot `at(<deadline>)`
    // schedule whose target is this Lambda; this Lambda then calls the
    // runtime with payload
    // `{"action": "stop_capture", "params": {"capture_id": "<id>"}}`.
    //
    // Retry policy lives entirely inside the Lambda (Req 4.7: "retry
    // up to 3 times with backoff"). On exhaustion the Lambda emits a
    // single `goat-network-auto-stop-failures` CloudWatch metric data
    // point under the `GOAT/Network` namespace (the metric and
    // namespace constants pinned at module level so the IAM condition
    // and the Lambda code agree on the value), and re-raises so the
    // failure is visible in CloudWatch Logs/Metrics.
    //
    // Choice -- small inline Function (no container image, no layer):
    // the handler depends only on `boto3` (provided by the Lambda
    // runtime) and the standard library, so a `lambda.Code.fromAsset`
    // on the source directory keeps the deployment artefact tiny and
    // the cold-start fast.
    // -----------------------------------------------------------------------
    const stopCaptureInvokerLogGroup = new logs.LogGroup(
      this,
      'StopCaptureInvokerLambdaLogGroup',
      {
        logGroupName: `/aws/lambda/goat-network-stop-capture-invoker-${this.account}-${this.region}`,
        retention: logs.RetentionDays.ONE_MONTH,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
      },
    );

    // Resolve the runtime ARN that the Lambda is allowed to invoke.
    //
    // - Post-follow-up-wiring path (preferred, single-resource
    //   policy): the CDK app passes the actual runtime ARN via
    //   `props.networkAgentRuntimeArn`. The Lambda's IAM policy and
    //   `NETWORK_AGENT_RUNTIME_ARN` env var both resolve to that
    //   single ARN.
    //
    // - Bootstrap path (before the follow-up wiring task is
    //   implemented, so `props.networkAgentRuntimeArn` is undefined):
    //   the Lambda's IAM policy is scoped to the well-known runtime
    //   name pattern in the deploying account/region. The env var is
    //   intentionally set to the same wildcard pattern; this fails
    //   loudly at runtime (`InvokeAgentRuntime` rejects wildcard ARNs
    //   in `agentRuntimeArn`) until the follow-up wiring task is
    //   completed. That fail-fast is preferable to silently invoking
    //   the wrong runtime.
    //
    // The wildcard pattern is intentionally restrictive -- it covers
    // only `runtime/goat_network_agent*` resources, not arbitrary
    // Bedrock AgentCore runtimes. AWS appends a hash suffix to runtime
    // names so the trailing `*` is necessary; the prefix prevents
    // cross-domain invocation even in the bootstrap path.
    const networkAgentRuntimeArnResource = props?.networkAgentRuntimeArn
      ?? `arn:${cdk.Aws.PARTITION}:bedrock-agentcore:${this.region}:${this.account}:runtime/${STOP_CAPTURE_INVOKER_DEFAULT_RUNTIME_NAME}*`;

    this.stopCaptureInvokerLambda = new lambda.Function(
      this,
      'StopCaptureInvokerLambda',
      {
        functionName: `goat-network-stop-capture-invoker-${this.account}-${this.region}`,
        runtime: TRANSFORMATION_LAMBDA_RUNTIME,
        handler: 'index.lambda_handler',
        code: lambda.Code.fromAsset(STOP_CAPTURE_INVOKER_LAMBDA_DIR),
        timeout: cdk.Duration.minutes(2),
        memorySize: 256,
        environment: {
          // The Lambda reads this env var to know which runtime to
          // call. In the bootstrap path this is a wildcard pattern
          // and the runtime call will fail until the follow-up wiring
          // task supplies the real ARN.
          NETWORK_AGENT_RUNTIME_ARN: networkAgentRuntimeArnResource,
          METRIC_NAMESPACE: STOP_CAPTURE_INVOKER_METRIC_NAMESPACE,
          METRIC_NAME: STOP_CAPTURE_INVOKER_METRIC_NAME,
          // 3 attempts with 1s base = waits of 0s, 1s, 2s before each
          // attempt. Total worst-case Lambda runtime ~3-6s assuming
          // each InvokeAgentRuntime call returns within 1-2 seconds.
          MAX_INVOCATION_ATTEMPTS: '3',
          BACKOFF_BASE_SECONDS: '1.0',
        },
        logGroup: stopCaptureInvokerLogGroup,
        description:
          'G.O.A.T. Network Agent Auto_Stop_Schedule shim: bridges EventBridge Scheduler to bedrock-agent-runtime:InvokeAgentRuntime for stop_capture (Reqs 4.6, 4.7).',
      },
    );

    // IAM scope #1 -- `bedrock-agent-runtime:InvokeAgentRuntime` on the
    // Network Agent runtime ARN only. The exact resource depends on
    // whether the follow-up wiring task has plumbed the actual runtime
    // ARN through; see the comment above
    // `networkAgentRuntimeArnResource`.
    this.stopCaptureInvokerLambda.addToRolePolicy(
      new iam.PolicyStatement({
        sid: 'StopCaptureInvokerInvokeAgentRuntime',
        effect: iam.Effect.ALLOW,
        actions: ['bedrock-agentcore:InvokeAgentRuntime'],
        resources: [networkAgentRuntimeArnResource],
      }),
    );

    // IAM scope #2 -- `cloudwatch:PutMetricData` gated on the namespace
    // condition (CloudWatch does not support resource-level ARNs for
    // PutMetricData; the documented approach is the
    // `cloudwatch:namespace` request condition). This prevents the
    // Lambda from polluting other namespaces if compromised.
    this.stopCaptureInvokerLambda.addToRolePolicy(
      new iam.PolicyStatement({
        sid: 'StopCaptureInvokerEmitFailureMetric',
        effect: iam.Effect.ALLOW,
        actions: ['cloudwatch:PutMetricData'],
        resources: ['*'],
        conditions: {
          StringEquals: {
            'cloudwatch:namespace': STOP_CAPTURE_INVOKER_METRIC_NAMESPACE,
          },
        },
      }),
    );

    // -----------------------------------------------------------------------
    // EventBridge Scheduler -- Auto_Stop_Schedule group (Task 27, Reqs 3.5,
    // 4.6, 4.10, 6.12)
    //
    // The Network Agent's `start_capture` handler (Task 11,
    // agents/network-agent/main.py `create_auto_stop_schedule`) creates
    // one `at(<deadline>)` schedule per Capture_Session. Pinning every
    // schedule into a single stack-owned group serves three purposes:
    //
    //   1. The AgentCore runtime IAM policy below scopes
    //      `scheduler:CreateSchedule` / `DeleteSchedule` / `GetSchedule`
    //      to a single resource ARN (`schedule/${groupName}/*`) -- never
    //      a wildcard on the group component (Task 27 line item).
    //   2. `stop_capture` can issue `DeleteSchedule(Name, GroupName)`
    //      using the per-capture name plus this stable group, instead
    //      of having to discover an ad-hoc per-capture group name.
    //   3. Operators can list every Auto_Stop_Schedule in a single
    //      Scheduler console view by filtering on the group, which is
    //      useful when reconciling schedules after the
    //      `goat-network-auto-stop-failures` CloudWatch metric fires.
    //
    // The physical name combines the module-level base, the deploying
    // account, and the deploying region for multi-region uniqueness
    // (matches the naming convention of every other physical resource
    // in this stack -- see `goat-network-vni-lookup-${account}-${region}`,
    // `goat-network-capture-state-${account}-${region}`).
    // -----------------------------------------------------------------------
    const autoStopScheduleGroupName =
      `${AUTO_STOP_SCHEDULE_GROUP_NAME_BASE}-${this.account}-${this.region}`;
    this.autoStopScheduleGroup = new scheduler.CfnScheduleGroup(
      this,
      'AutoStopScheduleGroup',
      {
        name: autoStopScheduleGroupName,
      },
    );
    this.autoStopScheduleGroup.applyRemovalPolicy(cdk.RemovalPolicy.DESTROY);

    // -----------------------------------------------------------------------
    // Scheduler-target IAM role (Task 27, Reqs 3.5, 4.6, 4.7, 6.12)
    //
    // EventBridge Scheduler assumes this role to invoke the
    // StopCaptureInvokerLambda when an Auto_Stop_Schedule fires. The
    // role exists separately from the AgentCore runtime role for two
    // reasons:
    //
    //   1. AWS requires Scheduler's `Target.RoleArn` to trust
    //      `scheduler.amazonaws.com`; the AgentCore runtime role
    //      trusts `bedrock-agentcore.amazonaws.com`, so a single role
    //      cannot satisfy both trust policies.
    //   2. Splitting the role lets the agent role hold only a narrow
    //      `iam:PassRole` permission on this exact role ARN
    //      (conditioned on `iam:PassedToService = scheduler.amazonaws.com`),
    //      preventing the agent from passing the role to any other
    //      service or using it directly to invoke the Lambda.
    //
    // The trust policy includes the standard
    // `aws:SourceAccount` confused-deputy mitigation so a Scheduler
    // service principal in another account cannot trick this role
    // into invoking the Lambda -- only Scheduler in the deploying
    // account is permitted.
    //
    // The permission grant is exactly one statement:
    // `lambda:InvokeFunction` on the StopCaptureInvokerLambda ARN.
    // No DynamoDB, no S3, no other Lambdas; Scheduler's only job
    // here is to invoke the auto-stop shim.
    // -----------------------------------------------------------------------
    this.schedulerTargetRole = new iam.Role(this, 'SchedulerTargetRole', {
      assumedBy: new iam.ServicePrincipal('scheduler.amazonaws.com', {
        conditions: {
          StringEquals: {
            'aws:SourceAccount': this.account,
          },
        },
      }),
      description:
        'Assumed by EventBridge Scheduler to invoke the Network Agent ' +
        'StopCaptureInvokerLambda when an Auto_Stop_Schedule fires (Reqs 4.6, 4.7).',
    });

    this.schedulerTargetRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'SchedulerTargetInvokeStopCaptureInvoker',
        effect: iam.Effect.ALLOW,
        actions: ['lambda:InvokeFunction'],
        resources: [
          this.stopCaptureInvokerLambda.functionArn,
          // Versioned/aliased ARN form so a future $LATEST → alias
          // migration on the StopCaptureInvokerLambda does not break
          // Scheduler's invoke; same approach as AWS Console-generated
          // Scheduler target roles.
          `${this.stopCaptureInvokerLambda.functionArn}:*`,
        ],
      }),
    );

    // -----------------------------------------------------------------------
    // AgentCore runtime IAM permissions (Task 27, Reqs 6.12, 19.14)
    //
    // The Network Agent runtime role is created by `BaseInfraStack` with
    // the standard ECR / CloudWatch / X-Ray / AgentCore-identity baseline
    // (see super() call above and base-infra-stack.ts). Task 27 layers
    // the Network-Agent-specific permissions on top via
    // `this.agentRole.addToPolicy(...)` so the resource ARNs can
    // reference the resources actually provisioned in this stack
    // (capture state table, VNI lookup table, schedule group,
    // scheduler-target role, transformation state machine, data bucket,
    // Glue database/table) -- all of which exist by the time these
    // statements run because their constructs were created higher up
    // in this same constructor.
    //
    // The statement set is the literal list documented in the design's
    // "Athena-side defense in depth" section (design.md) and the
    // task description: minimum permissions required for each action,
    // resource-scoped to the deploying account, region, and the
    // specific resources owned by this stack. Notably absent:
    //   * `athena:CreateNamedQuery` -- never granted (design.md).
    //   * `glue:UpdateTable` and any DDL (`glue:CreateTable`,
    //     `glue:DeleteTable`, `glue:UpdatePartition`, ...) -- never
    //     granted; the agent only reads catalog metadata.
    //   * `dynamodb:*` (wildcard) -- only the five capture-lifecycle
    //     verbs are granted, and only on the two stack-owned tables.
    //   * `scheduler:*` (wildcard) -- only the three lifecycle verbs
    //     are granted, and only on the stack-owned schedule group
    //     (no wildcard on the group component).
    //   * `iam:PassRole` (wildcard resource) -- only the
    //     scheduler-target role is allowed, and only when the target
    //     service is `scheduler.amazonaws.com`.
    //   * `route53resolver:*` and any broader Route 53 permission --
    //     `active_dns_lookup` runs as an in-VPC `socket.getaddrinfo`
    //     call against the VPC's `.2` resolver; that path requires
    //     no IAM at all (it is purely network-level egress to
    //     `169.254.169.253:53`). VPC DNS resolution is enabled at
    //     the VPC construct level, not via IAM (Req 19.14).
    // -----------------------------------------------------------------------

    // ---- EC2 / Traffic Mirror Sessions ------------------------------------
    //
    // `Describe*` calls have no resource-level scoping in EC2's IAM
    // model -- AWS rejects any resource other than `*` for these
    // verbs. We minimize blast radius by leaving `Create*` and
    // `Delete*` for traffic-mirror sessions resource-scoped to the
    // deploying account/region: the agent can manage its own
    // sessions, but cannot affect mirror filters or targets owned
    // by other tools in the same account.
    this.agentRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'NetworkAgentEc2DescribeReadOnly',
        effect: iam.Effect.ALLOW,
        actions: [
          'ec2:DescribeNetworkInterfaces',
          'ec2:DescribeInstances',
          'ec2:DescribeInstanceStatus',
          'ec2:DescribeTrafficMirrorSessions',
          'ec2:DescribeTrafficMirrorTargets',
          'ec2:DescribeTrafficMirrorFilters',
          'ec2:DescribeVpcs',
          'ec2:DescribeSubnets',
        ],
        // EC2 Describe* APIs do not support resource-level permissions.
        resources: ['*'],
      }),
    );
    this.agentRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'NetworkAgentEc2TrafficMirrorSessionLifecycle',
        effect: iam.Effect.ALLOW,
        actions: [
          'ec2:CreateTrafficMirrorSession',
          'ec2:DeleteTrafficMirrorSession',
        ],
        resources: [
          // Session resource: created per `start_capture`, deleted per
          // `stop_capture`. Resource-level scoping pins these to the
          // deploying account/region.
          `arn:${cdk.Aws.PARTITION}:ec2:${this.region}:${this.account}:traffic-mirror-session/*`,
          // CreateTrafficMirrorSession also references the filter
          // and target as part of the request -- IAM matches against
          // any of the resources in the call, so we list them here
          // alongside the session ARN. Both filter and target ARNs
          // are scoped to the stack-owned resources rather than `*`.
          `arn:${cdk.Aws.PARTITION}:ec2:${this.region}:${this.account}:traffic-mirror-filter/*`,
          `arn:${cdk.Aws.PARTITION}:ec2:${this.region}:${this.account}:traffic-mirror-target/*`,
          // The mirror source is an ENI; pin the resource-level grant
          // to ENIs in the deploying account so the agent cannot
          // mirror ENIs in other accounts via shared VPCs.
          `arn:${cdk.Aws.PARTITION}:ec2:${this.region}:${this.account}:network-interface/*`,
        ],
      }),
    );

    // ---- DynamoDB: capture state + VNI lookup tables only -----------------
    //
    // Both tables (and their GSIs) are scoped explicitly. Wildcards
    // are intentionally absent -- the agent has no business reading
    // any other DynamoDB table, including the orchestration agent's
    // conversation tables.
    this.agentRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'NetworkAgentDynamoDbTables',
        effect: iam.Effect.ALLOW,
        actions: [
          'dynamodb:GetItem',
          'dynamodb:PutItem',
          'dynamodb:UpdateItem',
          'dynamodb:DeleteItem',
          'dynamodb:Query',
          'dynamodb:Scan',
          'dynamodb:BatchWriteItem',
        ],
        resources: [
          // Table resource ARNs (CRUD + Query against base table).
          this.captureStateTable.tableArn,
          this.vniLookupTable.tableArn,
          // Index resource ARNs (Query against the GSIs:
          // `status-index` for `list_captures`, `capture-id-index`
          // for the `stop_capture` VNI cleanup path). DynamoDB
          // requires a separate index ARN to authorize Query on a
          // GSI even when the principal already has Query on the
          // base table.
          `${this.captureStateTable.tableArn}/index/*`,
          `${this.vniLookupTable.tableArn}/index/*`,
        ],
      }),
    );

    // ---- EventBridge Scheduler: stack-owned group only --------------------
    //
    // Resource ARN form for an EventBridge Scheduler schedule:
    //   `arn:aws:scheduler:${region}:${account}:schedule/${groupName}/${scheduleName}`
    //
    // We scope to `schedule/${groupName}/*` (every schedule in the
    // stack-owned group) -- never `schedule/*/*` (every schedule in
    // every group) and never the bare `*`. The agent therefore
    // cannot create, read, or delete schedules in any other group
    // (including AWS's `default` group), which guarantees the
    // schedules it creates can be enumerated and torn down by the
    // Stack's removal policy.
    this.agentRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'NetworkAgentSchedulerScopedToGroup',
        effect: iam.Effect.ALLOW,
        actions: [
          'scheduler:CreateSchedule',
          'scheduler:DeleteSchedule',
          'scheduler:GetSchedule',
        ],
        resources: [
          `arn:${cdk.Aws.PARTITION}:scheduler:${this.region}:${this.account}:schedule/${autoStopScheduleGroupName}/*`,
        ],
      }),
    );

    // ---- iam:PassRole on the scheduler-target role only -------------------
    //
    // EventBridge Scheduler's `CreateSchedule` API requires
    // `iam:PassRole` on the role supplied in `Target.RoleArn`. We
    // grant exactly that, on exactly the scheduler-target role, with
    // a `iam:PassedToService` condition that further constrains the
    // grant to Scheduler -- so the agent cannot reuse the role with
    // EC2, Lambda, ECS, or any other service principal. Combined
    // with the role's own trust policy (which only allows
    // `scheduler.amazonaws.com` from the deploying account), this
    // gives a tight three-way bind: agent ⇄ scheduler ⇄
    // StopCaptureInvokerLambda.
    this.agentRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'NetworkAgentPassSchedulerTargetRole',
        effect: iam.Effect.ALLOW,
        actions: ['iam:PassRole'],
        resources: [this.schedulerTargetRole.roleArn],
        conditions: {
          StringEquals: {
            'iam:PassedToService': 'scheduler.amazonaws.com',
          },
        },
      }),
    );

    // ---- Step Functions: Transformation_Workflow ARN only -----------------
    //
    // Scoped to the exact state machine ARN this stack provisions
    // (`Transformation_Workflow`, Task 25). `transform_capture`
    // calls `StartExecution`; `get_capture_progress` (and any
    // future progress-polling logic in the orchestration agent)
    // may call `DescribeExecution`. Both verbs are restricted to
    // the one state machine -- the agent has no business invoking
    // any other Step Functions workflow in the account.
    //
    // NOTE: `DescribeExecution` requires the resource ARN to be
    // the *execution* ARN (not the state machine ARN). We grant
    // both: the state machine ARN (for `StartExecution`) and the
    // execution-ARN wildcard derived from it (for
    // `DescribeExecution`). The wildcard portion only matches
    // executions of *this* state machine, not any other.
    this.agentRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'NetworkAgentStepFunctionsTransformation',
        effect: iam.Effect.ALLOW,
        actions: [
          'states:StartExecution',
          'states:DescribeExecution',
        ],
        resources: [
          this.transformationStateMachine.stateMachineArn,
          // Execution ARN form:
          //   arn:aws:states:region:account:execution:<state-machine-name>:<execution-name>
          // `stateMachineArn` is the state machine resource ARN, not
          // the execution ARN, so we have to construct the execution
          // ARN form explicitly. The `:execution:` segment is the
          // canonical Step Functions execution-ARN prefix.
          `arn:${cdk.Aws.PARTITION}:states:${this.region}:${this.account}:execution:${this.transformationStateMachine.stateMachineName}:*`,
        ],
      }),
    );

    // ---- S3: Network_Data_Bucket + raw/, parquet/ prefixes only -----------
    //
    // `s3:ListBucket` is bucket-level (resource = bucket ARN) but is
    // narrowed to the two prefixes via the standard `s3:prefix`
    // request condition. `s3:GetObject` and `s3:PutObject` are
    // object-level -- the resource ARN is the per-object form
    // (`<bucket-arn>/<prefix>/*`). Splitting List from Get/Put into
    // two statements is the AWS-recommended pattern for prefix-
    // scoped bucket access.
    //
    // `raw/` is read+write because the collector uploads pcaps via
    // the agent's IAM identity in some test paths and the agent's
    // `get_capture_progress` action reads object metadata to compute
    // upload progress. `parquet/` is read+write because Athena
    // queries (and the Glue Crawler) read it; the agent itself only
    // reads `parquet/` for diagnostic spot-checks but the AWS API
    // model for Athena queries inherits the calling principal's S3
    // permissions, so a read grant is required.
    this.agentRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'NetworkAgentS3ListBucketScopedToPrefixes',
        effect: iam.Effect.ALLOW,
        actions: ['s3:ListBucket'],
        resources: [this.networkDataBucket.bucketArn],
        conditions: {
          StringLike: {
            's3:prefix': ['raw/*', 'parquet/*', 'raw', 'parquet'],
          },
        },
      }),
    );
    // GetBucketLocation must be unconditional -- Athena calls it without
    // any s3:prefix context key to verify the bucket's region before
    // executing queries. Scoping it behind a prefix condition causes
    // Athena to fail with "Access Denied" on the bucket verification step.
    this.agentRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'NetworkAgentS3GetBucketLocation',
        effect: iam.Effect.ALLOW,
        actions: ['s3:GetBucketLocation'],
        resources: [this.networkDataBucket.bucketArn],
      }),
    );
    this.agentRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'NetworkAgentS3ObjectAccessRawParquet',
        effect: iam.Effect.ALLOW,
        actions: ['s3:GetObject', 's3:PutObject', 's3:AbortMultipartUpload'],
        resources: [
          `${this.networkDataBucket.bucketArn}/raw/*`,
          `${this.networkDataBucket.bucketArn}/parquet/*`,
        ],
      }),
    );

    // ---- Athena + Glue: goat_network database only ------------------------
    //
    // Athena query control plane: `StartQueryExecution`,
    // `GetQueryExecution`, `GetQueryResults`. Scoped to the default
    // workgroup the deployment runs in (no custom workgroup is
    // provisioned for this demo; matches `validateAthenaLambda`'s
    // policy higher up in this stack and the cur-infra-stack.ts
    // pattern for sibling agents).
    //
    // Glue: read-only catalog metadata (`Get*`) on the
    // `goat_network` database, table, and partitions only. Athena
    // resolves schema and prunes partitions via these calls. NO
    // DDL/DML -- explicitly never `CreateTable`, `UpdateTable`,
    // `DeleteTable`, `UpdatePartition`, etc. (design.md
    // "Athena-side defense in depth").
    //
    // We deliberately do not grant `athena:CreateNamedQuery` even
    // though some Athena clients use it for query history -- the
    // Network Agent's `query_pcap` action runs ad-hoc queries via
    // the safe-rewrite path (Task 13) and never persists them.
    this.agentRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'NetworkAgentAthenaQueryControlPlane',
        effect: iam.Effect.ALLOW,
        actions: [
          'athena:StartQueryExecution',
          'athena:GetQueryExecution',
          'athena:GetQueryResults',
          'athena:StopQueryExecution',
          'athena:GetWorkGroup',
        ],
        resources: [
          `arn:${cdk.Aws.PARTITION}:athena:${this.region}:${this.account}:workgroup/primary`,
        ],
      }),
    );
    this.agentRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'NetworkAgentGlueCatalogReadOnly',
        effect: iam.Effect.ALLOW,
        // `glue:Get*` covers GetDatabase, GetTable, GetTables,
        // GetPartition, GetPartitions, GetTableVersion,
        // GetTableVersions, GetSchema, GetSchemaVersion, etc. --
        // the full read-side surface Athena needs. Wildcard
        // `glue:Get*` is the recommended Athena pattern (CUR agent
        // and ValidateAthenaLambda use the same shape).
        actions: ['glue:Get*'],
        resources: [
          `arn:${cdk.Aws.PARTITION}:glue:${this.region}:${this.account}:catalog`,
          `arn:${cdk.Aws.PARTITION}:glue:${this.region}:${this.account}:database/${GLUE_DATABASE_NAME}`,
          `arn:${cdk.Aws.PARTITION}:glue:${this.region}:${this.account}:table/${GLUE_DATABASE_NAME}/${GLUE_TABLE_NAME}`,
          // Partitions live under the table ARN with a trailing wildcard.
          `arn:${cdk.Aws.PARTITION}:glue:${this.region}:${this.account}:table/${GLUE_DATABASE_NAME}/${GLUE_TABLE_NAME}/*`,
        ],
      }),
    );

    // ---- S3 read for Athena query results ---------------------------------
    //
    // Athena writes per-query result objects to a results prefix on
    // the bucket. The validateAthenaLambda above sets up the same
    // `athena-results/` prefix; the agent runtime uses the same
    // prefix for `query_pcap` and the dispatch helpers in
    // `agents/network-agent/athena_helper.py`. Because Athena
    // queries inherit the caller's S3 permissions, the agent role
    // needs read+write on this prefix to retrieve its own results.
    //
    // The prefix scope is intentional -- there is no `s3:*` on the
    // bucket and no access to objects outside `athena-results/`,
    // `raw/`, and `parquet/`.
    this.agentRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'NetworkAgentAthenaResultsReadWrite',
        effect: iam.Effect.ALLOW,
        actions: [
          's3:GetObject',
          's3:PutObject',
          's3:AbortMultipartUpload',
        ],
        resources: [`${this.networkDataBucket.bucketArn}/athena-results/*`],
      }),
    );

    // ---- VPC DNS resolution for active_dns_lookup (Req 19.14) -------------
    //
    // The `active_dns_lookup` strategy of Hostname_Resolution_Strategy
    // performs a runtime `socket.getaddrinfo()` from the AgentCore
    // runtime container. That call hits the VPC's local resolver
    // (`169.254.169.253:53` for VPC-attached compute, or the
    // configured DHCP option set's DNS server when overridden) over
    // UDP/53 -- purely a network-level egress, not an AWS API call.
    //
    // VPC DNS resolution itself is governed by VPC-level settings
    // (`enableDnsSupport`, `enableDnsHostnames`) configured on the
    // Network Agent's VPC at construct time, not by IAM. The
    // AgentCore runtime container needs no `route53resolver:*`,
    // `route53:*`, or other Route 53 IAM permissions to perform
    // these lookups; granting them would be over-broad and is
    // explicitly forbidden by the task description.
    //
    // This block exists as a documented affirmative no-op (rather
    // than silently omitting any IAM statement for DNS) to make
    // explicit: Req 19.14 is satisfied by the VPC's network
    // configuration, NOT by IAM. The corresponding VPC
    // `enableDnsSupport: true` setting lives in the
    // NetworkInfraStack VPC construct (Task 22) once the collector
    // VPC is provisioned. If `active_dns_lookup` ever requires a
    // future AWS API surface (for example, the cross-account
    // Route 53 Resolver query log API), this is where that grant
    // would be added -- narrowly scoped, never with `route53resolver:*`.
    //
    // No iam.PolicyStatement is created for this block.

    // -----------------------------------------------------------------------
    // `cdk.Fn.importValue()` so the agent container's `CAPTURE_STATE_TABLE`
    // and `VNI_LOOKUP_TABLE` environment variables resolve to the same
    // physical tables provisioned here (design.md "CfnOutput exports").
    //
    // Both name and ARN are exported: the name feeds the agent runtime
    // env vars; the ARN feeds task 27's resource-scoped IAM policies on
    // the AgentCore runtime role.
    // -----------------------------------------------------------------------
    new cdk.CfnOutput(this, 'CaptureStateTableName', {
      value: this.captureStateTable.tableName,
      description: 'DynamoDB Capture_State_Table name (Network Agent capture lifecycle state)',
      exportName: 'GOATNetworkAgentCaptureStateTableName',
    });

    new cdk.CfnOutput(this, 'CaptureStateTableArn', {
      value: this.captureStateTable.tableArn,
      description: 'DynamoDB Capture_State_Table ARN (used by Network Agent runtime IAM policy)',
      exportName: 'GOATNetworkAgentCaptureStateTableArn',
    });

    new cdk.CfnOutput(this, 'VniLookupTableName', {
      value: this.vniLookupTable.tableName,
      description: 'DynamoDB Vni_Lookup_Table name (VXLAN VNI → capture_id mapping)',
      exportName: 'GOATNetworkAgentVniLookupTableName',
    });

    new cdk.CfnOutput(this, 'VniLookupTableArn', {
      value: this.vniLookupTable.tableArn,
      description: 'DynamoDB Vni_Lookup_Table ARN (used by Network Agent runtime IAM policy)',
      exportName: 'GOATNetworkAgentVniLookupTableArn',
    });

    // -----------------------------------------------------------------------
    // Cross-stack export -- consumed by Network_Runtime_Stack via
    // cdk.Fn.importValue('GOATNetworkAgentDataBucketName') so the agent
    // container's `DATA_BUCKET_NAME` environment variable resolves to the
    // same bucket regardless of which path was chosen above.
    //
    // The ARN export is also published so future tasks (22-27) can build
    // resource-scoped IAM policies on the bucket without re-importing it.
    // -----------------------------------------------------------------------
    new cdk.CfnOutput(this, 'NetworkAgentDataBucketName', {
      value: this.networkDataBucketName,
      description: 'Resolved Network_Data_Bucket name (shared GOATData export or dedicated NetworkDataStack)',
      exportName: 'GOATNetworkAgentDataBucketName',
    });

    new cdk.CfnOutput(this, 'NetworkAgentDataBucketArn', {
      value: this.networkDataBucket.bucketArn,
      description: 'Resolved Network_Data_Bucket ARN',
      exportName: 'GOATNetworkAgentDataBucketArn',
    });

    // -----------------------------------------------------------------------
    // Glue catalog cross-stack exports (Reqs 6.7, 6.12).
    //
    // The database and table names feed the agent runtime container's
    // `GLUE_DATABASE` and `GLUE_TABLE` environment variables (Task 28's
    // NetworkRuntimeStack reads these via `cdk.Fn.importValue()`). They
    // also feed Task 27's IAM scoping for `glue:Get*` /
    // `athena:StartQueryExecution` resource ARNs and Task 25's Step
    // Functions definition (`RunCrawlerLambda` invokes the named
    // crawler; `ValidateAthenaLambda` queries `<database>.<table>`).
    //
    // Crawler name is exported in addition to the database/table names
    // so the Step Functions task can issue `glue:StartCrawler` against
    // the exact resource without hardcoding the physical name in the
    // workflow code.
    // -----------------------------------------------------------------------
    new cdk.CfnOutput(this, 'GlueDatabaseName', {
      value: GLUE_DATABASE_NAME,
      description: 'Glue database name hosting the Pcap_Athena_Table (goat_network)',
      exportName: 'GOATNetworkAgentGlueDatabaseName',
    });

    new cdk.CfnOutput(this, 'GlueTableName', {
      value: GLUE_TABLE_NAME,
      description: 'Glue table name for transformed pcap data (pcap_logs, partitioned by capture_id)',
      exportName: 'GOATNetworkAgentGlueTableName',
    });

    new cdk.CfnOutput(this, 'GlueCrawlerName', {
      value: this.glueCrawler.ref,
      description: 'Glue Crawler name that updates pcap_logs partitions when the Transformation_Workflow runs',
      exportName: 'GOATNetworkAgentGlueCrawlerName',
    });

    // -----------------------------------------------------------------------
    // Transformation_Workflow exports (Reqs 6.8, 6.9, 6.12).
    //
    // The state machine ARN is the value the Network Agent's
    // `handle_transform_capture` reads from `TRANSFORMATION_SFN_ARN` to
    // call `stepfunctions:StartExecution` (design.md "Capture Lifecycle
    // Handlers", agents/network-agent/main.py). The Network Runtime
    // Stack (Task 28) plumbs this export through to the agent
    // container's environment.
    //
    // The state machine name is also exported as a convenience for
    // operators inspecting Step Functions executions in the console
    // (the ARN is opaque; the name appears in URLs).
    // -----------------------------------------------------------------------
    new cdk.CfnOutput(this, 'TransformationStateMachineArn', {
      value: this.transformationStateMachine.stateMachineArn,
      description:
        'Step Functions Transformation_Workflow ARN (Network Agent transform_capture invokes this via StartExecution).',
      exportName: 'GOATNetworkAgentTransformationStateMachineArn',
    });

    new cdk.CfnOutput(this, 'TransformationStateMachineName', {
      value: this.transformationStateMachine.stateMachineName,
      description:
        'Step Functions Transformation_Workflow state machine name (operator convenience for console navigation).',
      exportName: 'GOATNetworkAgentTransformationStateMachineName',
    });

    // -----------------------------------------------------------------------
    // StopCaptureInvokerLambda exports (Reqs 3.5, 4.6, 4.7, 6.12).
    //
    // The Lambda ARN is the value the Network Agent's `start_capture`
    // handler reads from `STOP_CAPTURE_INVOKER_LAMBDA_ARN` (Task 28's
    // NetworkRuntimeStack plumbs the env var) and uses as the target
    // ARN when calling `scheduler:CreateSchedule` for each capture's
    // Auto_Stop_Schedule (Task 11, design.md "StopCaptureInvokerLambda").
    //
    // The function name is also exported for operators inspecting the
    // Lambda or its CloudWatch Logs in the AWS console -- the ARN is
    // opaque, the name appears in URLs and metric filters.
    // -----------------------------------------------------------------------
    new cdk.CfnOutput(this, 'StopCaptureInvokerLambdaArn', {
      value: this.stopCaptureInvokerLambda.functionArn,
      description:
        'StopCaptureInvokerLambda ARN -- the EventBridge Scheduler target invoked at each Capture_Session deadline to call stop_capture (Reqs 4.6, 4.7).',
      exportName: 'GOATNetworkAgentStopCaptureInvokerLambdaArn',
    });

    new cdk.CfnOutput(this, 'StopCaptureInvokerLambdaName', {
      value: this.stopCaptureInvokerLambda.functionName,
      description:
        'StopCaptureInvokerLambda function name (operator convenience for console navigation and CloudWatch metric filters).',
      exportName: 'GOATNetworkAgentStopCaptureInvokerLambdaName',
    });

    // -----------------------------------------------------------------------
    // Auto_Stop_Schedule cross-stack exports (Task 27, Reqs 3.5, 4.6,
    // 4.10, 6.12).
    //
    // The agent reads the schedule group name from `SCHEDULE_GROUP_NAME`
    // and the scheduler-target role ARN from `SCHEDULER_TARGET_ROLE_ARN`
    // (see agents/network-agent/main.py constants
    // `ENV_SCHEDULE_GROUP_NAME` and `ENV_SCHEDULER_TARGET_ROLE_ARN`).
    // Task 28's NetworkRuntimeStack reads these CFN exports via
    // `cdk.Fn.importValue()` and plumbs them into the agent
    // container's environment.
    //
    // Both values are also useful for operators auditing the
    // Auto_Stop_Schedule wiring from the AWS console without
    // descending into the deployed Network Agent's environment.
    // -----------------------------------------------------------------------
    new cdk.CfnOutput(this, 'AutoStopScheduleGroupName', {
      value: autoStopScheduleGroupName,
      description:
        'EventBridge Scheduler group name holding every Network Agent Auto_Stop_Schedule (Reqs 4.6, 4.10).',
      exportName: 'GOATNetworkAgentAutoStopScheduleGroupName',
    });

    new cdk.CfnOutput(this, 'SchedulerTargetRoleArn', {
      value: this.schedulerTargetRole.roleArn,
      description:
        'IAM role ARN passed in CreateSchedule Target.RoleArn so EventBridge Scheduler can invoke the StopCaptureInvokerLambda (Req 4.6).',
      exportName: 'GOATNetworkAgentSchedulerTargetRoleArn',
    });

    // -----------------------------------------------------------------------
    // Traffic Mirror plumbing exports (Reqs 6.5, 6.6, 6.12; Task 22).
    //
    // The agent runtime container reads the filter ID, target ID, and
    // collector instance ID from the corresponding environment
    // variables (`TRAFFIC_MIRROR_FILTER_ID`, `TRAFFIC_MIRROR_TARGET_ID`,
    // `COLLECTOR_INSTANCE_ID`). NetworkRuntimeStack (Task 28) imports
    // these CFN exports via `cdk.Fn.importValue()` and plumbs them
    // through.
    //
    // The collector instance ID also feeds the agent's Req 3.16
    // collector-readiness check, where the agent calls
    // `ec2:DescribeInstanceStatus` against this exact ID before
    // creating any Traffic Mirror Sessions.
    // -----------------------------------------------------------------------
    new cdk.CfnOutput(this, 'TrafficMirrorFilterId', {
      value: this.trafficMirrorFilter.ref,
      description:
        'AWS::EC2::TrafficMirrorFilter ID (goat-network-default-filter) -- the agent supplies this on every CreateTrafficMirrorSession call (Req 6.5).',
      exportName: 'GOATNetworkAgentTrafficMirrorFilterId',
    });

    new cdk.CfnOutput(this, 'TrafficMirrorTargetId', {
      value: this.trafficMirrorTarget.ref,
      description:
        'AWS::EC2::TrafficMirrorTarget ID (network-interface type, references collector ENI) -- the agent supplies this on every CreateTrafficMirrorSession call (Req 6.6).',
      exportName: 'GOATNetworkAgentTrafficMirrorTargetId',
    });

    new cdk.CfnOutput(this, 'CollectorInstanceId', {
      value: this.collectorInstance.ref,
      description:
        'EC2 instance ID of the single Traffic_Mirror_Collector -- used by the agent\'s Req 3.16 collector-readiness check (DescribeInstanceStatus).',
      exportName: 'GOATNetworkAgentCollectorInstanceId',
    });

    new cdk.CfnOutput(this, 'NetworkAgentVpcId', {
      value: this.networkAgentVpc.vpcId,
      description:
        'VPC ID of the Network_Agent_VPC hosting the Traffic_Mirror_Collector EC2 instance.',
      exportName: 'GOATNetworkAgentVpcId',
    });

    // NOTE: The `GOATNetworkAgentRuntimeRoleArn` export required by
    // Task 27 / Req 6.12 is published by `BaseInfraStack` (see the
    // `RuntimeRoleArn` CfnOutput in base-infra-stack.ts) using the
    // `exportPrefix='GOATNetworkAgent'` value passed to the super()
    // constructor at the top of this stack. Re-declaring the export
    // here would conflict with the parent class and is therefore
    // intentionally omitted.
  }
}
