import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import SpaceBetween from "@cloudscape-design/components/space-between";
import PolicyViewer from "./PolicyViewer";
import FindingsTable from "./FindingsTable";
import DependencyGraph from "./DependencyGraph";
import { Message, Finding, DependencyResult } from "../types";

interface MessageBubbleProps {
  message: Message;
}

export default function MessageBubble({ message }: MessageBubbleProps) {
  const isUser = message.role === "user";

  if (isUser) {
    return (
      <div style={{ display: "flex", justifyContent: "flex-end", padding: "4px 0" }}>
        <div
          style={{
            maxWidth: "75%",
            padding: "12px 16px",
            borderRadius: "12px",
            backgroundColor: "var(--color-background-button-primary-default)",
            color: "var(--color-text-button-primary-default, #ffffff)",
          }}
        >
          <Box variant="p">
            <span style={{ whiteSpace: "pre-wrap", lineHeight: "1.5", color: "inherit" }}>
              {message.content}
            </span>
          </Box>
        </div>
      </div>
    );
  }

  // Assistant message — detect structured content
  const sections = parseAssistantMessage(message.content);

  return (
    <div style={{ display: "flex", justifyContent: "flex-start", padding: "4px 0" }}>
      <div style={{ maxWidth: "90%", width: "100%" }}>
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
                return (
                  <DependencyGraph key={index} data={section.data} />
                );
              case "text":
              default:
                return (
                  <div
                    key={index}
                    style={{
                      padding: "12px 16px",
                      borderRadius: "12px",
                      backgroundColor: "var(--color-background-container-content)",
                      color: "var(--color-text-body-default)",
                      border: "1px solid var(--color-border-divider-default)",
                      position: "relative",
                    }}
                  >
                    <div
                      style={{ whiteSpace: "pre-wrap", lineHeight: "1.6" }}
                      dangerouslySetInnerHTML={{
                        __html: formatMarkdown(section.content),
                      }}
                    />
                    {section.content.length > 200 && (
                      <div style={{ marginTop: "8px", borderTop: "1px solid var(--color-border-divider-default)", paddingTop: "8px" }}>
                        <DownloadButton content={message.content} />
                      </div>
                    )}
                  </div>
                );
            }
          })}
        </SpaceBetween>
      </div>
    </div>
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

type MessageSection = TextSection | PolicySection | FindingsSection | DependenciesSection;

function parseAssistantMessage(content: string): MessageSection[] {
  const sections: MessageSection[] = [];

  // Try to detect JSON code blocks with policy content
  const codeBlockRegex = /```(?:json|python|typescript)?\s*\n([\s\S]*?)\n```/g;
  let lastIndex = 0;
  let match;

  while ((match = codeBlockRegex.exec(content)) !== null) {
    // Text before the code block
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
    } else if (isPolicyDocument(codeContent)) {
      sections.push({
        type: "policy",
        content: codeContent,
        title: "IAM Policy",
      });
    } else {
      // Generic code block — still render as policy viewer (syntax highlighted)
      sections.push({
        type: "policy",
        content: codeContent,
        title: "Code",
      });
    }

    lastIndex = match.index + match[0].length;
  }

  // Remaining text after last code block
  if (lastIndex < content.length) {
    const remaining = content.slice(lastIndex).trim();
    if (remaining) {
      sections.push({ type: "text", content: remaining });
    }
  }

  // If no code blocks found, return as single text section
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
    // Check for CDK patterns
    return (
      content.includes("iam.PolicyDocument(") ||
      content.includes("new iam.PolicyDocument(") ||
      content.includes("iam.PolicyStatement(")
    );
  }
}

function DownloadButton({ content }: { content: string }) {
  const handleDownload = async () => {
    const filename = `iam-analysis-${new Date().toISOString().slice(0, 10)}.md`;
    const blob = new Blob([content], { type: "text/markdown" });

    // Try modern File System Access API first (proper Save As dialog)
    if ((window as unknown as { showSaveFilePicker?: unknown }).showSaveFilePicker) {
      try {
        const handle = await (window as unknown as { showSaveFilePicker: (opts: unknown) => Promise<FileSystemFileHandle> }).showSaveFilePicker({
          suggestedName: filename,
          types: [{ description: "Markdown", accept: { "text/markdown": [".md"] } }],
        });
        const writable = await handle.createWritable();
        await writable.write(blob);
        await writable.close();
        return;
      } catch (err: unknown) {
        if ((err as Error).name === "AbortError") return;
      }
    }

    // Fallback: blob URL with download attribute
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  return (
    <Button iconName="download" variant="normal" onClick={handleDownload}>
      Save as .md
    </Button>
  );
}

function formatMarkdown(content: string): string {
  return content
    .replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>")
    .replace(/\*(.*?)\*/g, "<em>$1</em>")
    .replace(/`(.*?)`/g, '<code style="background:var(--color-background-code-editor-gutter-default, #2a2d35);color:var(--color-text-code-editor-plain-text, #e0e0e0);padding:2px 6px;border-radius:4px;font-size:12px;font-family:monospace;">$1</code>')
    // Lenient: the closing ')' is optional and the URL runs to the next space.
    // Long presigned URLs sometimes arrive without the closing paren (truncated
    // mid-line); the strict form left those as raw text. This still renders a
    // clean link using the label, hiding the URL.
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)?/g, '<a href="$2" target="_blank" rel="noopener noreferrer" style="color:var(--color-text-link-default);word-break:break-all;">$1</a>')
    // Auto-linkify bare URLs (e.g. presigned S3 download links) that the model
    // emitted without markdown link syntax. The lookbehind skips URLs already
    // inside an anchor (href="…), a markdown link '(…', or after '>' so we never
    // double-wrap the links handled by the rule above.
    .replace(/(?<!["=(])(https?:\/\/[^\s<>()[\]]+)/g, '<a href="$1" target="_blank" rel="noopener noreferrer" style="color:var(--color-text-link-default);word-break:break-all;">$1</a>')
    .replace(/^### (.*$)/gm, '<h4 style="margin:8px 0 4px;">$1</h4>')
    .replace(/^## (.*$)/gm, '<h3 style="margin:12px 0 4px;">$1</h3>')
    .replace(/^- (.*$)/gm, '<li style="margin:2px 0;">$1</li>')
    .replace(/(<li.*<\/li>\n?)+/g, '<ul style="margin:4px 0;padding-left:20px;">$&</ul>')
    .replace(/\n\n/g, '<br/><br/>')
    .replace(/\n/g, '<br/>');
}
