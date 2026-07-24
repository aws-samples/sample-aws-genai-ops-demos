import { useState } from "react";
import Container from "@cloudscape-design/components/container";
import Header from "@cloudscape-design/components/header";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import SpaceBetween from "@cloudscape-design/components/space-between";
import StatusIndicator from "@cloudscape-design/components/status-indicator";

interface PolicyViewerProps {
  policy: string;
  title?: string;
  reductionMetrics?: {
    current_actions: number;
    proposed_actions: number;
    removed_actions: number;
    reduction_percentage: number;
  };
}

export default function PolicyViewer({
  policy,
  title = "Generated Policy",
  reductionMetrics,
}: PolicyViewerProps) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    await navigator.clipboard.writeText(policy);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleDownload = async () => {
    const extension = isJson ? "json" : isCdkPython ? "py" : isCdkTs ? "ts" : "md";
    const filename = `iam-policy-${new Date().toISOString().slice(0, 10)}.${extension}`;
    const mimeType = extension === "json" ? "application/json" : "text/plain";
    const blob = new Blob([policy], { type: mimeType });

    // Try modern File System Access API first
    if ((window as unknown as { showSaveFilePicker?: unknown }).showSaveFilePicker) {
      try {
        const handle = await (window as unknown as { showSaveFilePicker: (opts: unknown) => Promise<FileSystemFileHandle> }).showSaveFilePicker({
          suggestedName: filename,
          types: [{ description: "Policy file", accept: { [mimeType]: [`.${extension}`] } }],
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

  // Try to detect format
  const isJson = policy.trim().startsWith("{");
  const isCdkPython = policy.includes("iam.PolicyDocument(");
  const isCdkTs = policy.includes("new iam.PolicyDocument(");

  const language = isCdkPython
    ? "Python (CDK)"
    : isCdkTs
      ? "TypeScript (CDK)"
      : isJson
        ? "JSON"
        : "Text";

  return (
    <Container
      header={
        <Header
          variant="h3"
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button
                iconName="download"
                onClick={handleDownload}
                variant="icon"
                ariaLabel="Download file locally"
              />
              <Button
                iconName={copied ? "status-positive" : "copy"}
                onClick={handleCopy}
                variant="icon"
                ariaLabel="Copy to clipboard"
              />
            </SpaceBetween>
          }
          description={`Format: ${language}`}
        >
          {title}
        </Header>
      }
    >
      <SpaceBetween size="m">
        {reductionMetrics && (
          <div
            style={{
              display: "flex",
              gap: "24px",
              padding: "12px 16px",
              backgroundColor: "#f2f8fd",
              borderRadius: "8px",
              borderLeft: "4px solid #0972d3",
            }}
          >
            <MetricItem
              label="Current Actions"
              value={reductionMetrics.current_actions}
            />
            <MetricItem
              label="Proposed Actions"
              value={reductionMetrics.proposed_actions}
              color="#037f0c"
            />
            <MetricItem
              label="Removed"
              value={reductionMetrics.removed_actions}
              color="#d91515"
            />
            <MetricItem
              label="Reduction"
              value={`${reductionMetrics.reduction_percentage}%`}
              color="#0972d3"
            />
          </div>
        )}

        <div
          style={{
            backgroundColor: "var(--color-background-code-editor-default, #1b1f27)",
            borderRadius: "8px",
            padding: "16px",
            overflow: "auto",
            maxHeight: "400px",
            border: "1px solid var(--color-border-divider-default)",
          }}
        >
          <pre
            style={{
              color: "var(--color-text-code-editor-plain-text, #e6e6e6)",
              fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
              fontSize: "13px",
              lineHeight: "1.5",
              margin: 0,
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
            }}
          >
            {policy}
          </pre>
        </div>

        {copied && (
          <StatusIndicator type="success">
            Copied to clipboard
          </StatusIndicator>
        )}
      </SpaceBetween>
    </Container>
  );
}

function MetricItem({
  label,
  value,
  color,
}: {
  label: string;
  value: string | number;
  color?: string;
}) {
  return (
    <div style={{ textAlign: "center" }}>
      <Box variant="small" color="text-body-secondary">
        {label}
      </Box>
      <Box variant="h3" color="text-status-info">
        <span style={{ color: color || "inherit" }}>{value}</span>
      </Box>
    </div>
  );
}
