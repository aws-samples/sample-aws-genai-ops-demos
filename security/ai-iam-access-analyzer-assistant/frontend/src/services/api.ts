import { fetchAuthSession } from "aws-amplify/auth";
import { Capabilities } from "../types";

const API_ENDPOINT = import.meta.env.VITE_API_ENDPOINT;

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
    // A raw fetch rejection ("Failed to fetch") on this endpoint is almost
    // always API Gateway's 29s integration timeout returning a 504 without
    // CORS headers, which the browser cannot read.
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
