import Alert from "@cloudscape-design/components/alert";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import ButtonGroup from "@cloudscape-design/components/button-group";
import ExpandableSection from "@cloudscape-design/components/expandable-section";
import Link from "@cloudscape-design/components/link";
import SpaceBetween from "@cloudscape-design/components/space-between";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import Steps from "@cloudscape-design/components/steps";
import Avatar from "@cloudscape-design/chat-components/avatar";
import ChatBubble from "@cloudscape-design/chat-components/chat-bubble";
import ReactMarkdown, { type Components } from "react-markdown";
import { DownloadLink, isDownloadUrl } from "./DownloadLink";
import remarkGfm from "remark-gfm";
import rehypeSanitize from "rehype-sanitize";
import PolicyViewer from "./PolicyViewer";
import FindingsTable from "./FindingsTable";
import DependencyGraph from "./DependencyGraph";
import AccessKeysTable from "./AccessKeysTable";
import { Message, Finding, DependencyResult, AccessKeysReport } from "../types";

interface MessageBubbleProps {
  message: Message;
  /**
   * Called when the user toggles the Helpful / Not helpful vote on this
   * response. Ignored for user messages and the UI-generated welcome
   * bubble. Local-only state per #167 spec DD-4 — no server telemetry.
   */
  onFeedback?: (feedback: "helpful" | "not-helpful") => void;
  /**
   * Called when the user clicks "Try again" on an error message. Only
   * meaningful when `message.kind === "error"` and the parent supplied
   * a `retryPrompt` — otherwise omit.
   */
  onRetry?: () => void;
}

/**
 * Custom renderers passed to `<ReactMarkdown>` so that markdown emitted by
 * the model is themed via Cloudscape primitives instead of raw HTML with
 * inline styles. Only the elements that differ from the browser default
 * are overridden — headings, paragraphs, lists, emphasis, and line breaks
 * inherit the Cloudscape global stylesheet and are left alone.
 *
 * SAFETY: this component tree is the ONLY path model output takes to the
 * DOM (see #167 Req 1). We render through `<ReactMarkdown>` with the
 * `rehypeSanitize` plugin using its default GitHub-derived schema, which
 * strips `<script>`, `<style>`, `<iframe>`, `<object>`, `<embed>`, every
 * `on*=` event handler, and any `javascript:` URL BEFORE this components
 * table is ever consulted. `dangerouslySetInnerHTML` is never used
 * anywhere in this file, so a malicious model turn (or a user turn the
 * model reflects back) cannot inject executable HTML.
 */
const MARKDOWN_COMPONENTS: Components = {
  a: ({ href, children }) => {
    // Cognito-authed download URLs from our API GW /downloads/ route go
    // through DownloadLink — that component intercepts the click, fetches
    // with the current Cognito session's Bearer token, and triggers a
    // browser Blob download. A plain <Link> here would 401 (browsers
    // don't attach Authorization headers to navigations).
    if (typeof href === "string" && isDownloadUrl(href)) {
      return <DownloadLink href={href}>{children}</DownloadLink>;
    }
    const isExternal =
      typeof href === "string" && /^https?:\/\//i.test(href);
    return (
      <Link
        href={href}
        external={isExternal}
        target={isExternal ? "_blank" : undefined}
        rel={isExternal ? "noopener noreferrer" : undefined}
        variant="primary"
      >
        {children}
      </Link>
    );
  },
  code: ({ children, className }) => {
    const isBlock = typeof className === "string" && className.startsWith("language-");
    if (isBlock) {
      return (
        <pre style={{ margin: 0, whiteSpace: "pre-wrap" }}>
          <code className={className}>{children}</code>
        </pre>
      );
    }
    return (
      <Box variant="code" display="inline">
        {children}
      </Box>
    );
  },
};

export default function MessageBubble({ message, onFeedback, onRetry }: MessageBubbleProps) {
  // Error rendering per #167 Req 7: a failed turn is a single inline
  // Cloudscape <Alert type="error"> at the position of the failure, with
  // a Try again action wired to re-send the original user prompt through
  // the parent's handleSend. This replaces the previous double-render
  // (top-of-container Alert + assistant text bubble parroting the error).
  if (message.kind === "error") {
    return (
      <Alert
        type="error"
        header="The request could not complete"
        action={
          onRetry ? (
            <Button onClick={onRetry} variant="normal">
              Try again
            </Button>
          ) : undefined
        }
      >
        {message.content}
      </Alert>
    );
  }

  const isUser = message.role === "user";

  const avatar = isUser ? (
    <Avatar iconName="user-profile" tooltipText="You" ariaLabel="Your message" />
  ) : (
    <Avatar
      iconName="gen-ai"
      color="gen-ai"
      tooltipText="Generative AI assistant"
      ariaLabel="Generative AI assistant response"
    />
  );

  const showActions = !isUser && onFeedback !== undefined && message.content.trim().length > 0;

  return (
    <ChatBubble
      type={isUser ? "outgoing" : "incoming"}
      avatar={avatar}
      ariaLabel={isUser ? "Your message" : "Generative AI assistant response"}
      actions={
        showActions ? (
          <ResponseActions message={message} onFeedback={onFeedback!} />
        ) : undefined
      }
    >
      {isUser ? (
        <Box variant="p">
          <span style={{ whiteSpace: "pre-wrap", lineHeight: 1.5 }}>
            {message.content}
          </span>
        </Box>
      ) : (
        <AssistantContent message={message} />
      )}
    </ChatBubble>
  );
}

function AssistantContent({ message }: { message: Message }) {
  return (
    <SpaceBetween size="s">
      {message.toolsUsed && message.toolsUsed.length > 0 && (
        <ThinkingSection
          toolsUsed={message.toolsUsed}
          durationSeconds={message.durationSeconds}
        />
      )}
      <AssistantSections message={message} />
    </SpaceBetween>
  );
}

/**
 * Cloudscape "Thinking pattern" disclosure — an inline ExpandableSection
 * whose body is a Steps list, one step per tool the backend called on
 * this turn. Collapsed by default; user opens it to inspect the trace.
 *
 * The backend is synchronous (POST /conversation returns everything at
 * once, including tools_used), so all steps arrive completed and render
 * with status="success". A live in-progress spinner during a turn would
 * need backend streaming (Bedrock Converse with reasoning content); the
 * spec explicitly defers that per DD-2.
 */
function ThinkingSection({
  toolsUsed,
  durationSeconds,
}: {
  toolsUsed: NonNullable<Message["toolsUsed"]>;
  durationSeconds?: number;
}) {
  const headerText = thoughtHeaderText(toolsUsed.length, durationSeconds);
  return (
    <ExpandableSection variant="inline" headerText={headerText} defaultExpanded={false}>
      <Steps
        ariaLabel="Tools used to build this response"
        steps={toolsUsed.map((t) => ({
          status: "success" as const,
          header: humanizeToolName(t.tool),
          details: t.input_summary || undefined,
        }))}
      />
    </ExpandableSection>
  );
}

function thoughtHeaderText(count: number, durationSeconds?: number): string {
  if (typeof durationSeconds === "number" && durationSeconds > 0) {
    const noun = count === 1 ? "tool" : "tools";
    return `Thought for ${durationSeconds}s — ${count} ${noun}`;
  }
  return `Used ${count} ${count === 1 ? "tool" : "tools"}`;
}

function humanizeToolName(tool: string): string {
  // "list_findings" -> "List findings"
  // "generate_action_plan" -> "Generate action plan"
  const spaced = tool.replace(/_/g, " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

/**
 * Inline actions rendered in the ChatBubble's `actions` footer slot per
 * Cloudscape's gen-AI chat pattern:
 *   * Helpful / Not helpful — icon-toggle-button with feedback popover.
 *     Toggling either OFF returns feedback to "no vote". Setting one
 *     while the other is on switches the vote (handled in the parent).
 *   * Copy — copies the raw response text to the clipboard, with a
 *     "Copied" popover confirmation.
 *   * Save as .md — same download behavior as the pre-Cloudscape
 *     surface; used to appear conditionally only for long responses,
 *     now always visible so users don't have to guess the threshold.
 */
function ResponseActions({
  message,
  onFeedback,
}: {
  message: Message;
  onFeedback: (feedback: "helpful" | "not-helpful") => void;
}) {
  const handleClick = ({ detail }: { detail: { id: string } }) => {
    if (detail.id === "helpful") {
      onFeedback("helpful");
    } else if (detail.id === "not-helpful") {
      onFeedback("not-helpful");
    } else if (detail.id === "copy") {
      void navigator.clipboard?.writeText(message.content);
    } else if (detail.id === "save") {
      void saveAsMarkdown(message.content);
    }
  };

  return (
    <ButtonGroup
      ariaLabel="Response actions"
      variant="icon"
      onItemClick={handleClick}
      items={[
        {
          type: "icon-toggle-button",
          id: "helpful",
          iconName: "thumbs-up",
          pressedIconName: "thumbs-up-filled",
          text: "Helpful",
          pressed: message.feedback === "helpful",
          popoverFeedback: (
            <StatusIndicator type="success">Marked as helpful</StatusIndicator>
          ),
          pressedPopoverFeedback: (
            <StatusIndicator type="info">Helpful vote cleared</StatusIndicator>
          ),
        },
        {
          type: "icon-toggle-button",
          id: "not-helpful",
          iconName: "thumbs-down",
          pressedIconName: "thumbs-down-filled",
          text: "Not helpful",
          pressed: message.feedback === "not-helpful",
          popoverFeedback: (
            <StatusIndicator type="success">Marked as not helpful</StatusIndicator>
          ),
          pressedPopoverFeedback: (
            <StatusIndicator type="info">Not-helpful vote cleared</StatusIndicator>
          ),
        },
        {
          type: "icon-button",
          id: "copy",
          iconName: "copy",
          text: "Copy",
          popoverFeedback: (
            <StatusIndicator type="success">Copied to clipboard</StatusIndicator>
          ),
        },
        {
          type: "icon-button",
          id: "save",
          iconName: "download",
          text: "Save as .md",
        },
      ]}
    />
  );
}

async function saveAsMarkdown(content: string) {
  const filename = `iam-analysis-${new Date().toISOString().slice(0, 10)}.md`;
  const blob = new Blob([content], { type: "text/markdown" });

  // Prefer the File System Access API (proper Save As dialog) when the
  // browser supports it and the request is served over a secure context.
  const w = window as unknown as {
    showSaveFilePicker?: (opts: unknown) => Promise<FileSystemFileHandle>;
  };
  if (typeof w.showSaveFilePicker === "function") {
    try {
      const handle = await w.showSaveFilePicker({
        suggestedName: filename,
        types: [{ description: "Markdown", accept: { "text/markdown": [".md"] } }],
      });
      const writable = await handle.createWritable();
      await writable.write(blob);
      await writable.close();
      return;
    } catch (err: unknown) {
      if ((err as Error).name === "AbortError") return;
      // fall through to the blob-URL fallback
    }
  }

  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

function AssistantSections({ message }: { message: Message }) {
  const sections = parseAssistantMessage(message.content);

  return (
    <SpaceBetween size="s">
      {sections.map((section, index) => {
        switch (section.type) {
          case "policy":
            return (
              <PolicyViewer
                key={index}
                policy={section.content}
                title={section.title}
                reductionMetrics={section.metrics}
              />
            );
          case "findings":
            return (
              <FindingsTable
                key={index}
                findings={section.findings}
                summary={section.summary}
              />
            );
          case "dependencies":
            return <DependencyGraph key={index} data={section.data} />;
          case "accessKeys":
            return <AccessKeysTable key={index} report={section.report} />;
          case "text":
          default:
            return (
              <div
                key={index}
                style={{
                  color: "var(--color-text-body-default)",
                  lineHeight: 1.6,
                }}
              >
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  rehypePlugins={[rehypeSanitize]}
                  components={MARKDOWN_COMPONENTS}
                >
                  {section.content}
                </ReactMarkdown>
              </div>
            );
        }
      })}
    </SpaceBetween>
  );
}

// --- Message Parsing ---

interface TextSection {
  type: "text";
  content: string;
}

interface PolicySection {
  type: "policy";
  content: string;
  title?: string;
  metrics?: {
    current_actions: number;
    proposed_actions: number;
    removed_actions: number;
    reduction_percentage: number;
  };
}

interface FindingsSection {
  type: "findings";
  findings: Finding[];
  summary?: { severity_breakdown?: Record<string, number> };
}

interface DependenciesSection {
  type: "dependencies";
  data: DependencyResult;
}

interface AccessKeysSection {
  type: "accessKeys";
  report: AccessKeysReport;
}

type MessageSection =
  | TextSection
  | PolicySection
  | FindingsSection
  | DependenciesSection
  | AccessKeysSection;

function parseAssistantMessage(content: string): MessageSection[] {
  const sections: MessageSection[] = [];

  const codeBlockRegex = /```(?:json|python|typescript)?\s*\n([\s\S]*?)\n```/g;
  let lastIndex = 0;
  let match;

  while ((match = codeBlockRegex.exec(content)) !== null) {
    if (match.index > lastIndex) {
      const textBefore = content.slice(lastIndex, match.index).trim();
      if (textBefore) {
        sections.push({ type: "text", content: textBefore });
      }
    }

    const codeContent = match[1].trim();

    if (/^https?:\/\/\S+$/.test(codeContent)) {
      // A lone URL fenced as a code block (e.g. a presigned download link the
      // model wrapped in ```) should be a clickable link, not a monospace box.
      sections.push({ type: "text", content: codeContent });
    } else {
      // Access-keys detection runs BEFORE isPolicyDocument — a raw triage
      // payload {keys, summary, coverage, usage_lag_caveat} has no
      // Version/Statement, but a paranoid future refactor of the tool
      // response shape could otherwise trip the policy branch.
      const accessKeys = tryParseAccessKeysReport(codeContent);
      if (accessKeys) {
        sections.push({ type: "accessKeys", report: accessKeys });
      } else if (isPolicyDocument(codeContent)) {
        sections.push({
          type: "policy",
          content: codeContent,
          title: "IAM Policy",
        });
      } else {
        sections.push({
          type: "policy",
          content: codeContent,
          title: "Code",
        });
      }
    }

    lastIndex = match.index + match[0].length;
  }

  if (lastIndex < content.length) {
    const remaining = content.slice(lastIndex).trim();
    if (remaining) {
      sections.push({ type: "text", content: remaining });
    }
  }

  if (sections.length === 0) {
    sections.push({ type: "text", content });
  }

  return sections;
}

function isPolicyDocument(content: string): boolean {
  try {
    const parsed = JSON.parse(content);
    return (
      parsed.Version === "2012-10-17" ||
      parsed.Statement !== undefined ||
      (parsed.Type && parsed.Properties?.PolicyDocument)
    );
  } catch {
    return (
      content.includes("iam.PolicyDocument(") ||
      content.includes("new iam.PolicyDocument(") ||
      content.includes("iam.PolicyStatement(")
    );
  }
}

// tryParseAccessKeysReport recognizes the triage_access_keys tool response
// payload (#175) as a fenced JSON block. Accepts either an explicit marker
// (_type === "access_keys_report") for future-proofing, or the natural shape
// of the payload — keys[] + summary + coverage[] + usage_lag_caveat.
// Returns null when the content is not a triage payload so the caller falls
// through to the isPolicyDocument branch. Deliberately strict on the shape:
// a nearby-shaped policy JSON must NOT be misclassified as an access-keys
// report.
function tryParseAccessKeysReport(content: string): AccessKeysReport | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(content);
  } catch {
    return null;
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    return null;
  }
  const obj = parsed as Record<string, unknown>;
  if (obj._type === "access_keys_report") {
    return obj as unknown as AccessKeysReport;
  }
  const looksLikeReport =
    Array.isArray(obj.keys) &&
    obj.summary !== undefined &&
    typeof obj.summary === "object" &&
    Array.isArray(obj.coverage) &&
    typeof obj.usage_lag_caveat === "string";
  return looksLikeReport ? (obj as unknown as AccessKeysReport) : null;
}
