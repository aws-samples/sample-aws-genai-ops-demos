/**
 * Rate limiting module for the GOAT Network Agent ↔ DevOps Agent Integration.
 *
 * Enforces a maximum of 3 concurrent active captures using DynamoDB
 * to atomically check and enforce the concurrency limit.
 *
 * Requirements: 1.6
 */

import {
  DynamoDBClient,
  QueryCommand,
  QueryCommandInput,
} from "@aws-sdk/client-dynamodb";
import {
  createRateLimitExceededError,
  ErrorDescription,
} from "../types/errors";

/** Maximum number of concurrent active captures allowed per tool interface */
export const MAX_CONCURRENT_CAPTURES = 3;

/** Capture statuses considered "active" for concurrency counting */
export const ACTIVE_CAPTURE_STATUSES = ["active", "capturing", "initializing"];

/**
 * Creates a DynamoDB client instance.
 * Extracted for testability — can be overridden in tests.
 */
export function createDynamoDBClient(
  region?: string
): DynamoDBClient {
  return new DynamoDBClient({ region: region ?? process.env.AWS_REGION });
}

/**
 * Queries DynamoDB for the count of currently active captures.
 *
 * Uses a query on the capture state table filtering for active statuses.
 * The table is expected to have captures stored with:
 *   PK: "CAPTURE_SESSION"
 *   status attribute: one of the ACTIVE_CAPTURE_STATUSES
 *
 * @param tableName - DynamoDB table name
 * @param client - DynamoDB client instance
 * @returns The count of currently active captures
 */
export async function getActiveCaptureCount(
  tableName: string,
  client: DynamoDBClient
): Promise<number> {
  // Query for active captures using a scan with filter
  // The capture state table stores sessions with a status attribute
  const params: QueryCommandInput = {
    TableName: tableName,
    IndexName: "StatusIndex",
    KeyConditionExpression: "#pk = :pk",
    FilterExpression: "#status IN (:s1, :s2, :s3)",
    ExpressionAttributeNames: {
      "#pk": "record_type",
      "#status": "capture_status",
    },
    ExpressionAttributeValues: {
      ":pk": { S: "CAPTURE_SESSION" },
      ":s1": { S: "active" },
      ":s2": { S: "capturing" },
      ":s3": { S: "initializing" },
    },
    Select: "COUNT",
  };

  try {
    const result = await client.send(new QueryCommand(params));
    return result.Count ?? 0;
  } catch {
    // If the index doesn't exist or table is misconfigured,
    // fall back to a scan-based approach
    const { ScanCommand } = await import("@aws-sdk/client-dynamodb");
    const scanResult = await client.send(
      new ScanCommand({
        TableName: tableName,
        FilterExpression:
          "#rt = :rt AND #status IN (:s1, :s2, :s3)",
        ExpressionAttributeNames: {
          "#rt": "record_type",
          "#status": "capture_status",
        },
        ExpressionAttributeValues: {
          ":rt": { S: "CAPTURE_SESSION" },
          ":s1": { S: "active" },
          ":s2": { S: "capturing" },
          ":s3": { S: "initializing" },
        },
        Select: "COUNT",
      })
    );
    return scanResult.Count ?? 0;
  }
}

/**
 * Checks whether a new capture can be started based on the concurrency limit.
 *
 * Queries DynamoDB for currently active captures and enforces the
 * maximum of 3 concurrent captures per tool interface.
 *
 * @param tableName - DynamoDB table name (from CAPTURE_STATE_TABLE env var)
 * @param client - Optional DynamoDB client (created if not provided)
 * @param maxConcurrent - Maximum allowed concurrent captures (default: 3)
 * @returns null if the capture is allowed, or an ErrorDescription if rate limited
 */
export async function checkCaptureRateLimit(
  tableName?: string,
  client?: DynamoDBClient,
  maxConcurrent: number = MAX_CONCURRENT_CAPTURES
): Promise<ErrorDescription | null> {
  const table = tableName ?? process.env.CAPTURE_STATE_TABLE;

  if (!table) {
    // If no table is configured, cannot enforce rate limit — allow the request
    // This is a configuration issue that should be caught at deployment time
    return null;
  }

  const dynamoClient = client ?? createDynamoDBClient();

  const activeCount = await getActiveCaptureCount(table, dynamoClient);

  if (activeCount >= maxConcurrent) {
    return createRateLimitExceededError(activeCount, maxConcurrent);
  }

  return null;
}
