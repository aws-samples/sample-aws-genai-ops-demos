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
import { sendMessage } from "../services/api";
import { Message } from "../types";

const WELCOME_MESSAGE: Message = {
  role: "assistant",
  content:
    "Hello! I'm your IAM Access Analyzer Assistant. I can help you with:\n\n" +
    "- **Reviewing IAM findings** from Security Hub\n" +
    "- **Generating least-privilege policies** based on CloudTrail activity\n" +
    "- **Blast radius analysis** before modifying or deleting IAM resources\n" +
    "- **Validating IAM policies** for security best practices\n" +
    "- **Prioritized action plans** for IAM remediation\n\n" +
    "Click a suggestion below to get started, or just ask me anything in your own words.\n\n" +
    "*Tip: Anything I generate (policies, reports, change requests) can be saved to S3 — just ask me to export it.*",
};

interface ActivityEntry {
  tool: string;
  timestamp: string;
}

type AssistantMode = "discovery" | "direct";

export default function ChatInterface() {
  const [messages, setMessages] = useState<Message[]>([WELCOME_MESSAGE]);
  const [inputValue, setInputValue] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sessionActivity, setSessionActivity] = useState<ActivityEntry[]>([]);
  const [mode, setMode] = useState<AssistantMode>("discovery");
  const [sessionTokens, setSessionTokens] = useState({ input: 0, output: 0 });
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const handleSend = async (overrideMessage?: string) => {
    const messageToSend = overrideMessage || inputValue;
    if (!messageToSend.trim() || isLoading) return;

    const userMessage: Message = { role: "user", content: messageToSend };
    setMessages((prev) => [...prev, userMessage]);
    setInputValue("");
    setIsLoading(true);
    setError(null);

    try {
      const history = messages
        .filter((m) => m !== WELCOME_MESSAGE)
        .map((m) => ({ role: m.role, content: m.content }));

      const response = await sendMessage(messageToSend, history, mode);

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

      // Track session token usage
      if (response.usage) {
        setSessionTokens((prev) => ({
          input: prev.input + (response.usage?.inputTokens || 0),
          output: prev.output + (response.usage?.outputTokens || 0),
        }));
      }
    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : "Unknown error";
      setError(errorMsg);
      const errorMessage: Message = {
        role: "assistant",
        content: `I encountered an error: ${errorMsg}\n\nPlease try again or rephrase your question.`,
      };
      setMessages((prev) => [...prev, errorMessage]);
    } finally {
      setIsLoading(false);
    }
  };

  const handleClear = () => {
    setMessages([WELCOME_MESSAGE]);
    setSessionActivity([]);
    setError(null);
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
            {mode === "discovery"
              ? "Discovery Mode — detailed explanations, AWS doc links, tool transparency"
              : "Direct Mode — concise answers, no hand-holding"}
          </span>
          <Button
            variant="inline-link"
            onClick={() => setMode(mode === "discovery" ? "direct" : "discovery")}
          >
            Switch to {mode === "discovery" ? "Direct" : "Discovery"}
          </Button>
        </div>

        {error && (
          <Alert type="error" dismissible onDismiss={() => setError(null)}>
            {error}
          </Alert>
        )}

        {/* Session activity summary */}
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
              placeholder="Ask about IAM findings, blast radius, generate policies..."
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
