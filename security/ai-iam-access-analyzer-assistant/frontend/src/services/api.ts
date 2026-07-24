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
  mode: string = "discovery"
): Promise<ConversationResponse> {
  const session = await fetchAuthSession();
  const token = session.tokens?.idToken?.toString();

  if (!token) {
    throw new Error("Not authenticated");
  }

  const response = await fetch(`${API_ENDPOINT}conversation`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: token,
    },
    body: JSON.stringify({ message, history, mode }),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.message || `API error: ${response.status}`);
  }

  return response.json();
}
