import Table from "@cloudscape-design/components/table";
import Box from "@cloudscape-design/components/box";
import Header from "@cloudscape-design/components/header";
import Badge from "@cloudscape-design/components/badge";
import SpaceBetween from "@cloudscape-design/components/space-between";
import { Finding } from "../types";

interface FindingsTableProps {
  findings: Finding[];
  title?: string;
  summary?: {
    severity_breakdown?: Record<string, number>;
    total_count?: number;
  };
}

export default function FindingsTable({
  findings,
  title = "IAM Access Analyzer Findings",
  summary,
}: FindingsTableProps) {
  return (
    <Table
      header={
        <Header
          variant="h3"
          counter={`(${findings.length})`}
          description={
            summary?.severity_breakdown
              ? formatSeverityBreakdown(summary.severity_breakdown)
              : undefined
          }
        >
          {title}
        </Header>
      }
      items={findings}
      columnDefinitions={[
        {
          id: "severity",
          header: "Severity",
          cell: (item) => <SeverityBadge severity={item.severity} />,
          width: 110,
          sortingField: "severity",
        },
        {
          id: "title",
          header: "Finding",
          cell: (item) => (
            <SpaceBetween size="xxxs">
              <Box variant="strong">{item.title}</Box>
              <Box variant="small" color="text-body-secondary">
                {item.description?.slice(0, 120)}
                {(item.description?.length || 0) > 120 ? "..." : ""}
              </Box>
            </SpaceBetween>
          ),
          width: 350,
        },
        {
          id: "resource",
          header: "Resource",
          cell: (item) => (
            <SpaceBetween size="xxxs">
              <Box variant="code">{item.resource_id?.split("/").pop() || item.resource_id}</Box>
              <Box variant="small" color="text-body-secondary">
                {item.resource_type?.replace("AwsIam", "IAM ")}
              </Box>
            </SpaceBetween>
          ),
          width: 200,
        },
        {
          id: "status",
          header: "Status",
          cell: (item) => item.status,
          width: 100,
        },
        {
          id: "recommendation",
          header: "Recommendation",
          cell: (item) => (
            <Box variant="small">
              {item.recommendation?.slice(0, 100)}
              {(item.recommendation?.length || 0) > 100 ? "..." : ""}
            </Box>
          ),
          width: 250,
        },
      ]}
      empty={
        <Box textAlign="center" padding={{ vertical: "l" }}>
          <SpaceBetween size="s">
            <Box variant="h4">No findings</Box>
            <Box variant="p" color="text-body-secondary">
              No IAM Access Analyzer findings match your filters.
              Your IAM posture looks clean!
            </Box>
          </SpaceBetween>
        </Box>
      }
      variant="embedded"
      stickyHeader
      wrapLines
    />
  );
}

function SeverityBadge({ severity }: { severity: string }) {
  const colorMap: Record<string, "red" | "blue" | "grey" | "severity-critical" | "severity-high" | "severity-medium" | "severity-low"> = {
    CRITICAL: "severity-critical",
    HIGH: "severity-high",
    MEDIUM: "severity-medium",
    LOW: "severity-low",
    INFORMATIONAL: "grey",
  };

  return (
    <Badge color={colorMap[severity] || "grey"}>
      {severity}
    </Badge>
  );
}

function formatSeverityBreakdown(breakdown: Record<string, number>): string {
  const parts = [];
  if (breakdown.CRITICAL) parts.push(`${breakdown.CRITICAL} Critical`);
  if (breakdown.HIGH) parts.push(`${breakdown.HIGH} High`);
  if (breakdown.MEDIUM) parts.push(`${breakdown.MEDIUM} Medium`);
  if (breakdown.LOW) parts.push(`${breakdown.LOW} Low`);
  return parts.join(" | ");
}
