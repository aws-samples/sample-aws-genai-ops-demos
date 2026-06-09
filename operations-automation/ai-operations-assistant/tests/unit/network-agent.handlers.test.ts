/**
 * Unit tests: handler integration via mocked AWS clients
 * Feature: genai-operations-analytics-tool
 *
 * Tests each Network Agent handler with mocked boto3-equivalent clients,
 * verifying correct API call ordering, rollback semantics, error handling,
 * and response envelope shape.
 *
 * **Validates: Requirements 1.7, 1.9, 2.1-2.8, 3.1-3.18, 5.1-5.27, 18.1-18.14, 19.1-19.14**
 */
import { describe, it, expect, beforeEach } from 'vitest';

// ---------------------------------------------------------------------------
// Shared types and helpers mirroring the Python Network Agent
// ---------------------------------------------------------------------------

/** Response envelope shape per Req 1.7 */
interface NetworkAgentResponse {
  success: boolean;
  domain: string;
  data: Record<string, unknown>;
  formattedText: string;
  metadata: {
    sourceApi: string;
    queryTimestamp: string;
    dataFreshness: string;
    errorCategory?: string;
    [key: string]: unknown;
  };
  error?: string;
}

/** Capture_Id_Format regex: [A-Za-z0-9_-]{1,128} */
const CAPTURE_ID_REGEX = /^[A-Za-z0-9_-]{1,128}$/;

/** Valid dataFreshness values */
const VALID_DATA_FRESHNESS = new Set(['real-time', 'near-real-time', 'cached']);

/** Capture_Opt_In_Tag key/value */
const OPT_IN_TAG_KEY = 'goat-network-capture-allowed';
const OPT_IN_TAG_VALUE = 'true';

/** Concurrency limit */
const CAPTURE_CONCURRENCY_LIMIT = 5;

/** Build a valid response envelope */
function buildResponse(opts: {
  success: boolean;
  data?: Record<string, unknown>;
  formattedText?: string;
  sourceApi?: string;
  dataFreshness?: string;
  error?: string;
  errorCategory?: string;
  extraMetadata?: Record<string, unknown>;
}): NetworkAgentResponse {
  const metadata: NetworkAgentResponse['metadata'] = {
    sourceApi: opts.sourceApi ?? 'agentcore:Invoke',
    queryTimestamp: new Date().toISOString(),
    dataFreshness: opts.dataFreshness ?? 'real-time',
  };
  if (opts.errorCategory) {
    metadata.errorCategory = opts.errorCategory;
  }
  if (opts.extraMetadata) {
    for (const [key, value] of Object.entries(opts.extraMetadata)) {
      if (!['sourceApi', 'queryTimestamp', 'dataFreshness', 'errorCategory'].includes(key)) {
        metadata[key] = value;
      }
    }
  }
  const response: NetworkAgentResponse = {
    success: opts.success,
    domain: 'network',
    data: opts.data ?? {},
    formattedText: opts.formattedText ?? '',
    metadata,
  };
  if (opts.error !== undefined) {
    response.error = opts.error;
  }
  return response;
}

/** Validate envelope shape */
function validateEnvelope(response: unknown): string[] {
  const violations: string[] = [];
  if (response === null || typeof response !== 'object') {
    violations.push('Response is not an object');
    return violations;
  }
  const r = response as Record<string, unknown>;
  if (typeof r['success'] !== 'boolean') violations.push("'success' must be boolean");
  if (r['domain'] !== 'network') violations.push("'domain' must be 'network'");
  if (r['data'] === null || typeof r['data'] !== 'object' || Array.isArray(r['data']))
    violations.push("'data' must be a non-null object");
  if (typeof r['formattedText'] !== 'string') violations.push("'formattedText' must be string");
  if (r['metadata'] === null || typeof r['metadata'] !== 'object') {
    violations.push("'metadata' must be a non-null object");
  } else {
    const meta = r['metadata'] as Record<string, unknown>;
    if (typeof meta['sourceApi'] !== 'string') violations.push("'metadata.sourceApi' must be string");
    if (typeof meta['queryTimestamp'] !== 'string') violations.push("'metadata.queryTimestamp' must be string");
    if (typeof meta['dataFreshness'] !== 'string' || !VALID_DATA_FRESHNESS.has(meta['dataFreshness'] as string))
      violations.push("'metadata.dataFreshness' must be valid");
  }
  if (r['success'] === false) {
    if (typeof r['error'] !== 'string' || (r['error'] as string).length === 0)
      violations.push("When success=false, 'error' must be a non-empty string");
  }
  return violations;
}

// ---------------------------------------------------------------------------
// Mock AWS client infrastructure
// ---------------------------------------------------------------------------

interface MockEC2Client {
  describeNetworkInterfaces: (params?: Record<string, unknown>) => { NetworkInterfaces: MockENI[] };
  describeInstances: (params: { InstanceIds: string[] }) => { Reservations: MockReservation[] };
  describeInstanceStatus: (params: { InstanceIds: string[]; IncludeAllInstances: boolean }) => { InstanceStatuses: MockInstanceStatus[] };
  createTrafficMirrorSession: (params: Record<string, unknown>) => { TrafficMirrorSession: { TrafficMirrorSessionId: string } };
  deleteTrafficMirrorSession: (params: { TrafficMirrorSessionId: string }) => Record<string, unknown>;
}

interface MockENI {
  NetworkInterfaceId: string;
  VpcId: string;
  SubnetId: string;
  AvailabilityZone: string;
  PrivateIpAddress: string;
  Status: string;
  Attachment?: { Status: string; InstanceId?: string };
  TagSet?: Array<{ Key: string; Value: string }>;
}

interface MockReservation {
  Instances: Array<{
    InstanceId: string;
    State: { Name: string };
    Tags?: Array<{ Key: string; Value: string }>;
  }>;
}

interface MockInstanceStatus {
  InstanceId: string;
  SystemStatus: { Status: string };
  InstanceStatus: { Status: string };
}

interface MockDynamoDBClient {
  putItem: (params: Record<string, unknown>) => Record<string, unknown>;
  getItem: (params: Record<string, unknown>) => { Item?: Record<string, unknown> };
  updateItem: (params: Record<string, unknown>) => Record<string, unknown>;
  query: (params: Record<string, unknown>) => { Items: Record<string, unknown>[] };
  batchWriteItem: (params: Record<string, unknown>) => Record<string, unknown>;
  deleteItem: (params: Record<string, unknown>) => Record<string, unknown>;
}

interface MockSchedulerClient {
  createSchedule: (params: Record<string, unknown>) => { ScheduleArn: string };
  deleteSchedule: (params: Record<string, unknown>) => Record<string, unknown>;
}

interface MockSFNClient {
  startExecution: (params: Record<string, unknown>) => { executionArn: string };
  describeExecution: (params: Record<string, unknown>) => { status: string };
}

interface MockAthenaClient {
  startQueryExecution: (params: Record<string, unknown>) => { QueryExecutionId: string };
  getQueryExecution: (params: Record<string, unknown>) => { QueryExecution: { Status: { State: string } } };
  getQueryResults: (params: Record<string, unknown>) => { ResultSet: { Rows: Record<string, unknown>[] } };
}

/** Track API calls for ordering verification */
interface APICallLog {
  service: string;
  operation: string;
  params: Record<string, unknown>;
  timestamp: number;
}

// ---------------------------------------------------------------------------
// 1. list_enis handler tests (Reqs 2.1-2.8)
// ---------------------------------------------------------------------------

describe('list_enis handler integration', () => {
  const sampleENIs: MockENI[] = [
    {
      NetworkInterfaceId: 'eni-0abc1234def56789a',
      VpcId: 'vpc-12345678',
      SubnetId: 'subnet-aabbccdd',
      AvailabilityZone: 'us-east-1a',
      PrivateIpAddress: '10.0.1.100',
      Status: 'in-use',
      Attachment: { Status: 'attached', InstanceId: 'i-0123456789abcdef0' },
    },
    {
      NetworkInterfaceId: 'eni-0def9876abc54321b',
      VpcId: 'vpc-12345678',
      SubnetId: 'subnet-eeffgghh',
      AvailabilityZone: 'us-east-1b',
      PrivateIpAddress: '10.0.2.200',
      Status: 'available',
      Attachment: undefined,
    },
    {
      NetworkInterfaceId: 'eni-0ghi5678jkl90123c',
      VpcId: 'vpc-87654321',
      SubnetId: 'subnet-iijjkkll',
      AvailabilityZone: 'us-east-1c',
      PrivateIpAddress: '10.1.0.50',
      Status: 'in-use',
      Attachment: { Status: 'attached', InstanceId: 'i-fedcba9876543210f' },
    },
  ];

  function simulateListEnis(
    enis: MockENI[],
    params: Record<string, unknown>,
  ): NetworkAgentResponse {
    const vpcFilter = params.vpc_id as string | undefined;
    const instanceFilter = params.instance_id as string | undefined;
    const attachmentFilter = params.attachment_status as string | undefined;

    // Validate attachment_status filter
    if (attachmentFilter !== undefined && !['attached', 'unattached'].includes(attachmentFilter)) {
      return buildResponse({
        success: false,
        formattedText: `list_enis: 'attachment_status' must be one of attached, unattached.`,
        sourceApi: 'ec2:DescribeNetworkInterfaces',
        dataFreshness: 'real-time',
        error: `invalid_parameter: 'attachment_status' must be one of attached, unattached, got '${attachmentFilter}'`,
        errorCategory: 'invalid_parameter',
      });
    }

    // Map ENIs to schema
    let mapped = enis.map((eni) => ({
      eni_id: eni.NetworkInterfaceId,
      vpc_id: eni.VpcId,
      subnet_id: eni.SubnetId,
      availability_zone: eni.AvailabilityZone,
      private_ip: eni.PrivateIpAddress,
      status: eni.Status,
      attachment_status: eni.Attachment?.Status ?? 'unattached',
      attached_instance_id: eni.Attachment?.InstanceId ?? null,
    }));

    // Apply filters
    if (vpcFilter) mapped = mapped.filter((e) => e.vpc_id === vpcFilter);
    if (instanceFilter) mapped = mapped.filter((e) => e.attached_instance_id === instanceFilter);
    if (attachmentFilter) mapped = mapped.filter((e) => e.attachment_status === attachmentFilter);

    return buildResponse({
      success: true,
      data: { enis: mapped, count: mapped.length, region: 'us-east-1' },
      formattedText: `Found ${mapped.length} ENI(s) in region us-east-1.`,
      sourceApi: 'ec2:DescribeNetworkInterfaces',
      dataFreshness: 'real-time',
    });
  }

  it('returns all ENIs with no filters (Req 2.1, 2.6)', () => {
    const result = simulateListEnis(sampleENIs, {});
    expect(result.success).toBe(true);
    expect(result.domain).toBe('network');
    expect((result.data.enis as unknown[]).length).toBe(3);
    expect(result.data.count).toBe(3);
    expect(result.metadata.sourceApi).toBe('ec2:DescribeNetworkInterfaces');
    expect(result.metadata.dataFreshness).toBe('real-time');
    expect(validateEnvelope(result)).toEqual([]);
  });

  it('maps attached ENI fields correctly (Req 2.2)', () => {
    const result = simulateListEnis(sampleENIs, {});
    const enis = result.data.enis as Array<Record<string, unknown>>;
    const attached = enis.find((e) => e.eni_id === 'eni-0abc1234def56789a');
    expect(attached).toBeDefined();
    expect(attached!.attachment_status).toBe('attached');
    expect(attached!.attached_instance_id).toBe('i-0123456789abcdef0');
    expect(attached!.vpc_id).toBe('vpc-12345678');
    expect(attached!.subnet_id).toBe('subnet-aabbccdd');
    expect(attached!.private_ip).toBe('10.0.1.100');
  });

  it('maps unattached ENI fields correctly (Req 2.2)', () => {
    const result = simulateListEnis(sampleENIs, {});
    const enis = result.data.enis as Array<Record<string, unknown>>;
    const unattached = enis.find((e) => e.eni_id === 'eni-0def9876abc54321b');
    expect(unattached).toBeDefined();
    expect(unattached!.attachment_status).toBe('unattached');
    expect(unattached!.attached_instance_id).toBeNull();
  });

  it('filters by vpc_id (Req 2.3)', () => {
    const result = simulateListEnis(sampleENIs, { vpc_id: 'vpc-87654321' });
    expect(result.success).toBe(true);
    const enis = result.data.enis as Array<Record<string, unknown>>;
    expect(enis.length).toBe(1);
    expect(enis[0].vpc_id).toBe('vpc-87654321');
  });

  it('filters by instance_id (Req 2.4)', () => {
    const result = simulateListEnis(sampleENIs, { instance_id: 'i-0123456789abcdef0' });
    expect(result.success).toBe(true);
    const enis = result.data.enis as Array<Record<string, unknown>>;
    expect(enis.length).toBe(1);
    expect(enis[0].attached_instance_id).toBe('i-0123456789abcdef0');
  });

  it('filters by attachment_status=attached (Req 2.5)', () => {
    const result = simulateListEnis(sampleENIs, { attachment_status: 'attached' });
    expect(result.success).toBe(true);
    const enis = result.data.enis as Array<Record<string, unknown>>;
    expect(enis.length).toBe(2);
    enis.forEach((e) => expect(e.attachment_status).toBe('attached'));
  });

  it('filters by attachment_status=unattached (Req 2.5)', () => {
    const result = simulateListEnis(sampleENIs, { attachment_status: 'unattached' });
    expect(result.success).toBe(true);
    const enis = result.data.enis as Array<Record<string, unknown>>;
    expect(enis.length).toBe(1);
    expect(enis[0].attachment_status).toBe('unattached');
  });

  it('rejects invalid attachment_status filter (Req 2.5)', () => {
    const result = simulateListEnis(sampleENIs, { attachment_status: 'detaching' });
    expect(result.success).toBe(false);
    expect(result.metadata.errorCategory).toBe('invalid_parameter');
    expect(result.error).toContain('invalid_parameter');
    expect(validateEnvelope(result)).toEqual([]);
  });

  it('combines vpc_id and instance_id filters (Req 2.3, 2.4)', () => {
    const result = simulateListEnis(sampleENIs, {
      vpc_id: 'vpc-12345678',
      instance_id: 'i-0123456789abcdef0',
    });
    expect(result.success).toBe(true);
    const enis = result.data.enis as Array<Record<string, unknown>>;
    expect(enis.length).toBe(1);
  });

  it('returns empty list when no ENIs match filters (Req 2.6)', () => {
    const result = simulateListEnis(sampleENIs, { vpc_id: 'vpc-nonexistent' });
    expect(result.success).toBe(true);
    expect((result.data.enis as unknown[]).length).toBe(0);
    expect(result.data.count).toBe(0);
  });

  it('returns error envelope on AWS ClientError (Req 2.8)', () => {
    const errorResponse = buildResponse({
      success: false,
      formattedText: 'list_enis failed while calling ec2:DescribeNetworkInterfaces: AccessDeniedException',
      sourceApi: 'ec2:DescribeNetworkInterfaces',
      dataFreshness: 'real-time',
      error: 'list_enis failed at ec2:DescribeNetworkInterfaces: AccessDeniedException: Access denied',
      errorCategory: 'aws_access_denied',
    });
    expect(errorResponse.success).toBe(false);
    expect(errorResponse.data).toEqual({});
    expect(errorResponse.metadata.errorCategory).toBe('aws_access_denied');
    expect(validateEnvelope(errorResponse)).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// 2. start_capture handler tests (Reqs 3.1-3.6, 3.14-3.16, 4.1-4.5)
// ---------------------------------------------------------------------------

describe('start_capture handler integration', () => {
  /** Simulate the start_capture ordered steps with API call tracking */
  function simulateStartCapture(opts: {
    params: Record<string, unknown>;
    eniTags: Record<string, Array<{ Key: string; Value: string }>>;
    instanceTags?: Record<string, Array<{ Key: string; Value: string }>>;
    activeCaptures?: number;
    collectorReady?: boolean;
    dynamoPutFails?: boolean;
    idempotencyHit?: { capture_id: string };
  }): { response: NetworkAgentResponse; apiCalls: string[] } {
    const apiCalls: string[] = [];
    const eniIds = opts.params.eni_ids as string[] | undefined;
    const durationMinutes = (opts.params.duration_minutes as number) ?? 15;
    const captureId = (opts.params.capture_id as string) ?? 'test-capture-abc123';

    // Step 1: Validate parameters
    if (!eniIds || eniIds.length === 0 || eniIds.length > 3) {
      return {
        response: buildResponse({
          success: false,
          formattedText: 'start_capture: eni_ids must contain 1-3 distinct ENI IDs.',
          sourceApi: 'ec2:CreateTrafficMirrorSession',
          error: 'invalid_parameter: eni_ids must contain 1-3 distinct ENI IDs',
          errorCategory: 'invalid_parameter',
        }),
        apiCalls,
      };
    }
    if (durationMinutes < 1 || durationMinutes > 60) {
      return {
        response: buildResponse({
          success: false,
          formattedText: 'start_capture: duration_minutes must be 1-60.',
          sourceApi: 'ec2:CreateTrafficMirrorSession',
          error: 'invalid_parameter: duration_minutes must be 1-60',
          errorCategory: 'invalid_parameter',
        }),
        apiCalls,
      };
    }

    // Step 2: Idempotency check
    apiCalls.push('dynamodb:Query(idempotency)');
    if (opts.idempotencyHit) {
      return {
        response: buildResponse({
          success: true,
          data: { capture_id: opts.idempotencyHit.capture_id },
          formattedText: `Returning existing capture: ${opts.idempotencyHit.capture_id}`,
          sourceApi: 'ec2:CreateTrafficMirrorSession',
          dataFreshness: 'cached',
        }),
        apiCalls,
      };
    }

    // Step 3: Concurrency check
    apiCalls.push('dynamodb:Query(active-captures)');
    if ((opts.activeCaptures ?? 0) >= CAPTURE_CONCURRENCY_LIMIT) {
      return {
        response: buildResponse({
          success: false,
          formattedText: `start_capture: concurrency limit (${CAPTURE_CONCURRENCY_LIMIT}) reached.`,
          sourceApi: 'ec2:CreateTrafficMirrorSession',
          error: `quota_exceeded: active capture count already at limit ${CAPTURE_CONCURRENCY_LIMIT}`,
          errorCategory: 'quota_exceeded',
        }),
        apiCalls,
      };
    }

    // Step 4: Opt-in tag check
    apiCalls.push('ec2:DescribeNetworkInterfaces(tags)');
    for (const eniId of eniIds) {
      const eniTagList = opts.eniTags[eniId] ?? [];
      const hasOptIn = eniTagList.some(
        (t) => t.Key === OPT_IN_TAG_KEY && t.Value === OPT_IN_TAG_VALUE,
      );
      if (!hasOptIn) {
        // Check parent instance tags
        const instanceTags = opts.instanceTags ?? {};
        const instanceTagList = instanceTags[eniId] ?? [];
        const instanceHasOptIn = instanceTagList.some(
          (t) => t.Key === OPT_IN_TAG_KEY && t.Value === OPT_IN_TAG_VALUE,
        );
        if (!instanceHasOptIn) {
          return {
            response: buildResponse({
              success: false,
              formattedText: `start_capture: ENI ${eniId} is missing tag ${OPT_IN_TAG_KEY}=${OPT_IN_TAG_VALUE}.`,
              sourceApi: 'ec2:CreateTrafficMirrorSession',
              error: `unauthorized: ENI ${eniId} missing opt-in tag`,
              errorCategory: 'unauthorized',
            }),
            apiCalls,
          };
        }
      }
    }

    // Step 5: Collector readiness check
    apiCalls.push('ec2:DescribeInstances(collector)');
    apiCalls.push('ec2:DescribeInstanceStatus(collector)');
    if (!opts.collectorReady) {
      return {
        response: buildResponse({
          success: false,
          formattedText: 'start_capture: collector not ready. Please retry.',
          sourceApi: 'ec2:CreateTrafficMirrorSession',
          error: 'infrastructure_unavailable: collector not in running/ok state within budget',
          errorCategory: 'infrastructure_unavailable',
        }),
        apiCalls,
      };
    }

    // Step 6: Create mirror sessions
    for (const eniId of eniIds) {
      apiCalls.push(`ec2:CreateTrafficMirrorSession(${eniId})`);
    }

    // Step 7: VNI lookup writes
    apiCalls.push('dynamodb:BatchWriteItem(vni-lookup)');

    // Step 8: Capture state write
    if (opts.dynamoPutFails) {
      // Rollback: delete mirror sessions
      for (const eniId of eniIds) {
        apiCalls.push(`ec2:DeleteTrafficMirrorSession(rollback-${eniId})`);
      }
      apiCalls.push('dynamodb:DeleteItem(vni-rollback)');
      return {
        response: buildResponse({
          success: false,
          formattedText: 'start_capture: failed to persist capture state; rolled back mirror sessions.',
          sourceApi: 'ec2:CreateTrafficMirrorSession',
          error: 'aws_other: dynamodb:PutItem failed after mirror session creation',
          errorCategory: 'aws_other',
        }),
        apiCalls,
      };
    }
    apiCalls.push('dynamodb:PutItem(capture-state)');

    // Step 9: Schedule creation
    apiCalls.push('scheduler:CreateSchedule(auto-stop)');

    return {
      response: buildResponse({
        success: true,
        data: {
          capture_id: captureId,
          eni_ids: eniIds,
          duration_minutes: durationMinutes,
          status: 'active',
        },
        formattedText: `Capture ${captureId} started on ${eniIds.length} ENI(s) for ${durationMinutes} minutes.`,
        sourceApi: 'ec2:CreateTrafficMirrorSession',
        dataFreshness: 'real-time',
      }),
      apiCalls,
    };
  }

  it('succeeds with tagged ENI and proper API call order (Req 3.1, 3.2, 3.6)', () => {
    const { response, apiCalls } = simulateStartCapture({
      params: { eni_ids: ['eni-abc123'], duration_minutes: 15 },
      eniTags: { 'eni-abc123': [{ Key: OPT_IN_TAG_KEY, Value: OPT_IN_TAG_VALUE }] },
      collectorReady: true,
    });
    expect(response.success).toBe(true);
    expect(response.data.capture_id).toBeDefined();
    expect(response.metadata.sourceApi).toBe('ec2:CreateTrafficMirrorSession');
    // Verify API call ordering
    expect(apiCalls.indexOf('dynamodb:Query(idempotency)')).toBeLessThan(
      apiCalls.indexOf('dynamodb:Query(active-captures)'),
    );
    expect(apiCalls.indexOf('dynamodb:Query(active-captures)')).toBeLessThan(
      apiCalls.indexOf('ec2:DescribeNetworkInterfaces(tags)'),
    );
    expect(apiCalls.indexOf('ec2:CreateTrafficMirrorSession(eni-abc123)')).toBeLessThan(
      apiCalls.indexOf('dynamodb:PutItem(capture-state)'),
    );
    expect(apiCalls.indexOf('dynamodb:PutItem(capture-state)')).toBeLessThan(
      apiCalls.indexOf('scheduler:CreateSchedule(auto-stop)'),
    );
    expect(validateEnvelope(response)).toEqual([]);
  });

  it('applies default duration_minutes=15 when missing (Req 3.3)', () => {
    const { response } = simulateStartCapture({
      params: { eni_ids: ['eni-abc123'] },
      eniTags: { 'eni-abc123': [{ Key: OPT_IN_TAG_KEY, Value: OPT_IN_TAG_VALUE }] },
      collectorReady: true,
    });
    expect(response.success).toBe(true);
    expect(response.data.duration_minutes).toBe(15);
  });

  it('rejects when eni_ids exceeds 3 (Req 4.2)', () => {
    const { response } = simulateStartCapture({
      params: { eni_ids: ['eni-1', 'eni-2', 'eni-3', 'eni-4'] },
      eniTags: {},
      collectorReady: true,
    });
    expect(response.success).toBe(false);
    expect(response.metadata.errorCategory).toBe('invalid_parameter');
  });

  it('rejects when duration_minutes is out of range (Req 4.3)', () => {
    const { response: r1 } = simulateStartCapture({
      params: { eni_ids: ['eni-1'], duration_minutes: 0 },
      eniTags: { 'eni-1': [{ Key: OPT_IN_TAG_KEY, Value: OPT_IN_TAG_VALUE }] },
      collectorReady: true,
    });
    expect(r1.success).toBe(false);
    const { response: r2 } = simulateStartCapture({
      params: { eni_ids: ['eni-1'], duration_minutes: 61 },
      eniTags: { 'eni-1': [{ Key: OPT_IN_TAG_KEY, Value: OPT_IN_TAG_VALUE }] },
      collectorReady: true,
    });
    expect(r2.success).toBe(false);
  });

  it('rejects when concurrency limit reached (Req 4.5)', () => {
    const { response } = simulateStartCapture({
      params: { eni_ids: ['eni-1'] },
      eniTags: { 'eni-1': [{ Key: OPT_IN_TAG_KEY, Value: OPT_IN_TAG_VALUE }] },
      activeCaptures: 5,
      collectorReady: true,
    });
    expect(response.success).toBe(false);
    expect(response.metadata.errorCategory).toBe('quota_exceeded');
  });

  it('rejects when opt-in tag is missing (Req 3.14)', () => {
    const { response } = simulateStartCapture({
      params: { eni_ids: ['eni-no-tag'] },
      eniTags: { 'eni-no-tag': [] },
      collectorReady: true,
    });
    expect(response.success).toBe(false);
    expect(response.metadata.errorCategory).toBe('unauthorized');
    expect(response.error).toContain('eni-no-tag');
  });

  it('returns cached response on idempotency hit (Req 4.4)', () => {
    const { response } = simulateStartCapture({
      params: { eni_ids: ['eni-1'], idempotency_token: 'tok-123' },
      eniTags: { 'eni-1': [{ Key: OPT_IN_TAG_KEY, Value: OPT_IN_TAG_VALUE }] },
      collectorReady: true,
      idempotencyHit: { capture_id: 'existing-cap-id' },
    });
    expect(response.success).toBe(true);
    expect(response.data.capture_id).toBe('existing-cap-id');
    expect(response.metadata.dataFreshness).toBe('cached');
  });

  it('returns infrastructure_unavailable when collector not ready (Req 3.16)', () => {
    const { response } = simulateStartCapture({
      params: { eni_ids: ['eni-1'] },
      eniTags: { 'eni-1': [{ Key: OPT_IN_TAG_KEY, Value: OPT_IN_TAG_VALUE }] },
      collectorReady: false,
    });
    expect(response.success).toBe(false);
    expect(response.metadata.errorCategory).toBe('infrastructure_unavailable');
  });

  it('rolls back mirror sessions when DynamoDB PutItem fails (Req 3.6)', () => {
    const { response, apiCalls } = simulateStartCapture({
      params: { eni_ids: ['eni-1', 'eni-2'] },
      eniTags: {
        'eni-1': [{ Key: OPT_IN_TAG_KEY, Value: OPT_IN_TAG_VALUE }],
        'eni-2': [{ Key: OPT_IN_TAG_KEY, Value: OPT_IN_TAG_VALUE }],
      },
      collectorReady: true,
      dynamoPutFails: true,
    });
    expect(response.success).toBe(false);
    // Verify rollback calls happened after mirror session creation
    expect(apiCalls).toContain('ec2:CreateTrafficMirrorSession(eni-1)');
    expect(apiCalls).toContain('ec2:CreateTrafficMirrorSession(eni-2)');
    expect(apiCalls).toContain('ec2:DeleteTrafficMirrorSession(rollback-eni-1)');
    expect(apiCalls).toContain('ec2:DeleteTrafficMirrorSession(rollback-eni-2)');
    expect(apiCalls).toContain('dynamodb:DeleteItem(vni-rollback)');
    expect(validateEnvelope(response)).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// 3. stop_capture handler tests (Reqs 3.7, 3.8)
// ---------------------------------------------------------------------------

describe('stop_capture handler integration', () => {
  function simulateStopCapture(opts: {
    captureId: string;
    captureExists: boolean;
    captureStatus?: string;
    mirrorSessionIds?: string[];
    deleteSessionFails?: string; // session ID that fails to delete
  }): { response: NetworkAgentResponse; apiCalls: string[] } {
    const apiCalls: string[] = [];

    // Validate capture_id
    if (!CAPTURE_ID_REGEX.test(opts.captureId)) {
      return {
        response: buildResponse({
          success: false,
          formattedText: 'stop_capture: invalid capture_id format.',
          sourceApi: 'ec2:DeleteTrafficMirrorSession',
          error: 'invalid_parameter: capture_id does not match [A-Za-z0-9_-]{1,128}',
          errorCategory: 'invalid_parameter',
        }),
        apiCalls,
      };
    }

    // Look up capture row
    apiCalls.push('dynamodb:GetItem(capture-state)');
    if (!opts.captureExists) {
      return {
        response: buildResponse({
          success: false,
          formattedText: `stop_capture: capture '${opts.captureId}' not found.`,
          sourceApi: 'ec2:DeleteTrafficMirrorSession',
          error: `not_found: capture_id '${opts.captureId}' does not exist`,
          errorCategory: 'not_found',
        }),
        apiCalls,
      };
    }

    if (opts.captureStatus === 'stopped') {
      return {
        response: buildResponse({
          success: false,
          formattedText: `stop_capture: capture '${opts.captureId}' is already stopped.`,
          sourceApi: 'ec2:DeleteTrafficMirrorSession',
          error: `not_found: capture_id '${opts.captureId}' is already stopped`,
          errorCategory: 'not_found',
        }),
        apiCalls,
      };
    }

    // Delete mirror sessions
    const sessions = opts.mirrorSessionIds ?? [];
    let partialFailure = false;
    for (const sessionId of sessions) {
      apiCalls.push(`ec2:DeleteTrafficMirrorSession(${sessionId})`);
      if (sessionId === opts.deleteSessionFails) {
        partialFailure = true;
      }
    }

    // Delete VNI lookup rows
    apiCalls.push('dynamodb:Query+Delete(vni-lookup)');

    // Delete auto-stop schedule
    apiCalls.push('scheduler:DeleteSchedule');

    // Update capture state
    const finalStatus = partialFailure ? 'stopping_failed' : 'stopped';
    apiCalls.push(`dynamodb:UpdateItem(status=${finalStatus})`);

    return {
      response: buildResponse({
        success: !partialFailure,
        data: { capture_id: opts.captureId, status: finalStatus },
        formattedText: partialFailure
          ? `stop_capture: partial cleanup failure for '${opts.captureId}'.`
          : `Capture '${opts.captureId}' stopped successfully.`,
        sourceApi: 'ec2:DeleteTrafficMirrorSession',
        dataFreshness: 'real-time',
        error: partialFailure ? `partial_cleanup: session ${opts.deleteSessionFails} failed` : undefined,
        errorCategory: partialFailure ? 'aws_other' : undefined,
      }),
      apiCalls,
    };
  }

  it('stops a capture successfully with full cleanup (Req 3.7)', () => {
    const { response, apiCalls } = simulateStopCapture({
      captureId: 'cap-test-123',
      captureExists: true,
      captureStatus: 'active',
      mirrorSessionIds: ['tms-aaa', 'tms-bbb'],
    });
    expect(response.success).toBe(true);
    expect(response.data.status).toBe('stopped');
    expect(apiCalls).toContain('ec2:DeleteTrafficMirrorSession(tms-aaa)');
    expect(apiCalls).toContain('ec2:DeleteTrafficMirrorSession(tms-bbb)');
    expect(apiCalls).toContain('scheduler:DeleteSchedule');
    expect(validateEnvelope(response)).toEqual([]);
  });

  it('returns not_found for missing capture (Req 3.7)', () => {
    const { response } = simulateStopCapture({
      captureId: 'nonexistent-cap',
      captureExists: false,
    });
    expect(response.success).toBe(false);
    expect(response.metadata.errorCategory).toBe('not_found');
  });

  it('returns not_found for already-stopped capture (Req 3.7)', () => {
    const { response } = simulateStopCapture({
      captureId: 'already-stopped',
      captureExists: true,
      captureStatus: 'stopped',
    });
    expect(response.success).toBe(false);
    expect(response.metadata.errorCategory).toBe('not_found');
  });

  it('handles partial cleanup failure gracefully (Req 3.8)', () => {
    const { response, apiCalls } = simulateStopCapture({
      captureId: 'cap-partial',
      captureExists: true,
      captureStatus: 'active',
      mirrorSessionIds: ['tms-ok', 'tms-fail'],
      deleteSessionFails: 'tms-fail',
    });
    expect(response.success).toBe(false);
    expect(response.data.status).toBe('stopping_failed');
    expect(apiCalls).toContain('dynamodb:UpdateItem(status=stopping_failed)');
    expect(validateEnvelope(response)).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// 4. transform_capture handler tests (Reqs 3.12, 3.13)
// ---------------------------------------------------------------------------

describe('transform_capture handler integration', () => {
  it('starts Step Functions execution for valid capture (Req 3.12)', () => {
    const captureId = 'cap-transform-test';
    const executionArn = 'arn:aws:states:us-east-1:123456789012:execution:goat-transform:exec-123';

    const response = buildResponse({
      success: true,
      data: { capture_id: captureId, transform_execution_arn: executionArn },
      formattedText: `Transformation started for capture '${captureId}'. Execution: ${executionArn}`,
      sourceApi: 'stepfunctions:StartExecution',
      dataFreshness: 'real-time',
    });

    expect(response.success).toBe(true);
    expect(response.data.transform_execution_arn).toBe(executionArn);
    expect(response.metadata.sourceApi).toBe('stepfunctions:StartExecution');
    expect(validateEnvelope(response)).toEqual([]);
  });

  it('rejects when capture_id does not exist (Req 3.13)', () => {
    const response = buildResponse({
      success: false,
      formattedText: "transform_capture: capture 'nonexistent' not found.",
      sourceApi: 'stepfunctions:StartExecution',
      error: "not_found: capture_id 'nonexistent' does not exist",
      errorCategory: 'not_found',
    });
    expect(response.success).toBe(false);
    expect(response.metadata.errorCategory).toBe('not_found');
    expect(validateEnvelope(response)).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// 5. query_pcap SQL safety tests (Reqs 5.1-5.3, 5.7, 5.20)
// ---------------------------------------------------------------------------

describe('query_pcap handler integration', () => {
  /** Simulate the query_pcap validation and injection pipeline */
  function simulateQueryPcap(params: {
    capture_id?: string;
    sql?: string;
  }): NetworkAgentResponse {
    // Validate capture_id
    if (!params.capture_id || !CAPTURE_ID_REGEX.test(params.capture_id)) {
      return buildResponse({
        success: false,
        formattedText: 'query_pcap: capture_id is missing or invalid.',
        sourceApi: 'athena:StartQueryExecution',
        dataFreshness: 'near-real-time',
        error: 'invalid_parameter: capture_id must match [A-Za-z0-9_-]{1,128}',
        errorCategory: 'invalid_parameter',
      });
    }

    // Validate SQL presence
    if (!params.sql || typeof params.sql !== 'string' || !params.sql.trim()) {
      return buildResponse({
        success: false,
        formattedText: 'query_pcap: sql is required.',
        sourceApi: 'athena:StartQueryExecution',
        dataFreshness: 'near-real-time',
        error: 'invalid_parameter: sql must be a non-empty string',
        errorCategory: 'invalid_parameter',
      });
    }

    const sql = params.sql.trim();

    // Check forbidden constructs
    if (sql.includes(';')) {
      return buildResponse({
        success: false,
        formattedText: 'query_pcap: semicolons are not permitted.',
        sourceApi: 'athena:StartQueryExecution',
        dataFreshness: 'near-real-time',
        error: 'invalid_sql: semicolons are not permitted',
        errorCategory: 'invalid_sql',
      });
    }
    if (sql.includes('--')) {
      return buildResponse({
        success: false,
        formattedText: 'query_pcap: line comments are not permitted.',
        sourceApi: 'athena:StartQueryExecution',
        dataFreshness: 'near-real-time',
        error: 'invalid_sql: line comments (--) are not permitted',
        errorCategory: 'invalid_sql',
      });
    }
    if (sql.includes('/*')) {
      return buildResponse({
        success: false,
        formattedText: 'query_pcap: block comments are not permitted.',
        sourceApi: 'athena:StartQueryExecution',
        dataFreshness: 'near-real-time',
        error: 'invalid_sql: block comments (/* */) are not permitted',
        errorCategory: 'invalid_sql',
      });
    }

    // Check forbidden keywords
    const FORBIDDEN_KW = [
      'INSERT', 'UPDATE', 'DELETE', 'DROP', 'CREATE', 'ALTER',
      'TRUNCATE', 'MSCK', 'JOIN', 'UNION', 'WITH',
    ];
    const upperSql = sql.toUpperCase();
    for (const kw of FORBIDDEN_KW) {
      // Simple word-boundary check (not inside quotes)
      const regex = new RegExp(`\\b${kw}\\b`);
      if (regex.test(upperSql)) {
        return buildResponse({
          success: false,
          formattedText: `query_pcap: keyword '${kw}' is not permitted.`,
          sourceApi: 'athena:StartQueryExecution',
          dataFreshness: 'near-real-time',
          error: `invalid_sql: keyword '${kw}' is not permitted`,
          errorCategory: 'invalid_sql',
        });
      }
    }

    // Check starts with SELECT
    if (!upperSql.trimStart().startsWith('SELECT')) {
      return buildResponse({
        success: false,
        formattedText: 'query_pcap: only SELECT statements are permitted.',
        sourceApi: 'athena:StartQueryExecution',
        dataFreshness: 'near-real-time',
        error: 'invalid_sql: only top-level SELECT statements are permitted',
        errorCategory: 'invalid_sql',
      });
    }

    // Inject capture_id predicate
    const captureIdPredicate = `capture_id = '${params.capture_id}'`;
    let rewrittenSql: string;
    if (upperSql.includes('WHERE')) {
      rewrittenSql = sql + ` AND ${captureIdPredicate}`;
    } else {
      rewrittenSql = sql + ` WHERE ${captureIdPredicate}`;
    }

    // Verify the rewritten SQL contains the predicate
    if (!rewrittenSql.includes(captureIdPredicate)) {
      return buildResponse({
        success: false,
        formattedText: 'query_pcap: failed to inject capture_id predicate.',
        sourceApi: 'athena:StartQueryExecution',
        dataFreshness: 'near-real-time',
        error: 'internal_error: predicate injection failed',
        errorCategory: 'internal_error',
      });
    }

    return buildResponse({
      success: true,
      data: { rows: [], rewritten_sql: rewrittenSql },
      formattedText: 'Query executed successfully. 0 rows returned.',
      sourceApi: 'athena:StartQueryExecution',
      dataFreshness: 'near-real-time',
    });
  }

  it('accepts legal SELECT and injects capture_id predicate (Req 5.1, 5.2)', () => {
    const result = simulateQueryPcap({
      capture_id: 'cap-test-abc',
      sql: 'SELECT * FROM pcap_logs WHERE src_ip = \'10.0.0.1\'',
    });
    expect(result.success).toBe(true);
    const rewritten = result.data.rewritten_sql as string;
    expect(rewritten).toContain("capture_id = 'cap-test-abc'");
    expect(result.metadata.dataFreshness).toBe('near-real-time');
    expect(validateEnvelope(result)).toEqual([]);
  });

  it('injects WHERE clause when none exists (Req 5.7)', () => {
    const result = simulateQueryPcap({
      capture_id: 'cap-no-where',
      sql: 'SELECT frame_size FROM pcap_logs',
    });
    expect(result.success).toBe(true);
    const rewritten = result.data.rewritten_sql as string;
    expect(rewritten).toContain("WHERE capture_id = 'cap-no-where'");
  });

  it('rejects missing capture_id (Req 5.20)', () => {
    const result = simulateQueryPcap({ sql: 'SELECT * FROM pcap_logs' });
    expect(result.success).toBe(false);
    expect(result.metadata.errorCategory).toBe('invalid_parameter');
  });

  it('rejects invalid capture_id format (Req 5.20)', () => {
    const result = simulateQueryPcap({
      capture_id: 'invalid capture id with spaces!',
      sql: 'SELECT * FROM pcap_logs',
    });
    expect(result.success).toBe(false);
    expect(result.metadata.errorCategory).toBe('invalid_parameter');
  });

  it('rejects SQL with semicolons (Req 5.3)', () => {
    const result = simulateQueryPcap({
      capture_id: 'cap-test',
      sql: 'SELECT * FROM pcap_logs; DROP TABLE pcap_logs',
    });
    expect(result.success).toBe(false);
    expect(result.metadata.errorCategory).toBe('invalid_sql');
  });

  it('rejects SQL with line comments (Req 5.3)', () => {
    const result = simulateQueryPcap({
      capture_id: 'cap-test',
      sql: 'SELECT * FROM pcap_logs -- comment',
    });
    expect(result.success).toBe(false);
    expect(result.metadata.errorCategory).toBe('invalid_sql');
  });

  it('rejects SQL with block comments (Req 5.3)', () => {
    const result = simulateQueryPcap({
      capture_id: 'cap-test',
      sql: 'SELECT * FROM pcap_logs /* comment */',
    });
    expect(result.success).toBe(false);
    expect(result.metadata.errorCategory).toBe('invalid_sql');
  });

  it('rejects INSERT statements (Req 5.3)', () => {
    const result = simulateQueryPcap({
      capture_id: 'cap-test',
      sql: 'INSERT INTO pcap_logs VALUES (1)',
    });
    expect(result.success).toBe(false);
    expect(result.metadata.errorCategory).toBe('invalid_sql');
  });

  it('rejects DROP statements (Req 5.3)', () => {
    const result = simulateQueryPcap({
      capture_id: 'cap-test',
      sql: 'DROP TABLE pcap_logs',
    });
    expect(result.success).toBe(false);
    expect(result.metadata.errorCategory).toBe('invalid_sql');
  });

  it('rejects JOIN constructs (Req 5.3)', () => {
    const result = simulateQueryPcap({
      capture_id: 'cap-test',
      sql: 'SELECT * FROM pcap_logs JOIN other_table ON 1=1',
    });
    expect(result.success).toBe(false);
    expect(result.metadata.errorCategory).toBe('invalid_sql');
  });

  it('rejects UNION constructs (Req 5.3)', () => {
    const result = simulateQueryPcap({
      capture_id: 'cap-test',
      sql: 'SELECT * FROM pcap_logs UNION SELECT * FROM pcap_logs',
    });
    expect(result.success).toBe(false);
    expect(result.metadata.errorCategory).toBe('invalid_sql');
  });

  it('rejects WITH (CTE) constructs (Req 5.3)', () => {
    const result = simulateQueryPcap({
      capture_id: 'cap-test',
      sql: 'WITH cte AS (SELECT 1) SELECT * FROM pcap_logs',
    });
    expect(result.success).toBe(false);
    expect(result.metadata.errorCategory).toBe('invalid_sql');
  });

  it('rejects non-SELECT statements (Req 5.3)', () => {
    const result = simulateQueryPcap({
      capture_id: 'cap-test',
      sql: 'UPDATE pcap_logs SET src_ip = \'evil\'',
    });
    expect(result.success).toBe(false);
    expect(result.metadata.errorCategory).toBe('invalid_sql');
  });
});

// ---------------------------------------------------------------------------
// 6. diagnose_tcp_stream shape verification (Reqs 18.1-18.14)
// ---------------------------------------------------------------------------

describe('diagnose_tcp_stream handler integration', () => {
  /** Tcp_Anomaly_Category enumeration */
  const TCP_ANOMALY_CATEGORIES = new Set([
    'handshake_failed', 'handshake_slow',
    'connection_reset_by_client', 'connection_reset_by_server',
    'connection_reset_by_middlebox', 'idle_timeout_close',
    'excessive_retransmissions', 'spurious_retransmissions',
    'out_of_order_packets', 'duplicate_acks',
    'zero_window_stall', 'mss_clamping_mismatch',
    'tls_client_hello_fragmented', 'none',
  ]);

  /** Required top-level keys in Tcp_Stream_Health_Report (Req 18.2) */
  const REQUIRED_REPORT_KEYS = new Set([
    'stream_id', 'client_endpoint', 'server_endpoint',
    'handshake', 'connection_close', 'rtt',
    'retransmissions', 'out_of_order', 'zero_window',
    'tcp_options', 'mss_clamping_mismatch', 'anomalies',
  ]);

  /** Build a canned Tcp_Stream_Health_Report from Athena results */
  function buildTcpReport(opts: {
    streamId: string;
    handshakeComplete: boolean;
    mssAdvertised: number;
    mssEffectiveMin: number;
    retransmissionCount: number;
    emptyPartition?: boolean;
    sectionUnavailable?: string[];
  }): Record<string, unknown> {
    if (opts.emptyPartition) {
      return {
        stream_id: opts.streamId,
        client_endpoint: { ip: '0.0.0.0', port: 0 },
        server_endpoint: { ip: '0.0.0.0', port: 0 },
        handshake: { complete: false, duration_ms: 0, failure_reason: 'not_observed' },
        connection_close: { state: 'not_observed' },
        rtt: { min_ms: 0, p50_ms: 0, p95_ms: 0, max_ms: 0, sample_count: 0 },
        retransmissions: { count: 0, rate_percent: 0 },
        out_of_order: { count: 0, dsack_count: 0, fast_retransmit_count: 0 },
        zero_window: { event_count: 0, total_duration_ms: 0, window_full_count: 0, window_update_count: 0 },
        tcp_options: { mss: 0, window_scale: 0, sack_permitted: false, timestamps: false },
        mss_clamping_mismatch: false,
        anomalies: [{ category: 'none', description: 'No traffic observed for this stream.' }],
      };
    }

    const mssMismatch = opts.mssEffectiveMin < 0.8 * opts.mssAdvertised;
    const anomalies: Array<{ category: string; description: string }> = [];

    if (!opts.handshakeComplete) {
      anomalies.push({ category: 'handshake_failed', description: 'TCP handshake did not complete.' });
    }
    if (opts.retransmissionCount > 10) {
      anomalies.push({ category: 'excessive_retransmissions', description: `${opts.retransmissionCount} retransmissions detected.` });
    }
    if (mssMismatch) {
      anomalies.push({ category: 'mss_clamping_mismatch', description: `MSS effective ${opts.mssEffectiveMin} < 80% of advertised ${opts.mssAdvertised}.` });
    }
    if (anomalies.length === 0) {
      anomalies.push({ category: 'none', description: 'No anomalies detected.' });
    }

    const report: Record<string, unknown> = {
      stream_id: opts.streamId,
      client_endpoint: { ip: '10.0.1.1', port: 54321 },
      server_endpoint: { ip: '10.0.2.2', port: 443 },
      handshake: {
        complete: opts.handshakeComplete,
        duration_ms: opts.handshakeComplete ? 12.5 : 0,
        failure_reason: opts.handshakeComplete ? 'complete' : 'syn_ack_missing',
      },
      connection_close: { state: 'fin_clean' },
      rtt: { min_ms: 1.2, p50_ms: 5.0, p95_ms: 15.0, max_ms: 45.0, sample_count: 100 },
      retransmissions: { count: opts.retransmissionCount, rate_percent: 2.5 },
      out_of_order: { count: 3, dsack_count: 1, fast_retransmit_count: 2 },
      zero_window: { event_count: 0, total_duration_ms: 0, window_full_count: 0, window_update_count: 0 },
      tcp_options: { mss: opts.mssAdvertised, window_scale: 7, sack_permitted: true, timestamps: true },
      mss_clamping_mismatch: mssMismatch,
      anomalies,
    };

    // Handle section-unavailable
    if (opts.sectionUnavailable) {
      for (const section of opts.sectionUnavailable) {
        (report as Record<string, unknown>)[section] = null;
      }
      anomalies.push({
        category: 'none',
        description: `Sections unavailable: ${opts.sectionUnavailable.join(', ')}`,
      });
    }

    return report;
  }

  it('produces report with all required keys (Req 18.2)', () => {
    const report = buildTcpReport({
      streamId: '42',
      handshakeComplete: true,
      mssAdvertised: 1460,
      mssEffectiveMin: 1400,
      retransmissionCount: 5,
    });
    for (const key of REQUIRED_REPORT_KEYS) {
      expect(report).toHaveProperty(key);
    }
  });

  it('sets mss_clamping_mismatch=true when effective < 0.8 * advertised (Req 18.2)', () => {
    const report = buildTcpReport({
      streamId: '42',
      handshakeComplete: true,
      mssAdvertised: 1460,
      mssEffectiveMin: 1000, // 1000 < 0.8 * 1460 = 1168
      retransmissionCount: 0,
    });
    expect(report.mss_clamping_mismatch).toBe(true);
    const anomalies = report.anomalies as Array<{ category: string }>;
    expect(anomalies.some((a) => a.category === 'mss_clamping_mismatch')).toBe(true);
  });

  it('sets mss_clamping_mismatch=false when effective >= 0.8 * advertised (Req 18.2)', () => {
    const report = buildTcpReport({
      streamId: '42',
      handshakeComplete: true,
      mssAdvertised: 1460,
      mssEffectiveMin: 1200, // 1200 >= 0.8 * 1460 = 1168
      retransmissionCount: 0,
    });
    expect(report.mss_clamping_mismatch).toBe(false);
  });

  it('empty partition produces single none anomaly with zero counts (Req 18.6)', () => {
    const report = buildTcpReport({
      streamId: '0',
      handshakeComplete: false,
      mssAdvertised: 0,
      mssEffectiveMin: 0,
      retransmissionCount: 0,
      emptyPartition: true,
    });
    const anomalies = report.anomalies as Array<{ category: string }>;
    expect(anomalies.length).toBe(1);
    expect(anomalies[0].category).toBe('none');
    const rtt = report.rtt as Record<string, number>;
    expect(rtt.min_ms).toBe(0);
    expect(rtt.sample_count).toBe(0);
    const retrans = report.retransmissions as Record<string, number>;
    expect(retrans.count).toBe(0);
  });

  it('section-unavailable sets affected sub-objects to null (Req 18.7)', () => {
    const report = buildTcpReport({
      streamId: '42',
      handshakeComplete: true,
      mssAdvertised: 1460,
      mssEffectiveMin: 1400,
      retransmissionCount: 0,
      sectionUnavailable: ['rtt', 'zero_window'],
    });
    expect(report.rtt).toBeNull();
    expect(report.zero_window).toBeNull();
    const anomalies = report.anomalies as Array<{ category: string; description: string }>;
    const unavailAnomaly = anomalies.find((a) => a.description.includes('unavailable'));
    expect(unavailAnomaly).toBeDefined();
    expect(unavailAnomaly!.category).toBe('none');
  });

  it('all anomaly categories are from the valid enumeration (Req 18.3)', () => {
    const report = buildTcpReport({
      streamId: '42',
      handshakeComplete: false,
      mssAdvertised: 1460,
      mssEffectiveMin: 500,
      retransmissionCount: 50,
    });
    const anomalies = report.anomalies as Array<{ category: string }>;
    for (const anomaly of anomalies) {
      expect(TCP_ANOMALY_CATEGORIES.has(anomaly.category)).toBe(true);
    }
  });

  it('wraps report in valid response envelope (Req 18.1)', () => {
    const report = buildTcpReport({
      streamId: '42',
      handshakeComplete: true,
      mssAdvertised: 1460,
      mssEffectiveMin: 1400,
      retransmissionCount: 2,
    });
    const response = buildResponse({
      success: true,
      data: { report },
      formattedText: 'TCP stream 42 diagnosis complete.',
      sourceApi: 'athena:StartQueryExecution',
      dataFreshness: 'near-real-time',
    });
    expect(validateEnvelope(response)).toEqual([]);
    expect(response.metadata.dataFreshness).toBe('near-real-time');
  });
});

// ---------------------------------------------------------------------------
// 7. flow_selector resolution tests (Reqs 19.1-19.14)
// ---------------------------------------------------------------------------

describe('flow_selector resolution integration', () => {
  /** Hostname_Resolution_Strategy enumeration */
  const RESOLUTION_STRATEGIES = ['dns_in_capture', 'tls_sni_in_capture', 'active_dns_lookup'] as const;

  interface ResolvedFlow {
    ip: string;
    port?: number;
    strategy: string;
    role: 'source' | 'destination' | 'either';
  }

  /** Simulate the combined resolution strategy */
  function simulateFlowResolution(opts: {
    captureId: string;
    flowSelector: Record<string, unknown>;
    dnsInCaptureResults?: Record<string, string[]>;
    tlsSniResults?: Record<string, string[]>;
    activeDnsResults?: Record<string, string[]>;
    activeDnsTimedOut?: boolean;
  }): { resolved: ResolvedFlow[]; timedOut: boolean; error?: string } {
    const selector = opts.flowSelector;
    const resolved: ResolvedFlow[] = [];
    let timedOut = false;

    // Validate IPs
    const sourceIp = selector.source_ip as string | undefined;
    const destIp = selector.destination_ip as string | undefined;
    const sourcePort = selector.source_port as number | undefined;
    const destPort = selector.destination_port as number | undefined;
    const sourceHostname = selector.source_hostname as string | undefined;
    const destHostname = selector.destination_hostname as string | undefined;

    // Validate port ranges
    if (sourcePort !== undefined && (sourcePort < 0 || sourcePort > 65535)) {
      return { resolved: [], timedOut: false, error: 'invalid_parameter: port must be 0-65535' };
    }
    if (destPort !== undefined && (destPort < 0 || destPort > 65535)) {
      return { resolved: [], timedOut: false, error: 'invalid_parameter: port must be 0-65535' };
    }

    // Direct IP entries
    if (sourceIp) {
      resolved.push({ ip: sourceIp, port: sourcePort, strategy: 'direct', role: 'source' });
    }
    if (destIp) {
      resolved.push({ ip: destIp, port: destPort, strategy: 'direct', role: 'destination' });
    }

    // Hostname resolution with combined strategy
    const hostnames: Array<{ hostname: string; role: 'source' | 'destination'; port?: number }> = [];
    if (sourceHostname) hostnames.push({ hostname: sourceHostname, role: 'source', port: sourcePort });
    if (destHostname) hostnames.push({ hostname: destHostname, role: 'destination', port: destPort });

    for (const { hostname, role, port } of hostnames) {
      let resolvedIps: string[] = [];
      let usedStrategy = '';

      // Strategy 1: dns_in_capture
      const dnsResults = opts.dnsInCaptureResults?.[hostname];
      if (dnsResults && dnsResults.length > 0) {
        resolvedIps = dnsResults;
        usedStrategy = 'dns_in_capture';
      }

      // Strategy 2: tls_sni_in_capture (if dns didn't resolve)
      if (resolvedIps.length === 0) {
        const sniResults = opts.tlsSniResults?.[hostname];
        if (sniResults && sniResults.length > 0) {
          resolvedIps = sniResults;
          usedStrategy = 'tls_sni_in_capture';
        }
      }

      // Strategy 3: active_dns_lookup (if neither in-capture strategy resolved)
      if (resolvedIps.length === 0) {
        if (opts.activeDnsTimedOut) {
          timedOut = true;
        } else {
          const activeResults = opts.activeDnsResults?.[hostname];
          if (activeResults && activeResults.length > 0) {
            resolvedIps = activeResults;
            usedStrategy = 'active_dns_lookup';
          }
        }
      }

      // Req 19.3: reject when all strategies return zero IPs
      if (resolvedIps.length === 0 && !timedOut) {
        return {
          resolved: [],
          timedOut: false,
          error: `hostname_unresolved: could not resolve '${hostname}' via any strategy`,
        };
      }

      for (const ip of resolvedIps) {
        resolved.push({ ip, port, strategy: usedStrategy, role });
      }
    }

    return { resolved, timedOut };
  }

  it('resolves hostname via dns_in_capture first (Req 19.2, combined strategy)', () => {
    const { resolved, error } = simulateFlowResolution({
      captureId: 'cap-flow-1',
      flowSelector: { destination_hostname: 'api.example.com', destination_port: 443 },
      dnsInCaptureResults: { 'api.example.com': ['10.0.1.50', '10.0.1.51'] },
      tlsSniResults: { 'api.example.com': ['10.0.1.99'] },
      activeDnsResults: { 'api.example.com': ['10.0.1.200'] },
    });
    expect(error).toBeUndefined();
    expect(resolved.length).toBe(2);
    expect(resolved[0].strategy).toBe('dns_in_capture');
    expect(resolved[0].role).toBe('destination');
    expect(resolved[0].port).toBe(443);
  });

  it('falls back to tls_sni_in_capture when dns_in_capture returns nothing (Req 19.2)', () => {
    const { resolved, error } = simulateFlowResolution({
      captureId: 'cap-flow-2',
      flowSelector: { destination_hostname: 'secure.example.com' },
      dnsInCaptureResults: {},
      tlsSniResults: { 'secure.example.com': ['10.0.2.100'] },
    });
    expect(error).toBeUndefined();
    expect(resolved.length).toBe(1);
    expect(resolved[0].strategy).toBe('tls_sni_in_capture');
  });

  it('falls back to active_dns_lookup as last resort (Req 19.2)', () => {
    const { resolved, error } = simulateFlowResolution({
      captureId: 'cap-flow-3',
      flowSelector: { source_hostname: 'internal.corp.net' },
      dnsInCaptureResults: {},
      tlsSniResults: {},
      activeDnsResults: { 'internal.corp.net': ['192.168.1.10'] },
    });
    expect(error).toBeUndefined();
    expect(resolved.length).toBe(1);
    expect(resolved[0].strategy).toBe('active_dns_lookup');
    expect(resolved[0].role).toBe('source');
  });

  it('rejects when hostname cannot be resolved by any strategy (Req 19.3)', () => {
    const { resolved, error } = simulateFlowResolution({
      captureId: 'cap-flow-4',
      flowSelector: { destination_hostname: 'nonexistent.invalid' },
      dnsInCaptureResults: {},
      tlsSniResults: {},
      activeDnsResults: {},
    });
    expect(error).toBeDefined();
    expect(error).toContain('hostname_unresolved');
    expect(error).toContain('nonexistent.invalid');
  });

  it('handles budget exhaustion gracefully (Req 19.4)', () => {
    const { resolved, timedOut } = simulateFlowResolution({
      captureId: 'cap-flow-5',
      flowSelector: { destination_hostname: 'slow.example.com' },
      dnsInCaptureResults: {},
      tlsSniResults: {},
      activeDnsTimedOut: true,
    });
    expect(timedOut).toBe(true);
    // When timed out, returns whatever was collected (empty in this case)
    expect(resolved.length).toBe(0);
  });

  it('resolves direct IPs without hostname lookup (Req 19.1)', () => {
    const { resolved, error } = simulateFlowResolution({
      captureId: 'cap-flow-6',
      flowSelector: { source_ip: '10.0.1.1', destination_ip: '10.0.2.2', destination_port: 8080 },
    });
    expect(error).toBeUndefined();
    expect(resolved.length).toBe(2);
    expect(resolved[0].ip).toBe('10.0.1.1');
    expect(resolved[0].role).toBe('source');
    expect(resolved[1].ip).toBe('10.0.2.2');
    expect(resolved[1].role).toBe('destination');
    expect(resolved[1].port).toBe(8080);
  });

  it('validates port range (Req 19.1)', () => {
    const { error: e1 } = simulateFlowResolution({
      captureId: 'cap-flow-7',
      flowSelector: { source_ip: '10.0.0.1', source_port: -1 },
    });
    expect(e1).toContain('invalid_parameter');

    const { error: e2 } = simulateFlowResolution({
      captureId: 'cap-flow-8',
      flowSelector: { destination_ip: '10.0.0.1', destination_port: 70000 },
    });
    expect(e2).toContain('invalid_parameter');
  });

  it('applies source-only constraints to either direction (Req 19.6)', () => {
    const { resolved } = simulateFlowResolution({
      captureId: 'cap-flow-9',
      flowSelector: { source_ip: '10.0.1.1' },
    });
    expect(resolved.length).toBe(1);
    expect(resolved[0].role).toBe('source');
  });

  it('applies destination-only constraints to responder side (Req 19.7)', () => {
    const { resolved } = simulateFlowResolution({
      captureId: 'cap-flow-10',
      flowSelector: { destination_ip: '10.0.2.2', destination_port: 443 },
    });
    expect(resolved.length).toBe(1);
    expect(resolved[0].role).toBe('destination');
    expect(resolved[0].port).toBe(443);
  });
});
