// CrossAccountRolesTable renders the cross_account_iam_context tool result
// (#173 Level 2) as a Cloudscape Table inside the assistant's message
// bubble. The backend short-circuit embeds the structured payload as a
// fenced `_type: "cross_account_roles"` JSON block specifically so the
// model never has to re-format this into markdown — that second Bedrock
// round was the dominant latency (10-18s) this feature removes.
//
// Cloudscape idioms match AccessKeysTable/FindingsTable — variant="embedded"
// with sticky header and wrapped lines, per-column subpath imports, Badge
// for the high-privilege flag, StatusIndicator for CloudTrail reachability.
import Table from "@cloudscape-design/components/table";
import Box from "@cloudscape-design/components/box";
import Header from "@cloudscape-design/components/header";
import Badge from "@cloudscape-design/components/badge";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import SpaceBetween from "@cloudscape-design/components/space-between";
import type { CrossAccountRoleRow, CrossAccountRolesReport } from "../types";

interface CrossAccountRolesTableProps {
  report: CrossAccountRolesReport;
}

export default function CrossAccountRolesTable({
  report,
}: CrossAccountRolesTableProps) {
  const roles = report.roles ?? [];
  const scope = report.is_hub_account
    ? "this hub account"
    : `account ${report.account_id}`;
  const highPrivCount = roles.filter((r) => r.high_privilege).length;

  return (
    <SpaceBetween size="s">
      <Table<CrossAccountRoleRow>
        variant="embedded"
        stickyHeader
        wrapLines
        items={roles}
        header={
          <Header
            counter={
              report.has_more
                ? `(${report.returned_count}+)`
                : `(${report.returned_count})`
            }
            description={
              highPrivCount > 0
                ? `${highPrivCount} flagged high-privilege (admin/full-access managed policy attached)`
                : "No roles flagged high-privilege in this page"
            }
          >
            IAM roles in {scope}
          </Header>
        }
        columnDefinitions={[
          {
            id: "name",
            header: "Role",
            cell: (item) => item.name,
            isRowHeader: true,
          },
          {
            id: "high_privilege",
            header: "Privilege",
            cell: (item) =>
              item.high_privilege ? (
                <Badge color="red">High</Badge>
              ) : (
                <Badge color="grey">Standard</Badge>
              ),
          },
          {
            id: "create_date",
            header: "Created",
            cell: (item) => item.create_date || "—",
          },
          {
            id: "arn",
            header: "ARN",
            cell: (item) => (
              <Box variant="code" fontSize="body-s">
                {item.arn}
              </Box>
            ),
          },
        ]}
        empty={
          <Box textAlign="center" color="inherit">
            No IAM roles returned for {scope}.
          </Box>
        }
      />
      <Box fontSize="body-s">
        <StatusIndicator
          type={report.cloudtrail_reachable ? "success" : "warning"}
        >
          {report.cloudtrail_reachable
            ? `CloudTrail reachable in ${scope}`
            : `CloudTrail not reachable in ${scope} — usage-based analysis would be incomplete`}
        </StatusIndicator>
        {report.has_more && (
          <Box variant="small" padding={{ top: "xxs" }}>
            More roles are available — ask for a higher limit or a narrower
            view.
          </Box>
        )}
      </Box>
    </SpaceBetween>
  );
}
