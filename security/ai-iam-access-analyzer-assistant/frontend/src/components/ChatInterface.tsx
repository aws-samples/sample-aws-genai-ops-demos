import { useState, useRef, useEffect } from "react";
import Container from "@cloudscape-design/components/container";
import Header from "@cloudscape-design/components/header";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Button from "@cloudscape-design/components/button";
import Box from "@cloudscape-design/components/box";
import FormField from "@cloudscape-design/components/form-field";
import KeyValuePairs from "@cloudscape-design/components/key-value-pairs";
import LiveRegion from "@cloudscape-design/components/live-region";
import Popover from "@cloudscape-design/components/popover";
import PromptInput from "@cloudscape-design/components/prompt-input";
import SegmentedControl from "@cloudscape-design/components/segmented-control";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import Avatar from "@cloudscape-design/chat-components/avatar";
import ChatBubble from "@cloudscape-design/chat-components/chat-bubble";
import SupportPromptGroup from "@cloudscape-design/chat-components/support-prompt-group";
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
  "This generative AI assistant helps you understand and fix your IAM roles, policies, and long-lived access keys — unused roles, overly-permissive permissions, cross-account access risks, and credential hygiene.\n\n" +
  "**Four capabilities that work independently or together:**\n\n" +
  "- **Analyze** — surface unused roles, excessive permissions, cross-account risks\n" +
  "- **Audit** — inventory long-lived access keys and recommend short-lived-credential replacements\n" +
  "- **Generate** — create least-privilege policies from actual usage\n" +
  "- **Protect** — validate changes, assess blast radius before you act\n\n" +
  "Choose a suggested question, or ask anything in your own words.\n\n" +
  "*Tip: anything generated here can be saved to S3 — just say \"export that\".*";

// Read-only disclaimer moved to the composer's FormField constraintText per
// Cloudscape's disclaimer pattern (Cloudscape gen-AI chat › "Under the prompt
// input, use FormField constraint text for constraint content that applies
// to the entire chat"). The pre-existing 🔒 glyph + prose inside the
// welcome bubble is removed here (#167 Req 3.4, Req 8.5) — the constraint
// message below carries the same information in the right place.
const COMPOSER_DISCLAIMER =
  "Read-only assistant — analyzes and recommends. Never modifies IAM roles, policies, or configurations.";

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

/**
 * Suggested prompts rendered as a Cloudscape <SupportPromptGroup> below the
 * transcript on session start. Each item's `id` is the full prompt text
 * sent to the backend when clicked; the `text` is the short label the user
 * sees on the pill.
 */
const SUGGESTED_PROMPTS: Array<{ id: string; text: string }> = [
  { text: "Guided tour", id: "Take me on a guided tour of my IAM security posture — walk me through step by step" },
  { text: "Show my findings", id: "What are my active IAM findings?" },
  { text: "Prioritized action plan", id: "Generate a prioritized action plan for my IAM findings" },
  { text: "Audit access keys", id: "Audit my IAM access keys" },
  { text: "Blast radius check", id: "What's the blast radius if I delete my most critical unused role?" },
  { text: "Build a policy", id: "Help me create a least-privilege policy for a new workload I'm building" },
  { text: "Compare roles", id: "Compare the risk profile of my top 3 unused roles" },
  { text: "Practice exercise", id: "Give me a practice exercise — show me an overly permissive policy and teach me what's wrong with it" },
];

const TIMEOUT_ADVICE =
  "That request ran past the API gateway's 29-second limit before finishing. " +
  "Break it into smaller steps (for example, `show my active findings`, then `generate an action plan` on its own), " +
  "or ask for a narrower filter. Any export you were creating may still complete on the server — try `list my exports`.";

const GENERIC_ERROR_PREAMBLE =
  "The request could not complete";

export default function ChatInterface() {
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [messages, setMessages] = useState<Message[]>([buildWelcomeMessage(null)]);
  const [inputValue, setInputValue] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [sessionActivity, setSessionActivity] = useState<ActivityEntry[]>([]);
  const [mode, setMode] = useState<AssistantMode>("guided");
  const [sessionTokens, setSessionTokens] = useState({ input: 0, output: 0 });
  // Announcement text for the <LiveRegion> below. Screen readers re-announce
  // whenever this string changes — used to signal "Generating a response"
  // on request start and the plain-text response body on request end.
  const [liveAnnouncement, setLiveAnnouncement] = useState("");
  // Whether the transcript is currently scrolled to the bottom. Auto-scroll
  // to new messages ONLY when true, so a user reading older messages is not
  // yanked back to the end (#167 Req 7.5).
  const [isAtBottom, setIsAtBottom] = useState(true);
  // Use a ref so the current pagination cursor is read synchronously on the
  // next send, without a re-render round trip.
  const paginationRef = useRef<PaginationContext | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const transcriptRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (isAtBottom) {
      messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [messages, isAtBottom]);

  // Update `isAtBottom` from the scroll listener so `useEffect` above can
  // decide whether new messages should pull the viewport. The tolerance
  // absorbs sub-pixel scroll positions and browser rounding.
  const handleTranscriptScroll: React.UIEventHandler<HTMLDivElement> = (e) => {
    const el = e.currentTarget;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
    setIsAtBottom(atBottom);
  };

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
    // Announce request start to screen readers via the <LiveRegion>.
    setLiveAnnouncement("Generating a response");
    // Client-side request-duration measurement — used by the Thinking
    // pattern's ExpandableSection header ("Thought for Ns"). This is a
    // wall-clock measurement, so it includes network latency to API
    // Gateway plus the backend Lambda's full turn. The backend does not
    // currently return a server-side elapsed time; if it starts to, we
    // switch to that value here (it would be more accurate for the
    // "how long did the model think" question).
    const startTime = performance.now();

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

      const durationSeconds = Math.max(
        1,
        Math.round((performance.now() - startTime) / 1000)
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

      // Tools_used and duration ride ON the Message object now (per #167
      // Req 5). The old `*Used: tool1, tool2*` markdown prefix that used
      // to be concatenated into the response text is gone — MessageBubble
      // renders a Cloudscape ExpandableSection + Steps for these fields.
      const assistantMessage: Message = {
        role: "assistant",
        content: response.response,
        usage: response.usage,
        toolsUsed: response.tools_used?.map((t) => ({
          tool: t.tool,
          input_summary: t.input_summary,
        })),
        durationSeconds,
      };
      setMessages((prev) => [...prev, assistantMessage]);
      // Announce the response body to screen readers. Strip markdown emphasis
      // markers so the announcement reads as plain text, not "star star word
      // star star". Cap the announced length so a long response doesn't lock
      // the AT into a multi-minute readback.
      setLiveAnnouncement(announceableText(response.response));

      if (response.usage) {
        setSessionTokens((prev) => ({
          input: prev.input + (response.usage?.inputTokens || 0),
          output: prev.output + (response.usage?.outputTokens || 0),
        }));
      }
    } catch (err) {
      const isTimeout = err instanceof ApiTimeoutError;
      const errorMsg = isTimeout
        ? TIMEOUT_ADVICE
        : `${GENERIC_ERROR_PREAMBLE}: ${err instanceof Error ? err.message : "Unknown error"}. Try again, or rephrase the question.`;
      // Single-source error rendering per #167 Req 7: append one error
      // message to the transcript at the failed turn's position. The
      // previous double-render (top-of-container Alert + assistant text
      // bubble) is gone — MessageBubble detects `kind: "error"` and
      // renders a Cloudscape <Alert type="error" action=Try again>.
      const errorMessage: Message = {
        role: "assistant",
        kind: "error",
        content: errorMsg,
        retryPrompt: messageToSend,
      };
      setMessages((prev) => [...prev, errorMessage]);
      setLiveAnnouncement(errorMsg);
    } finally {
      setIsLoading(false);
    }
  };

  const handleClear = () => {
    setMessages([buildWelcomeMessage(capabilities)]);
    setSessionActivity([]);
    paginationRef.current = null;
    setLiveAnnouncement("");
    setIsAtBottom(true);
  };

  /**
   * Called when the user clicks "Try again" on an error message in the
   * transcript. Removes the error message from the list (so we don't
   * pile up duplicate error alerts if the retry also fails) and
   * re-sends the original user prompt through the normal handleSend
   * path (which will append a fresh assistant response or a fresh
   * error message as appropriate).
   */
  const handleRetry = (index: number, prompt: string) => {
    setMessages((prev) => prev.filter((_, i) => i !== index));
    void handleSend(prompt);
  };

  /**
   * Toggle the helpful / not-helpful feedback flag on a specific message.
   * Clicking the same option twice clears the vote. Local-only state per
   * #167 spec DD-4 — no server telemetry endpoint is called.
   */
  const handleFeedback = (index: number, feedback: "helpful" | "not-helpful") => {
    setMessages((prev) =>
      prev.map((m, i) =>
        i === index
          ? { ...m, feedback: m.feedback === feedback ? undefined : feedback }
          : m
      )
    );
  };

  return (
    <Container
      header={
        <Header
          variant="h2"
          description={
            mode === "guided"
              ? "Guided — detailed explanations, step-by-step recommendations"
              : "Quick — concise answers, data-first"
          }
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <SegmentedControl
                selectedId={mode}
                onChange={({ detail }) => setMode(detail.selectedId as AssistantMode)}
                label="Response style"
                options={[
                  { id: "guided", text: "Guided" },
                  { id: "quick", text: "Quick" },
                ]}
              />
              <Button
                onClick={handleClear}
                iconName="remove"
                variant="icon"
                ariaLabel="Clear conversation"
              />
            </SpaceBetween>
          }
        >
          Conversation
        </Header>
      }
    >
      <SpaceBetween size="m">
        {capabilities && <DataSourcesStatus capabilities={capabilities} />}

        {sessionActivity.length > 0 && (
          <SessionActivityBar activities={sessionActivity} tokens={sessionTokens} />
        )}

        {/* Transcript with accessibility landmark. Screen readers get a
            "Chat" region with all messages inside, so users can navigate
            in and out with landmark shortcuts. */}
        <div
          role="region"
          aria-label="Chat"
          ref={transcriptRef}
          onScroll={handleTranscriptScroll}
          style={{
            maxHeight: "60vh",
            overflowY: "auto",
            padding: "16px 0",
          }}
        >
          <SpaceBetween size="s">
            {messages.map((message, index) => (
              <ErrorBoundary key={index}>
                <MessageBubble
                  message={message}
                  onFeedback={
                    message.role === "assistant" && index > 0 && !message.kind
                      ? (fb) => handleFeedback(index, fb)
                      : undefined
                  }
                  onRetry={
                    message.kind === "error" && message.retryPrompt
                      ? () => handleRetry(index, message.retryPrompt!)
                      : undefined
                  }
                />
              </ErrorBoundary>
            ))}
            {isLoading && <LoadingBubble />}
            <div ref={messagesEndRef} />
          </SpaceBetween>
        </div>

        {/* Suggested prompts — Cloudscape <SupportPromptGroup>, session
            start only (#167 Req 3.3). */}
        {messages.length <= 1 && !isLoading && (
          <SupportPromptGroup
            ariaLabel="Suggested questions"
            alignment="horizontal"
            items={SUGGESTED_PROMPTS}
            onItemClick={({ detail }) => handleSend(detail.id)}
          />
        )}

        {/* Composer with disclaimer as constraint text (#167 Req 3.1, 3.4).
            PromptInput handles Enter-to-send, the send button icon, and
            multi-line growth. */}
        <FormField constraintText={COMPOSER_DISCLAIMER}>
          <PromptInput
            value={inputValue}
            onChange={({ detail }) => setInputValue(detail.value)}
            onAction={() => handleSend()}
            actionButtonIconName="send"
            actionButtonAriaLabel="Send message"
            placeholder="Ask a question"
            disabled={isLoading}
            minRows={1}
            maxRows={4}
            ariaLabel="Ask the generative AI assistant a question"
          />
        </FormField>
      </SpaceBetween>

      {/* Visually hidden live region for screen-reader announcements
          (#167 Req 4.2, 4.3). Cloudscape's <LiveRegion> re-announces
          whenever its rendered children change; we drive it from a
          single state string so start/end announcements are serialized. */}
      <LiveRegion hidden>{liveAnnouncement}</LiveRegion>
    </Container>
  );
}

// --- Sub-components ---

/**
 * Placeholder assistant bubble shown while a request is in flight. Uses the
 * standard Cloudscape gen-AI loading pattern: an incoming <ChatBubble> whose
 * <Avatar> is in the loading state, with visible copy so sighted users see
 * that something is happening. The <LiveRegion> mounted alongside handles
 * the AT announcement.
 */
function LoadingBubble() {
  return (
    <ChatBubble
      type="incoming"
      showLoadingBar
      avatar={
        <Avatar
          iconName="gen-ai"
          color="gen-ai"
          loading
          ariaLabel="Generative AI assistant thinking"
        />
      }
      ariaLabel="Assistant is generating a response"
    >
      <Box color="text-body-secondary">Generating a response</Box>
    </ChatBubble>
  );
}

/**
 * Strip markdown emphasis and cap length so the <LiveRegion> announcement
 * reads as natural language. Screen readers otherwise pronounce `**bold**`
 * as "star star bold star star".
 */
function announceableText(raw: string): string {
  const stripped = raw
    .replace(/\*\*(.*?)\*\*/g, "$1")
    .replace(/\*(.*?)\*/g, "$1")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/^\s*[-*]\s+/gm, "")
    .replace(/^\s*#{1,6}\s+/gm, "")
    .trim();
  const MAX = 600;
  return stripped.length > MAX ? `${stripped.slice(0, MAX)}. Response continues.` : stripped;
}

/**
 * Compact "Data sources" row rendered above the message history.
 *
 * One entry per AWS source (Security Hub, Access Analyzer, CloudTrail),
 * rendered as a Cloudscape `StatusIndicator` — `success` when every coverage
 * entry for the source succeeded, `warning` when some succeeded and some
 * failed (typical: external-access analyzer active but unused-access one
 * missing), `error` when all failed. Each indicator is wrapped in a
 * Cloudscape `Popover` that surfaces the per-entry detail on click, giving
 * keyboard-accessible and screen-reader-friendly disclosure of what each
 * sub-check actually observed.
 *
 * The surrounding row uses inline flex styles for now; a fuller Cloudscape
 * refactor of ChatInterface's hand-rolled wrappers is tracked in #167.
 * This component only takes on the semantic status primitives — which are
 * the pieces Ben's #167 "Writing" section (glyph-in-copy) explicitly calls
 * out — and leaves the rest of the layout for that rework.
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

  const items = Array.from(bySource.entries()).map(([source, entries]) => {
    const hasChecked = entries.some((e) => e.state === "checked");
    const hasUnavailable = entries.some((e) => e.state === "unavailable");
    let type: "success" | "warning" | "error" = "success";
    if (hasChecked && hasUnavailable) {
      type = "warning";
    } else if (!hasChecked && hasUnavailable) {
      type = "error";
    }
    return (
      <Popover
        key={source}
        size="medium"
        triggerType="text"
        dismissButton={false}
        header={pretty[source] || source}
        content={
          <ul style={{ margin: 0, paddingInlineStart: "1.25em" }}>
            {entries.map((e, i) => (
              <li key={i}>{e.detail}</li>
            ))}
          </ul>
        }
      >
        <StatusIndicator type={type}>
          {pretty[source] || source}
        </StatusIndicator>
      </Popover>
    );
  });

  return (
    <div
      style={{
        display: "flex",
        flexWrap: "wrap",
        alignItems: "center",
        gap: "12px",
        padding: "8px 12px",
        backgroundColor: "var(--color-background-container-content)",
        borderRadius: "8px",
        border: "1px solid var(--color-border-divider-default)",
      }}
    >
      <Box variant="small" fontWeight="bold" color="text-body-secondary">
        Data sources ({capabilities.region})
      </Box>
      {items}
    </div>
  );
}

/**
 * Session stats rendered as a Cloudscape KeyValuePairs list instead of a
 * hand-rolled colored div. Three columns: tool call breakdown, token
 * counts, estimated cost. All theme tokens come from Cloudscape now (no
 * more direct `var(--color-*)` reads).
 */
function SessionActivityBar({
  activities,
  tokens,
}: {
  activities: ActivityEntry[];
  tokens: { input: number; output: number };
}) {
  const toolCounts: Record<string, number> = {};
  for (const a of activities) {
    const name = a.tool.replace(/_/g, " ");
    toolCounts[name] = (toolCounts[name] || 0) + 1;
  }

  // Approximate cost: Claude Sonnet input $3/MTok, output $15/MTok
  const estimatedCost = (tokens.input * 3 + tokens.output * 15) / 1_000_000;
  const costDisplay = estimatedCost < 0.01 ? "<$0.01" : `~$${estimatedCost.toFixed(3)}`;
  const totalTokens = tokens.input + tokens.output;
  const toolSummary = Object.entries(toolCounts)
    .map(([name, count]) => `${name} (${count}×)`)
    .join(", ") || "None";
  const totalCalls = activities.length;

  return (
    <Box padding="xs" variant="div">
      <KeyValuePairs
        columns={3}
        items={[
          {
            label: `Tool calls (${totalCalls})`,
            value: <Box variant="small">{toolSummary}</Box>,
          },
          {
            label: "Tokens",
            value: (
              <Box variant="small">
                {totalTokens > 0 ? totalTokens.toLocaleString() : "0"}
              </Box>
            ),
          },
          {
            label: "Estimated cost",
            value: <Box variant="small">{totalTokens > 0 ? costDisplay : "$0.00"}</Box>,
          },
        ]}
      />
    </Box>
  );
}
