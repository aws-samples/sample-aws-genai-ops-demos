import { fetchAuthSession } from "aws-amplify/auth";
import { Capabilities } from "../types";

// Normalize the endpoint to always end with a single trailing slash. The
// backend CDK sets `VITE_API_ENDPOINT` from the API Gateway stage invoke
// URL, which sometimes lands with a trailing slash and sometimes without
// depending on how it was written to `.env.production.local`. Callers do
// `${API_ENDPOINT}conversation` (no leading slash on the path piece), so
// an unnormalized value like `.../prod` produced `.../prodconversation`
// and blew up in <200ms with a browser `Failed to fetch`. Enforcing the
// slash here removes an entire class of misconfiguration.
const RAW_API_ENDPOINT = import.meta.env.VITE_API_ENDPOINT;
const API_ENDPOINT = RAW_API_ENDPOINT
  ? RAW_API_ENDPOINT.endsWith("/")
    ? RAW_API_ENDPOINT
    : `${RAW_API_ENDPOINT}/`
  : "";

/**
 * Thrown when the synchronous /conversation call hits (or almost certainly
 * hit) the API Gateway 29s integration limit.
 */
export class ApiTimeoutError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ApiTimeoutError";
  }
}

export interface PaginationContext {
  tool: string;
  next_token: string;
  has_more: boolean;
  last_input?: Record<string, unknown>;
}

interface ConversationResponse {
  response: string;
  usage?: {
    inputTokens?: number;
    outputTokens?: number;
  };
  tools_used?: Array<{
    tool: string;
    input_summary: string;
  }>;
  pagination?: PaginationContext | null;
}

interface MessageHistory {
  role: string;
  content: string;
}

export async function sendMessage(
  message: string,
  history: MessageHistory[],
  mode: string = "guided",
  pagination?: PaginationContext | null
): Promise<ConversationResponse> {
  const session = await fetchAuthSession();
  const token = session.tokens?.idToken?.toString();

  if (!token) {
    throw new Error("Not authenticated");
  }

  // Track wall-clock time so we can tell an actual API GW 29s timeout
  // (browser sees a CORS-less 504 that looks like `Failed to fetch`) apart
  // from a network / DNS / TLS / misconfigured-endpoint failure that
  // rejects in a few hundred ms. Same-looking exception, opposite
  // root cause — the operator needs different guidance for each.
  const startedAt = Date.now();
  let response: Response;
  try {
    response = await fetch(`${API_ENDPOINT}conversation`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: token,
      },
      body: JSON.stringify({
        message,
        history,
        mode,
        ...(pagination ? { pagination } : {}),
      }),
    });
  } catch {
    const elapsedMs = Date.now() - startedAt;
    // Under ~20 seconds a `Failed to fetch` is almost never the 29s API GW
    // timeout — the request never reached a gateway that could take that
    // long to answer. Surface a network-level error so the operator looks
    // at endpoint configuration / connectivity, not at synthesis load.
    if (elapsedMs < 20_000) {
      const seconds = (elapsedMs / 1000).toFixed(1);
      throw new Error(
        `Couldn't reach the API — the request failed in ${seconds}s. ` +
          "This is a network-level failure, not a synthesis timeout. Check " +
          "your connection or verify VITE_API_ENDPOINT points at the right " +
          "API Gateway stage."
      );
    }
    // Over ~20 seconds we're in API-GW-timeout territory: 504 without CORS
    // headers, which the browser surfaces as an unreadable network error.
    throw new ApiTimeoutError(
      "That request didn't finish in time — it likely ran past the API gateway's 29-second limit."
    );
  }

  if (!response.ok) {
    if (response.status === 504 || response.status === 502) {
      throw new ApiTimeoutError(
        "The request took longer than the API gateway allows (29s), so the connection timed out. " +
          "The operation may have still completed on the server — if you were exporting or saving something, " +
          "say \"list my exports\" to check before retrying. For multi-step requests, try one step per message."
      );
    }
    const error = await response.json().catch(() => ({}));
    throw new Error(error.message || error.error || `API error: ${response.status}`);
  }

  return response.json();
}

/**
 * Session-start capability probe (#171 phase C).
 *
 * Called once when the chat mounts. Returns a per-source status list plus
 * a server-composed welcome sentence that names what CAN and what CANNOT
 * be seen in this account/region. The endpoint is cheap (3–5 read-only
 * AWS calls, sub-second) so callers should treat it as best-effort — a
 * failure here should NOT block the chat from loading.
 */
export async function getCapabilities(): Promise<Capabilities> {
  const session = await fetchAuthSession();
  const token = session.tokens?.idToken?.toString();

  if (!token) {
    throw new Error("Not authenticated");
  }

  const response = await fetch(`${API_ENDPOINT}capabilities`, {
    method: "GET",
    headers: {
      Authorization: token,
    },
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(
      error.message || error.error || `API error: ${response.status}`
    );
  }

  return response.json();
}
