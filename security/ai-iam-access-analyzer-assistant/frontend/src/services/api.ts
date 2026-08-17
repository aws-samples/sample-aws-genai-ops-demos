import { fetchAuthSession } from "aws-amplify/auth";

const API_ENDPOINT = import.meta.env.VITE_API_ENDPOINT;

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
}

interface MessageHistory {
  role: string;
  content: string;
}

export async function sendMessage(
  message: string,
  history: MessageHistory[],
  mode: string = "guided"
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
      body: JSON.stringify({ message, history, mode }),
    });
  } catch {
    // fetch() itself rejects (browser "Failed to fetch") on a network-level
    // failure. The most common cause here is API Gateway hitting its hard 29s
    // integration timeout on a heavy multi-tool turn and returning a 504 WITHOUT
    // CORS headers — the browser can't read it, so it surfaces as a generic
    // network/CORS error rather than a readable 504. Give actionable guidance
    // instead of a bare "Failed to fetch", and steer away from blind retries
    // (which just re-run the same slow path and fail the same way).
    throw new Error(
      "That request didn't finish in time — it likely ran past the API gateway's 29-second limit, " +
        "which happens when one request chains several analysis steps (e.g. investigating a role runs " +
        "finding details + blast radius + summarization together). Try narrowing it to a single step " +
        "— for example \"show the finding details for ConsoleAdminAccess\" first, then ask for blast " +
        "radius separately. If you were exporting or saving, say \"list my exports\" to check before retrying."
    );
  }

  if (!response.ok) {
    // API Gateway enforces a hard 29-second integration timeout. On long
    // multi-tool turns (e.g. an export that runs after other tool calls) the
    // gateway returns 504/502 while the Lambda keeps running and may still
    // finish its work — including writing an export to S3. Surface that clearly
    // so users check their exports instead of blindly retrying and creating
    // duplicate objects.
    if (response.status === 504 || response.status === 502) {
      throw new Error(
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
