import { useState, useRef, useEffect } from "react";
import Container from "@cloudscape-design/components/container";
import Header from "@cloudscape-design/components/header";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Input from "@cloudscape-design/components/input";
import Button from "@cloudscape-design/components/button";
import Box from "@cloudscape-design/components/box";
import Alert from "@cloudscape-design/components/alert";
import MessageBubble from "./MessageBubble";
import ErrorBoundary from "./ErrorBoundary";
import {
  sendMessage,
  getCapabilities,
  ApiTimeoutError,
  PaginationContext,
} from "../services/api";
import { Capabilities, CoverageEntry, Message } from "../types";

const GREETING_BODY =
  "Hello! I'm your **IAM Security Assistant**. I help you understand and fix your IAM roles and policies — unused roles, overly-permissive permissions, and cross-account access risks.\n\n" +
  "**Three capabilities that work independently or together:**\n\n" +
  "- **Analyze** — surface unused roles, excessive permissions, cross-account risks\n" +
  "- **Generate** — create least-privilege policies from actual usage\n" +
  "- **Protect** — validate changes, assess blast radius before you act\n\n" +
  "Click a suggestion below to get started, or ask anything in your own words.\n\n" +
  "*Tip: Anything I generate can be saved to S3 — just say \"export that\".*\n\n" +
  "🔒 **Read-only** — this assistant analyzes and recommends but never modifies your IAM roles, policies, or configurations.";

/**
 * Compose the welcome bubble from the greeting plus the session-start
 * capability probe (#171 phase C). When the probe has resolved, the
 * server-composed data-source honesty statement leads the bubble so the
 * user reads what CAN and what CANNOT be seen in this account/region
 * BEFORE the generic feature list.
 */
function buildWelcomeMessage(capabilities: Capabilities | null): Message {
  const content = capabilities?.welcome_message
    ? `${capabilities.welcome_message}\n\n---\n\n${GREETING_BODY}`
    : GREETING_BODY;
  return { role: "assistant", content };
}

interface ActivityEntry {
  tool: string;
  timestamp: string;
}

type AssistantMode = "guided" | "quick";

const TIMEOUT_ADVICE =
  "That request ran past the API gateway's 29-second limit before finishing. " +
  "Break it into smaller steps (for example, `show my active findings`, then `generate an action plan` on its own), " +
  "or ask for a narrower filter. Any export you were creating may still complete on the server — try `list my exports`.";

export default function ChatInterface() {
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [messages, setMessages] = useState<Message[]>([buildWelcomeMessage(null)]);
  const [inputValue, setInputValue] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sessionActivity, setSessionActivity] = useState<ActivityEntry[]>([]);
  const [mode, setMode] = useState<AssistantMode>("guided");
  const [sessionTokens, setSessionTokens] = useState({ input: 0, output: 0 });
  // Use a ref so the current pagination cursor is read synchronously on the
  // next send, without a re-render round trip.
  const paginationRef = useRef<PaginationContext | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // Session-start capability probe (#171 phase C). Best-effort: a failure
  // must NOT block the chat, so if the probe fails we leave capabilities
  // null and the welcome bubble falls back to the generic greeting.
  useEffect(() => {
    let cancelled = false;
    getCapabilities()
      .then((caps) => {
        if (cancelled) return;
        setCapabilities(caps);
        // Rebuild the welcome bubble in place, but only if the user hasn't
        // typed anything yet (still on the greeting-only state).
        setMessages((prev) =>
          prev.length === 1 && prev[0].role === "assistant"
            ? [buildWelcomeMessage(caps)]
            : prev
        );
      })
      .catch((err) => {
        // Non-fatal — log and move on.
        console.warn("capability probe failed:", err);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const handleSend = async (overrideMessage?: string) => {
    const messageToSend = overrideMessage || inputValue;
    if (!messageToSend.trim() || isLoading) return;

    const userMessage: Message = { role: "user", content: messageToSend };
    setMessages((prev) => [...prev, userMessage]);
    setInputValue("");
    setIsLoading(true);
    setError(null);

    try {
      // The welcome bubble is always messages[0] and belongs to the UI, not
      // the conversation. Sending it to the backend as prior assistant turn
      // would leak the greeting into the model's context every turn.
      const history = messages
        .slice(1)
        .map((m) => ({ role: m.role, content: m.content }));

      const response = await sendMessage(
        messageToSend,
        history,
        mode,
        paginationRef.current
      );

      // Persist pagination cursor for deterministic follow-ups like "next 20".
      // Clear it when the server did not return one so a later, unrelated turn
      // doesn't accidentally continue paging the wrong list.
      paginationRef.current = response.pagination ?? null;

      // Track session activity
      if (response.tools_used && response.tools_used.length > 0) {
        const newActivities = response.tools_used.map((t) => ({
          tool: t.tool,
          timestamp: new Date().toISOString(),
        }));
        setSessionActivity((prev) => [...prev, ...newActivities]);
      }

      // Show tools used as a subtle indicator
      let toolsPrefix = "";
      if (response.tools_used && response.tools_used.length > 0) {
        const toolNames = response.tools_used
          .map((t) => t.tool.replace(/_/g, " "))
          .join(", ");
        toolsPrefix = `*Used: ${toolNames}*\n\n`;
      }

      const assistantMessage: Message = {
        role: "assistant",
        content: toolsPrefix + response.response,
        usage: response.usage,
      };
      setMessages((prev) => [...prev, assistantMessage]);

      if (response.usage) {
        setSessionTokens((prev) => ({
          input: prev.input + (response.usage?.inputTokens || 0),
          output: prev.output + (response.usage?.outputTokens || 0),
        }));
      }
    } catch (err) {
      const isTimeout = err instanceof ApiTimeoutError;
      const errorMsg = isTimeout ? TIMEOUT_ADVICE : err instanceof Error ? err.message : "Unknown error";
      setError(errorMsg);
      const assistantMessage: Message = {
        role: "assistant",
        content: isTimeout ? errorMsg : `I encountered an error: ${errorMsg}\n\nPlease try again or rephrase your question.`,
      };
      setMessages((prev) => [...prev, assistantMessage]);
    } finally {
      setIsLoading(false);
    }
  };

  const handleClear = () => {
    setMessages([buildWelcomeMessage(capabilities)]);
    setSessionActivity([]);
    setError(null);
    paginationRef.current = null;
  };

  return (
    <Container
      header={
        <Header
          variant="h2"
          description="Ask questions about your IAM security posture"
          actions={
            <Button
              onClick={handleClear}
              iconName="remove"
              variant="icon"
              ariaLabel="Clear conversation"
            />
          }
        >
          Conversation
        </Header>
      }
    >
      <SpaceBetween size="m">
        {/* Mode toggle */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            padding: "8px 12px",
            backgroundColor: "var(--color-background-layout-toggle-selected-default)",
            borderRadius: "8px",
            border: "1px solid var(--color-border-divider-default)",
          }}
        >
          <span style={{ fontSize: "13px", color: "var(--color-text-body-secondary)" }}>
            {mode === "guided"
              ? "Guided Mode — detailed explanations, step-by-step recommendations, educational context"
              : "Quick Mode — concise answers, data-first, no hand-holding"}
          </span>
          <Button
            variant="inline-link"
            onClick={() => setMode(mode === "guided" ? "quick" : "guided")}
          >
            Switch to {mode === "guided" ? "Quick" : "Guided"}
          </Button>
        </div>

        {error && (
          <Alert type="error" dismissible onDismiss={() => setError(null)}>
            {error}
          </Alert>
        )}

        {capabilities && (
          <DataSourcesStatus capabilities={capabilities} />
        )}

        {sessionActivity.length > 0 && (
          <SessionActivityBar activities={sessionActivity} tokens={sessionTokens} />
        )}

        {/* Message history */}
        <div
          style={{
            maxHeight: "60vh",
            overflowY: "auto",
            padding: "16px 0",
          }}
        >
          <SpaceBetween size="s">
            {messages.map((message, index) => (
              <ErrorBoundary key={index}>
                <MessageBubble message={message} />
              </ErrorBoundary>
            ))}
            {isLoading && (
              <div
                style={{
                  display: "flex",
                  justifyContent: "flex-start",
                  padding: "4px 0",
                }}
              >
                <div
                  style={{
                    padding: "12px 16px",
                    borderRadius: "12px",
                    backgroundColor: "var(--color-background-container-content)",
                    border: "1px solid var(--color-border-divider-default)",
                  }}
                >
                  <Box color="text-body-secondary">
                    <LoadingDots />
                  </Box>
                </div>
              </div>
            )}
            <div ref={messagesEndRef} />
          </SpaceBetween>
        </div>

        {/* Suggested prompts — show only at start */}
        {messages.length <= 1 && !isLoading && (
          <SuggestedPrompts onSelect={(prompt) => handleSend(prompt)} />
        )}

        {/* Input area */}
        <form
          onSubmit={(e) => {
            e.preventDefault();
            handleSend();
          }}
          style={{ display: "flex", gap: "8px" }}
        >
          <div style={{ flex: 1 }}>
            <Input
              value={inputValue}
              onChange={({ detail }) => setInputValue(detail.value)}
              placeholder="Ask about a role, finding, or policy…"
              disabled={isLoading}
            />
          </div>
          <Button
            variant="primary"
            formAction="submit"
            onClick={() => handleSend()}
            disabled={!inputValue.trim() || isLoading}
            iconName="send"
          >
            Send
          </Button>
        </form>
      </SpaceBetween>
    </Container>
  );
}

// --- Sub-components ---

function SuggestedPrompts({ onSelect }: { onSelect: (prompt: string) => void }) {
  const prompts = [
    { label: "Guided tour", value: "Take me on a guided tour of my IAM security posture — walk me through step by step" },
    { label: "Show my findings", value: "What are my active IAM findings?" },
    {
      label: "Prioritized action plan",
      value: "Generate a prioritized action plan for my IAM findings",
    },
    {
      label: "Blast radius check",
      value: "What's the blast radius if I delete my most critical unused role?",
    },
    {
      label: "Build a policy",
      value: "Help me create a least-privilege policy for a new workload I'm building",
    },
    {
      label: "Compare roles",
      value: "Compare the risk profile of my top 3 unused roles",
    },
    {
      label: "Practice exercise",
      value: "Give me a practice exercise — show me an overly permissive policy and teach me what's wrong with it",
    },
  ];

  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: "8px" }}>
      {prompts.map((p) => (
        <Button key={p.label} variant="normal" onClick={() => onSelect(p.value)}>
          {p.label}
        </Button>
      ))}
    </div>
  );
}

function SessionActivityBar({ activities, tokens }: { activities: ActivityEntry[]; tokens: { input: number; output: number } }) {
  const toolCounts: Record<string, number> = {};
  for (const a of activities) {
    const name = a.tool.replace(/_/g, " ");
    toolCounts[name] = (toolCounts[name] || 0) + 1;
  }

  // Approximate cost: Claude Sonnet input $3/MTok, output $15/MTok
  const estimatedCost = (tokens.input * 3 + tokens.output * 15) / 1_000_000;
  const costDisplay = estimatedCost < 0.01 ? "<$0.01" : `~$${estimatedCost.toFixed(3)}`;

  return (
    <div
      style={{
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
        padding: "8px 12px",
        backgroundColor: "var(--color-background-status-info)",
        borderRadius: "8px",
        border: "1px solid var(--color-border-status-info)",
        fontSize: "12px",
        color: "var(--color-text-status-info)",
      }}
    >
      <span>
        <strong>Session:</strong>{" "}
        {Object.entries(toolCounts)
          .map(([name, count]) => `${name} (${count}x)`)
          .join(" | ")}
        {" — "}
        {activities.length} tool call{activities.length !== 1 ? "s" : ""}
      </span>
      <span style={{ opacity: 0.8 }}>
        {tokens.input + tokens.output > 0 && (
          <>Tokens: {(tokens.input + tokens.output).toLocaleString()} | Cost: {costDisplay}</>
        )}
      </span>
    </div>
  );
}

/**
 * Compact "Data sources" pill row rendered above the message history.
 *
 * One pill per AWS source (Security Hub, Access Analyzer, CloudTrail).
 * A source is "checked" (green ✓) if any coverage entry for it succeeded,
 * even when a peer entry failed — for example, when the external-access
 * analyzer is active but the unused-access one is missing, the Access
 * Analyzer pill shows a warning (⚠) rather than a full failure (⛔), and
 * the detail lists both entries so the user can see exactly which
 * sub-check was missing.
 */
function DataSourcesStatus({ capabilities }: { capabilities: Capabilities }) {
  const bySource = new Map<string, CoverageEntry[]>();
  for (const entry of capabilities.coverage) {
    const existing = bySource.get(entry.source);
    if (existing) {
      existing.push(entry);
    } else {
      bySource.set(entry.source, [entry]);
    }
  }

  const pretty: Record<string, string> = {
    securityhub: "Security Hub",
    accessanalyzer: "Access Analyzer",
    cloudtrail: "CloudTrail",
  };

  const pills = Array.from(bySource.entries()).map(([source, entries]) => {
    const hasChecked = entries.some((e) => e.state === "checked");
    const hasUnavailable = entries.some((e) => e.state === "unavailable");
    let icon = "✓";
    let color = "var(--color-text-status-success, #037f0c)";
    let bg = "var(--color-background-status-success, #f2fcf3)";
    let border = "var(--color-border-status-success, #d1e7d3)";
    if (hasChecked && hasUnavailable) {
      icon = "⚠";
      color = "var(--color-text-status-warning, #855900)";
      bg = "var(--color-background-status-warning, #fff8ec)";
      border = "var(--color-border-status-warning, #f0d69b)";
    } else if (!hasChecked && hasUnavailable) {
      icon = "⛔";
      color = "var(--color-text-status-error, #d13212)";
      bg = "var(--color-background-status-error, #fdf3f1)";
      border = "var(--color-border-status-error, #f2c9c1)";
    }
    const tooltip = entries.map((e) => `• ${e.detail}`).join("\n");
    return (
      <span
        key={source}
        title={tooltip}
        style={{
          padding: "4px 10px",
          borderRadius: "12px",
          border: `1px solid ${border}`,
          backgroundColor: bg,
          color,
          fontSize: "12px",
          fontWeight: 500,
          cursor: "help",
        }}
      >
        {icon} {pretty[source] || source}
      </span>
    );
  });

  return (
    <div
      style={{
        display: "flex",
        flexWrap: "wrap",
        alignItems: "center",
        gap: "8px",
        padding: "8px 12px",
        backgroundColor: "var(--color-background-container-content)",
        borderRadius: "8px",
        border: "1px solid var(--color-border-divider-default)",
      }}
    >
      <span
        style={{
          fontSize: "12px",
          fontWeight: 600,
          color: "var(--color-text-body-secondary)",
        }}
      >
        Data sources ({capabilities.region}):
      </span>
      {pills}
    </div>
  );
}

function LoadingDots() {
  return (
    <span style={{ display: "inline-flex", gap: "4px", alignItems: "center" }}>
      <span>Analyzing</span>
      <span className="loading-dots">
        <span style={{ animation: "pulse 1.4s infinite", animationDelay: "0s" }}>.</span>
        <span style={{ animation: "pulse 1.4s infinite", animationDelay: "0.2s" }}>.</span>
        <span style={{ animation: "pulse 1.4s infinite", animationDelay: "0.4s" }}>.</span>
      </span>
      <style>{`
        @keyframes pulse {
          0%, 80%, 100% { opacity: 0.3; }
          40% { opacity: 1; }
        }
      `}</style>
    </span>
  );
}
