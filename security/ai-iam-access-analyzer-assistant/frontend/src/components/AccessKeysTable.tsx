// AccessKeysTable renders the JSON payload from the triage_access_keys tool
// (#175) as a sortable Cloudscape Table sitting inside the assistant's
// message bubble. Column set, priority coloring, and the deactivate →
// monitor → delete safety framing come from the analysis-criteria
// reference document at src/tools/references/access_key_analysis_criteria.md.
//
// Cloudscape idioms match FindingsTable — variant="embedded" with sticky
// header and wrapped lines, per-column subpath imports, Badge sub-component
// for enum status cells, StatusIndicator for row-level severity.
import { useState } from "react";
import Table from "@cloudscape-design/components/table";
import Box from "@cloudscape-design/components/box";
import Header from "@cloudscape-design/components/header";
import Badge from "@cloudscape-design/components/badge";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import SpaceBetween from "@cloudscape-design/components/space-between";
import ColumnLayout from "@cloudscape-design/components/column-layout";
import Button from "@cloudscape-design/components/button";
import Link from "@cloudscape-design/components/link";
import type { AccessKeyRow, AccessKeysReport } from "../types";

interface AccessKeysTableProps {
  report: AccessKeysReport;
  title?: string;
}

// Priority classes rendered as StatusIndicator variants. The mapping matches
// the severity ladder in access_key_analysis_criteria.md.
const PRIORITY_INDICATOR: Record<
  string,
  "error" | "warning" | "info" | "pending"
> = {
  Critical: "error",
  High: "warning",
  Cleanup: "info",
  Rotation: "pending",
};

const PRIORITY_ORDER: Record<string, number> = {
  Critical: 0,
  High: 1,
  Cleanup: 2,
  Rotation: 3,
};

// Which columns are user-sortable. Sort direction is handled by Cloudscape
// via sortingField; a small comparator array feeds SortingComparator for
// columns whose natural ordering differs from string / number.
type SortableColumnId = "priority" | "age" | "last_used";

export default function AccessKeysTable({ report, title }: AccessKeysTableProps) {
  const [sorting, setSorting] = useState<{
    column: SortableColumnId;
    isDescending: boolean;
  }>({
    column: "priority",
    isDescending: false,
  });
  const [expanded, setExpanded] = useState<AccessKeyRow[]>([]);

  // If any coverage entry reports the iam source as unavailable, the tool
  // could not run at all in this account or region. Do not render a
  // half-empty table; render the coverage banner only. The response prose
  // carries the actual "what to do next" per the #180 COVERAGE HANDLING
  // prompt rule.
  const iamUnavailable = report.coverage?.some(
    (c) => c.source === "iam" && c.state === "unavailable",
  );

  const coverageEntries = report.coverage || [];

  if (iamUnavailable) {
    return (
      <div style={{ padding: "8px 0" }}>
        <SpaceBetween size="s">
          <Header variant="h3">{title || "IAM access-key triage"}</Header>
          {coverageEntries
            .filter((c) => c.state === "unavailable")
            .map((c, i) => (
              <StatusIndicator key={i} type="error">
                {c.source}: {c.detail || "unavailable"}
              </StatusIndicator>
            ))}
          <Box variant="small" color="text-body-secondary">
            The audit could not read IAM in this account or region — no
            key inventory to show. See the assistant response for
            remediation.
          </Box>
        </SpaceBetween>
      </div>
    );
  }

  const rows = [...report.keys].sort((a, b) => cmp(a, b, sorting));

  return (
    <div style={{ padding: "8px 0" }}>
      <SpaceBetween size="s">
        <Table<AccessKeyRow>
          variant="embedded"
          stickyHeader
          wrapLines
          items={rows}
          trackBy="key_id"
          sortingColumn={{ sortingField: sorting.column }}
          sortingDescending={sorting.isDescending}
          onSortingChange={(event) => {
            const detail = event.detail;
            const field =
              (detail.sortingColumn?.sortingField as SortableColumnId) ||
              "priority";
            setSorting({
              column: field,
              isDescending: !!detail.isDescending,
            });
          }}
          expandableRows={{
            getItemChildren: () => [],
            isItemExpandable: () => true,
            expandedItems: expanded,
            onExpandableItemToggle: (event) => {
              const item = event.detail.item;
              setExpanded((prev) =>
                event.detail.expanded
                  ? [...prev, item]
                  : prev.filter((r) => r.key_id !== item.key_id),
              );
            },
          }}
          header={
            <Header
              variant="h3"
              counter={`(${report.summary.total_keys})`}
              description={formatSummary(report.summary)}
            >
              {title || "IAM access-key triage"}
            </Header>
          }
          columnDefinitions={[
            {
              id: "priority",
              header: "Priority",
              cell: (item) => <PriorityBadge priority={item.priority_class} />,
              width: 120,
              sortingField: "priority",
            },
            {
              id: "account",
              header: "Account",
              cell: (item) => (
                <Box variant="code">
                  {item.account_id || "-"}
                </Box>
              ),
              width: 140,
            },
            {
              id: "user",
              header: "User",
              cell: (item) => (
                <SpaceBetween size="xxxs" direction="horizontal">
                  <Box variant="strong">{item.user}</Box>
                  {item.is_root ? (
                    <Badge color="red">root</Badge>
                  ) : null}
                </SpaceBetween>
              ),
              width: 200,
            },
            {
              id: "key_id",
              header: "Key ID",
              cell: (item) => <KeyIdCell keyId={item.key_id} />,
              width: 180,
            },
            {
              id: "age",
              header: "Age (days)",
              cell: (item) => (
                <Box
                  color={
                    (item.key_age_days || 0) >= 365
                      ? "text-status-warning"
                      : undefined
                  }
                  textAlign="right"
                >
                  {item.key_age_days ?? "-"}
                </Box>
              ),
              width: 110,
              sortingField: "age",
            },
            {
              id: "last_used",
              header: "Last used",
              cell: (item) => <LastUsedCell row={item} />,
              width: 160,
              sortingField: "last_used",
            },
            {
              id: "risk_flags",
              header: "Risk flags",
              cell: (item) => (
                <SpaceBetween size="xxxs" direction="horizontal">
                  {item.risk_flags.map((f, i) => (
                    <Badge key={i} color={badgeColorForFlag(f)}>
                      {f}
                    </Badge>
                  ))}
                </SpaceBetween>
              ),
              width: 340,
            },
            {
              id: "remediation",
              header: "Suggested remediation",
              cell: (item) => {
                const label = formatRemediation(item.suggested_remediation);
                // Render as an external link into AWS docs when the tool
                // supplied a URL. Falls back to plain text for unrecognized
                // remediation labels — the tool intentionally returns an
                // empty string for unknown labels rather than fabricating
                // a URL.
                if (item.suggested_remediation_url) {
                  return (
                    <Box variant="small">
                      <Link
                        href={item.suggested_remediation_url}
                        external
                        externalIconAriaLabel="Opens AWS documentation in a new tab"
                      >
                        {label}
                      </Link>
                    </Box>
                  );
                }
                return <Box variant="small">{label}</Box>;
              },
              width: 240,
            },
          ]}
          empty={
            <Box textAlign="center" padding={{ vertical: "l" }}>
              <SpaceBetween size="s">
                <Box variant="h4">No access keys</Box>
                <Box variant="p" color="text-body-secondary">
                  No IAM users in this account have access keys. That
                  is the recommended posture — long-term keys are the
                  top breach vector.
                </Box>
              </SpaceBetween>
            </Box>
          }
          // Row expansion shows the effective policy, source policies,
          // and resource scope for the selected key — the details an
          // operator needs when deciding what to migrate.
          resizableColumns
        />

        {/* Coverage entries that aren't 'unavailable' render as inline
             StatusIndicator lines so partial-data conditions surface to
             the operator without duplicating them into the response
             prose. Same idiom DependencyGraph uses for its warnings. */}
        {coverageEntries.length > 0 && (
          <SpaceBetween size="xxs">
            {coverageEntries.map((c, i) => (
              <StatusIndicator
                key={i}
                type={coverageStateType(c.state)}
              >
                {c.source}: {c.detail || c.state}
              </StatusIndicator>
            ))}
          </SpaceBetween>
        )}

        {report.usage_lag_caveat && (
          <Box variant="small" color="text-body-secondary">
            <strong>Note:</strong> {report.usage_lag_caveat}
          </Box>
        )}

        {/* Expanded rows can't drop custom content back into the Table
             body in Cloudscape 3 without a nested table renderer, so
             render an inline detail panel below the table for each
             expanded row. */}
        {expanded.length > 0 && (
          <SpaceBetween size="s">
            {expanded.map((row) => (
              <ExpandedRowDetail key={row.key_id} row={row} />
            ))}
          </SpaceBetween>
        )}
      </SpaceBetween>
    </div>
  );
}

// -- Cell / badge sub-components ---------------------------------------------

function PriorityBadge({ priority }: { priority: string }) {
  const type = PRIORITY_INDICATOR[priority] || "info";
  return <StatusIndicator type={type}>{priority}</StatusIndicator>;
}

function KeyIdCell({ keyId }: { keyId: string }) {
  // The useState hook must run on every render — placing it BEFORE the
  // early-return guard, per the Rules of Hooks. A "(root)" key or an empty
  // key still returns from this component, but only after the hook count
  // is stable across renders.
  const [copied, setCopied] = useState(false);
  if (!keyId || keyId === "(root)") {
    return <Box variant="code">{keyId || "-"}</Box>;
  }
  const short =
    keyId.length > 8 ? `${keyId.slice(0, 4)}…${keyId.slice(-4)}` : keyId;
  return (
    <SpaceBetween size="xxxs" direction="horizontal">
      <Box variant="code">{short}</Box>
      <Button
        iconName="copy"
        variant="inline-icon"
        // aria-label mirrors the visible truncation (`AKIA…XXXX`) rather
        // than the full key ID. On fixture data this is cosmetic, but on
        // real data the full AKIA in the accessibility tree is a needless
        // exposure — screen readers can announce the truncated form; a
        // user who needs the whole string clicks the button.
        ariaLabel={`Copy ${short}`}
        onClick={async () => {
          try {
            await navigator.clipboard.writeText(keyId);
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
          } catch {
            /* clipboard blocked — silent, the operator can select the
               short-form text manually */
          }
        }}
      />
      {copied ? (
        <Box variant="small" color="text-status-success">
          copied
        </Box>
      ) : null}
    </SpaceBetween>
  );
}

function LastUsedCell({ row }: { row: AccessKeyRow }) {
  if (row.last_used === "UNKNOWN") {
    return <Badge color="grey">Unknown</Badge>;
  }
  if (row.last_used === "NEVER" || !row.last_used) {
    return <Badge color="grey">Never</Badge>;
  }
  const date = row.last_used.slice(0, 10);
  return (
    <SpaceBetween size="xxxs">
      <Box>{date}</Box>
      {row.last_used_service ? (
        <Box variant="small" color="text-body-secondary">
          {row.last_used_service}
        </Box>
      ) : null}
    </SpaceBetween>
  );
}

function ExpandedRowDetail({ row }: { row: AccessKeyRow }) {
  return (
    <div
      style={{
        padding: "12px 16px",
        background: "var(--color-background-container-content)",
        border: "1px solid var(--color-border-divider-default)",
        borderRadius: "4px",
      }}
    >
      <SpaceBetween size="xs">
        <Header variant="h3">
          {row.user}
          {row.key_id && row.key_id !== "(root)"
            ? ` • ${row.key_id.slice(0, 4)}…${row.key_id.slice(-4)}`
            : ""}
        </Header>
        <ColumnLayout columns={2} variant="text-grid">
          <div>
            <Box variant="awsui-key-label">Resource scope</Box>
            <div>{row.resource_scope}</div>
          </div>
          <div>
            <Box variant="awsui-key-label">Has condition</Box>
            <div>{row.has_condition ? "yes" : "no"}</div>
          </div>
        </ColumnLayout>
        <div>
          <Box variant="awsui-key-label">Effective actions</Box>
          <Box variant="code">
            <span style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
              {row.actions}
            </span>
          </Box>
        </div>
        <div>
          <Box variant="awsui-key-label">Source policies</Box>
          <Box variant="small">{row.policies}</Box>
        </div>
        {row.suggested_remediation_steps && row.suggested_remediation_steps.length > 0 && (
          <div>
            <Box variant="awsui-key-label">Migration steps</Box>
            <Box variant="small" color="text-body-secondary">
              Deactivate → monitor a full business cycle → delete. Never a bare delete on an in-use key.
            </Box>
            <ol style={{ marginTop: "8px", paddingLeft: "20px" }}>
              {row.suggested_remediation_steps.map((step, i) => (
                <li key={i} style={{ marginBottom: "6px", lineHeight: 1.4 }}>
                  <Box variant="small">{step}</Box>
                </li>
              ))}
            </ol>
            {row.suggested_remediation_url && (
              <Box variant="small" margin={{ top: "xs" }}>
                <Link
                  href={row.suggested_remediation_url}
                  external
                  externalIconAriaLabel="Opens AWS documentation in a new tab"
                >
                  Open AWS documentation
                </Link>
              </Box>
            )}
          </div>
        )}
      </SpaceBetween>
    </div>
  );
}

// -- Helpers ------------------------------------------------------------------

function cmp(
  a: AccessKeyRow,
  b: AccessKeyRow,
  sorting: { column: SortableColumnId; isDescending: boolean },
): number {
  let base: number;
  switch (sorting.column) {
    case "priority":
      base =
        (PRIORITY_ORDER[a.priority_class] ?? 4) -
        (PRIORITY_ORDER[b.priority_class] ?? 4);
      if (base === 0) {
        // Within a priority class, sort by descending age. Matches the
        // tool's server-side sort so the initial render is stable.
        base = (b.key_age_days ?? 0) - (a.key_age_days ?? 0);
      }
      break;
    case "age":
      base = (a.key_age_days ?? -1) - (b.key_age_days ?? -1);
      break;
    case "last_used":
      base = lastUsedRank(a) - lastUsedRank(b);
      break;
  }
  return sorting.isDescending ? -base : base;
}

function lastUsedRank(row: AccessKeyRow): number {
  // Sort order for "Last used": UNKNOWN and NEVER go to the bottom, then
  // dated rows sort by their ISO string (which orders chronologically).
  if (row.last_used === "UNKNOWN") return Number.POSITIVE_INFINITY;
  if (row.last_used === "NEVER" || !row.last_used)
    return Number.POSITIVE_INFINITY - 1;
  const t = Date.parse(row.last_used);
  return Number.isFinite(t) ? -t : Number.POSITIVE_INFINITY;
}

function badgeColorForFlag(
  flag: string,
): "red" | "blue" | "grey" | "severity-critical" | "severity-high" | "severity-medium" | "severity-low" {
  if (flag === "ADMIN" || flag.startsWith("KEY_AGE_")) return "severity-critical";
  if (flag.startsWith("BROAD:") || flag.startsWith("SERVICE_WILDCARD:"))
    return "severity-high";
  if (flag === "RESOURCE_WILDCARD" || flag === "MULTI_ACTIVE_KEYS")
    return "severity-medium";
  if (flag === "LASTUSED_UNKNOWN") return "grey";
  if (flag === "NEVER_USED" || flag.startsWith("IDLE_")) return "severity-low";
  return "grey";
}

function coverageStateType(
  state: string,
): "success" | "warning" | "error" | "info" {
  switch (state) {
    case "checked":
      return "success";
    case "empty":
      return "info";
    case "unavailable":
      return "error";
    default:
      return "info";
  }
}

function formatSummary(summary: AccessKeysReport["summary"]): string {
  const bits: string[] = [];
  if (summary.Critical) bits.push(`${summary.Critical} Critical`);
  if (summary.High) bits.push(`${summary.High} High`);
  if (summary.Cleanup) bits.push(`${summary.Cleanup} Cleanup`);
  if (summary.Rotation) bits.push(`${summary.Rotation} Rotation`);
  const users = summary.users_with_keys;
  const prefix = bits.length > 0 ? bits.join(" · ") : "no risky keys";
  return `${prefix} across ${users} user${users === 1 ? "" : "s"} with access keys`;
}

function formatRemediation(value: string): string {
  // Map the tool's raw remediation labels to a scanner-friendly phrasing
  // without paraphrasing them away — the ACCESS KEY TRIAGE prompt rule
  // requires the model to quote the raw label, but the table itself can
  // spell them out for readability.
  switch (value) {
    case "SSO_Federation":
      return "Federate via your SSO provider";
    case "IAM_Role":
      return "Replace with an IAM role";
    case "OIDC_Federation":
      return "Move to OIDC federation";
    case "Cross_Account_Role_With_External_Id":
      return "Cross-account role + external ID (investigate first)";
    case "IAM_Roles_Anywhere":
      return "Use IAM Roles Anywhere";
    case "Remove_Root_Access_Keys":
      return "Remove root access keys (Critical)";
    default:
      return value;
  }
}
