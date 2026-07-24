import Container from "@cloudscape-design/components/container";
import Header from "@cloudscape-design/components/header";
import Box from "@cloudscape-design/components/box";
import SpaceBetween from "@cloudscape-design/components/space-between";
import ColumnLayout from "@cloudscape-design/components/column-layout";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import Badge from "@cloudscape-design/components/badge";
import { DependencyResult } from "../types";

interface DependencyGraphProps {
  data: DependencyResult;
}

export default function DependencyGraph({ data }: DependencyGraphProps) {
  const riskLevel = data.risk_score?.level || "LOW";
  const riskColor = getRiskColor(riskLevel);

  return (
    <Container
      header={
        <Header
          variant="h3"
          description={`${data.entity_type}: ${data.entity_name}`}
        >
          Blast Radius Analysis
        </Header>
      }
    >
      <SpaceBetween size="l">
        {/* Risk Score Banner */}
        <div
          style={{
            padding: "12px 16px",
            borderRadius: "8px",
            backgroundColor: riskColor.bg,
            borderLeft: `4px solid ${riskColor.border}`,
          }}
        >
          <SpaceBetween size="xs">
            <Box variant="strong">
              Blast Radius: <Badge color={riskColor.badge as "red" | "blue" | "grey"}>{riskLevel}</Badge>
              {" "}(Score: {data.risk_score?.score || 0}/100)
            </Box>
            <Box variant="small">{data.risk_score?.recommendation}</Box>
            {data.risk_score?.factors?.map((factor, i) => (
              <Box key={i} variant="small" color="text-body-secondary">
                - {factor}
              </Box>
            ))}
          </SpaceBetween>
        </div>

        <ColumnLayout columns={2}>
          {/* Trust Relationships */}
          <SpaceBetween size="s">
            <Box variant="h4">
              Who Can Assume This Role ({data.trust_relationships?.length || 0})
            </Box>
            {data.trust_relationships?.length === 0 && (
              <Box variant="small" color="text-body-secondary">
                No trust relationships found
              </Box>
            )}
            {data.trust_relationships?.map((trust, i) => (
              <div
                key={i}
                style={{
                  padding: "8px 12px",
                  borderRadius: "4px",
                  backgroundColor: "#fafafa",
                  border: "1px solid #e9ebed",
                }}
              >
                <SpaceBetween size="xxxs">
                  <Box variant="code" fontSize="body-s">
                    {trust.principal}
                  </Box>
                  <Box variant="small" color="text-body-secondary">
                    Type: {trust.principal_type || "Unknown"}
                    {trust.risk && trust.risk !== "INFO" && (
                      <> | Risk: <TrustRiskIndicator risk={trust.risk} /></>
                    )}
                  </Box>
                </SpaceBetween>
              </div>
            ))}
          </SpaceBetween>

          {/* Dependents */}
          <SpaceBetween size="s">
            <Box variant="h4">
              Dependents ({data.direct_dependents?.length || 0})
            </Box>
            {data.direct_dependents?.length === 0 && (
              <Box variant="small" color="text-body-secondary">
                No direct dependents found
              </Box>
            )}
            {data.direct_dependents?.map((dep, i) => (
              <div
                key={i}
                style={{
                  padding: "8px 12px",
                  borderRadius: "4px",
                  backgroundColor: "#fafafa",
                  border: "1px solid #e9ebed",
                }}
              >
                <SpaceBetween size="xxxs">
                  <Box variant="strong">
                    <Badge color="blue">{dep.type}</Badge> {dep.name}
                  </Box>
                  {dep.impact && (
                    <Box variant="small" color="text-body-secondary">
                      {dep.impact}
                    </Box>
                  )}
                  {dep.is_service_linked && (
                    <StatusIndicator type="warning">
                      Service-linked (AWS managed)
                    </StatusIndicator>
                  )}
                </SpaceBetween>
              </div>
            ))}
          </SpaceBetween>
        </ColumnLayout>

        {/* Policy Attachments */}
        {data.policy_attachments?.length > 0 && (
          <SpaceBetween size="s">
            <Box variant="h4">
              Attached Policies ({data.policy_attachments.length})
            </Box>
            <div style={{ display: "flex", flexWrap: "wrap", gap: "8px" }}>
              {data.policy_attachments.map((policy, i) => (
                <div
                  key={i}
                  style={{
                    padding: "4px 10px",
                    borderRadius: "4px",
                    backgroundColor: policy.is_aws_managed ? "#f0f5ff" : "#f5f5f5",
                    border: `1px solid ${policy.is_aws_managed ? "#c5d9f2" : "#e9ebed"}`,
                    fontSize: "13px",
                  }}
                >
                  {policy.policy_name}
                  {policy.type === "inline" && (
                    <span style={{ color: "#687078", marginLeft: "4px" }}>(inline)</span>
                  )}
                </div>
              ))}
            </div>
          </SpaceBetween>
        )}

        {/* Warnings */}
        {data.warnings?.length > 0 && (
          <SpaceBetween size="xs">
            {data.warnings.map((warning, i) => (
              <StatusIndicator key={i} type="warning">
                {warning}
              </StatusIndicator>
            ))}
          </SpaceBetween>
        )}
      </SpaceBetween>
    </Container>
  );
}

function TrustRiskIndicator({ risk }: { risk: string }) {
  const type =
    risk === "CRITICAL" ? "error" :
    risk === "HIGH" ? "warning" :
    "info";
  return <StatusIndicator type={type}>{risk}</StatusIndicator>;
}

function getRiskColor(level: string) {
  switch (level) {
    case "CRITICAL":
      return { bg: "var(--color-background-status-error)", border: "var(--color-border-status-error)", badge: "red" };
    case "HIGH":
      return { bg: "var(--color-background-status-warning)", border: "var(--color-border-status-warning)", badge: "red" };
    case "MEDIUM":
      return { bg: "var(--color-background-status-info)", border: "var(--color-border-status-info)", badge: "blue" };
    default:
      return { bg: "var(--color-background-container-content)", border: "var(--color-border-divider-default)", badge: "grey" };
  }
}
