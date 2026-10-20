// How it works: what a scan and a migration do step by step, what every status
// and code means, and where the data comes from. Static reference content,
// Cloudscape only (no custom CSS). The status and code tables read the SAME
// maps the My functions page renders from (constants.ts), so the two pages can
// never drift.
import { ReactNode } from 'react';
import ContentLayout from '@cloudscape-design/components/content-layout';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Table from '@cloudscape-design/components/table';
import Box from '@cloudscape-design/components/box';
import KeyValuePairs from '@cloudscape-design/components/key-value-pairs';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import TextContent from '@cloudscape-design/components/text-content';
import Link from '@cloudscape-design/components/link';
import Icon from '@cloudscape-design/components/icon';
import {
  MIGRATION_STATUS, MIGRATION_RUN_STATUS, MIGRATION_FAILURE_CODES, StatusConfig,
} from '../constants';
import type { MigrationRunStatus } from '../api';

// --- Static content --------------------------------------------------------

interface StepRow {
  step: string;        // the UI phase label (RefreshSteps / MigrationSteps)
  durableStep: string; // the durable step name(s) in pipeline.py / migrate.py
  what: string;
  service: string;
  fails: string;
}

// In order of pipeline.handler; labels are api._progress_from_history's.
const SCAN_STEPS: StepRow[] = [
  {
    step: 'Determine the accounts to scan',
    durableStep: 'start-run',
    what: "Lists the organization's accounts, or just this account when AWS Organizations is not readable.",
    service: 'AWS Organizations',
    fails: 'Falls back to this account only; not an error.',
  },
  {
    step: "Check each account's AWS Support tier",
    durableStep: 'support-<account>',
    what: 'Infers Basic / Developer / Business / Enterprise from the severities the account can open.',
    service: 'AWS Support',
    fails: 'Accounts below Business tier are reported as not checked (Trusted Advisor needs Business or Enterprise).',
  },
  {
    step: 'Discover functions using deprecated runtimes and prioritize',
    durableStep: 'discover-<account>-<region>',
    what: 'Reads Trusted Advisor check L4dfs2Q4C5, enriches each function with its Lambda configuration, tags and CloudWatch invocations; the priority is a rule-based score (alert status, deadline, usage) that Amazon Nova adjusts by at most ±15 from the tags and the function name.',
    service: 'Trusted Advisor, Lambda, CloudWatch, Amazon Bedrock',
    fails: 'The account/region cell is reported as failed; the other cells continue.',
  },
  {
    step: 'Assess migration complexity and risks',
    durableStep: 'list-discovered, assess-<account>-<i>',
    what: 'Downloads each deployment package (source plus manifests such as package.json or requirements.txt) and asks Nova for the complexity, deprecated APIs, breaking changes, dependency issues and risks; the target runtime is fixed per language family by the managed transformation.',
    service: 'Lambda, Amazon Bedrock',
    fails: 'The function is counted as skipped in the scan summary.',
  },
  {
    step: 'Summarize findings and update the inventory',
    durableStep: 'summarize',
    what: 'Writes the scan rollup and marks the functions Trusted Advisor no longer reports as Migrated.',
    service: 'DynamoDB',
    fails: 'The run is reported failed; the inventory keeps the previous rollup.',
  },
];

// In order of migrate._run_batch; labels are api._MIGRATION_PHASE_LABELS.
const MIGRATION_STEPS: StepRow[] = [
  {
    step: 'Prepare the migration batch',
    durableStep: 'prepare-batch',
    what: 'Mints the batch id and applies the 50-function per-run cap (extra selections are reported, never silently dropped).',
    service: 'DynamoDB',
    fails: 'Nothing starts.',
  },
  {
    step: 'Resolve source repositories',
    durableStep: 'resolve-<i>',
    what: 'Reads the repository (and optional branch) tag configured in Settings, checks the repository and branch exist, and uses the family\'s target runtime (nodejs24.x, dotnet10, python3.13).',
    service: 'CodeCommit',
    fails: 'The function is Rejected with a code (see Rejection and failure codes).',
  },
  {
    step: 'Transform with AWS Transform',
    durableStep: 'transform-<i>',
    what: 'Renders the transformation config (the additionalPlanContext directives: upgrade this one function to the target, leave the rest of the repository untouched, plus the non-empty assessment findings as advisory), starts a CodeBuild job that runs the AWS Transform CLI on a fresh clone, pushes the upgrade branch lambda-migration/<target>-<job> when there is a diff, and reports back through EventBridge.',
    service: 'CodeBuild, AWS Transform, S3, EventBridge',
    fails: 'Failed with TRANSFORM_FAILED, PUSH_FAILED or TIMED_OUT.',
  },
  {
    step: 'Settle (only when needed)',
    durableStep: 'settle-<i>',
    what: 'Only runs when the orchestrator has to write the outcome itself (the build timed out or never reported); it never changes a result the build already wrote.',
    service: 'DynamoDB',
    fails: 'The function is Failed with TIMED_OUT or TRANSFORM_FAILED.',
  },
];

// Deprecation axis meanings (the fn# row's migration_status, written by the scan).
const DEPRECATION_MEANING: Record<string, string> = {
  DISCOVERED: 'Found by Trusted Advisor on a deprecated runtime; not yet assessed.',
  ASSESSED: 'Assessed by Nova: complexity, deprecated APIs, breaking changes and dependency issues are known.',
  SKIPPED: 'Container image or unreadable package; cannot be assessed or transformed.',
  MIGRATED: 'No longer reported by Trusted Advisor (inferred after your merged branch is deployed).',
};

// Migration-run axis meanings (the mig# item, merged as migration_run_status).
const RUN_MEANING: Record<MigrationRunStatus, string> = {
  NOT_STARTED: 'No migration run yet.',
  QUEUED: 'Accepted into a batch; the build has not started.',
  IN_PROGRESS: 'AWS Transform is running in CodeBuild.',
  REJECTED: 'Triaged out before any build (see the code); fix the cause and migrate again.',
  FAILED: 'The build, the transformation or the push failed (see the code).',
  NO_CHANGES: 'The transformation found nothing to change; nothing was pushed.',
  BRANCH_READY: 'The upgrade branch is pushed; open the link in the Migration column.',
};

interface StatusRow {
  key: string;
  config: StatusConfig;
  meaning: string;
}

const deprecationRows: StatusRow[] = Object.entries(MIGRATION_STATUS)
  .map(([key, config]) => ({ key, config, meaning: DEPRECATION_MEANING[key] ?? '' }));
const runRows: StatusRow[] = (Object.keys(MIGRATION_RUN_STATUS) as MigrationRunStatus[])
  .map((key) => ({ key, config: MIGRATION_RUN_STATUS[key], meaning: RUN_MEANING[key] }));

interface CodeRow {
  code: string;
  label: string;
  meaning: string;
  next: string;
}

const codeRows: CodeRow[] = Object.entries(MIGRATION_FAILURE_CODES)
  .map(([code, help]) => ({ code, ...help }));

// --- Rendering helpers -----------------------------------------------------

const TA_CHECK_URL = 'https://docs.aws.amazon.com/awssupport/latest/user/security-checks.html#aws-lambda-functions-deprecated-runtimes';

const stepCell = (row: StepRow): ReactNode => (
  <SpaceBetween size="xxxs">
    <Box>{row.step}</Box>
    <Box variant="code" fontSize="body-s" color="text-body-secondary">{row.durableStep}</Box>
  </SpaceBetween>
);

const STEP_COLUMNS = [
  { id: 'step', header: 'Step', cell: stepCell, width: 280 },
  { id: 'what', header: 'What it does', cell: (r: StepRow) => r.what },
  { id: 'service', header: 'AWS service', cell: (r: StepRow) => r.service, width: 220 },
  { id: 'fails', header: 'When it fails', cell: (r: StepRow) => r.fails, width: 320 },
];

const STATUS_COLUMNS = [
  {
    id: 'status', header: 'Status', width: 220,
    cell: (r: StatusRow) => <StatusIndicator type={r.config.type}>{r.config.label}</StatusIndicator>,
  },
  { id: 'meaning', header: 'Meaning', cell: (r: StatusRow) => r.meaning },
];

const CODE_COLUMNS = [
  {
    id: 'code', header: 'Code', width: 260,
    cell: (r: CodeRow) => (
      <SpaceBetween size="xxxs">
        <Box>{r.label}</Box>
        <Box variant="code" fontSize="body-s" color="text-body-secondary">{r.code}</Box>
      </SpaceBetween>
    ),
  },
  { id: 'meaning', header: 'Meaning', cell: (r: CodeRow) => r.meaning },
  { id: 'next', header: 'What to do next', cell: (r: CodeRow) => r.next },
];

function Section({ id, title, description, children }: { id: string; title: string; description?: string; children: ReactNode }) {
  // A plain id on a wrapper div gives the intro anchors a target; no CSS.
  return (
    <div id={id}>
      <Container header={<Header variant="h2" description={description}>{title}</Header>}>
        {children}
      </Container>
    </div>
  );
}

export default function HowItWorksPage() {
  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          description="What a scan and a migration do, step by step, and what every status and code means."
        >
          How it works
        </Header>
      }
    >
      <SpaceBetween size="l">
        <Container>
          <TextContent>
            <p>
              A <strong>scan</strong> finds the Lambda functions running a deprecated runtime across your
              accounts and assesses each one. A <strong>migration</strong> runs AWS Transform on the source
              repository of the functions you select and pushes an upgrade branch; it never touches{' '}
              <code>main</code>. Jump to:{' '}
              <a href="#scan-steps">Scan steps</a> · <a href="#migration-steps">Migration steps</a> ·{' '}
              <a href="#statuses">Function statuses</a> · <a href="#codes">Rejection and failure codes</a> ·{' '}
              <a href="#data-sources">Where the data comes from</a> · <a href="#git-hosts">Other git hosts</a>
            </p>
          </TextContent>
        </Container>

        <Section id="scan-steps" title="Scan steps" description="What “Scan functions” does, in the order the Steps card shows. Each row is one durable step of the scan execution.">
          <Table variant="embedded" wrapLines items={SCAN_STEPS} columnDefinitions={STEP_COLUMNS} trackBy="durableStep" ariaLabels={{ tableLabel: 'Scan steps' }} />
        </Section>

        <Section id="migration-steps" title="Migration steps" description="What “Migrate” does for each selected function, in the order the Steps card shows.">
          <SpaceBetween size="m">
            <Table variant="embedded" wrapLines items={MIGRATION_STEPS} columnDefinitions={STEP_COLUMNS} trackBy="durableStep" ariaLabels={{ tableLabel: 'Migration steps' }} />
            <Box variant="p">
              The flow ends with a pushed branch. <Box variant="code">main</Box> is never changed. Opening the
              pull request, reviewing and merging happen in your repository host. Once the merged code is
              deployed, Trusted Advisor stops reporting the function and the next scan marks it Migrated.
            </Box>
          </SpaceBetween>
        </Section>

        <Section id="statuses" title="Function statuses" description="Two independent axes: where the function stands with Trusted Advisor, and where its migration run stands.">
          <SpaceBetween size="l">
            <SpaceBetween size="xs">
              <Header variant="h3">Deprecation (from the scan)</Header>
              <Table variant="embedded" wrapLines items={deprecationRows} columnDefinitions={STATUS_COLUMNS} trackBy="key" ariaLabels={{ tableLabel: 'Deprecation statuses' }} />
            </SpaceBetween>
            <SpaceBetween size="xs">
              <Header variant="h3">Migration (from a migration run)</Header>
              <Table variant="embedded" wrapLines items={runRows} columnDefinitions={STATUS_COLUMNS} trackBy="key" ariaLabels={{ tableLabel: 'Migration run statuses' }} />
              <Box variant="small" color="text-body-secondary">
                The target runtime is fixed per language family by the managed transformation: nodejs24.x for Node.js,
                dotnet10 for .NET, python3.13 for Python (see <code>backend/transform_runtimes.py</code>); the assessment
                does not suggest one.
              </Box>
            </SpaceBetween>
          </SpaceBetween>
        </Section>

        <Section id="codes" title="Rejection and failure codes" description="Rejected and Failed carry a code; the same text appears in the Migration column popover and the function drawer.">
          <Table variant="embedded" wrapLines items={codeRows} columnDefinitions={CODE_COLUMNS} trackBy="code" ariaLabels={{ tableLabel: 'Rejection and failure codes' }} />
        </Section>

        <Section id="data-sources" title="Where the data comes from">
          <KeyValuePairs
            columns={2}
            items={[
              {
                label: 'Trusted Advisor',
                value: (
                  <SpaceBetween size="xxxs">
                    <Box>
                      Check <Box variant="code">L4dfs2Q4C5</Box> (“AWS Lambda Functions Using Deprecated Runtimes”),
                      the <Box variant="code">$LATEST</Box> version of each function only.
                    </Box>
                    <Link external href={TA_CHECK_URL}>Check documentation</Link>
                  </SpaceBetween>
                ),
              },
              {
                label: <SpaceBetween direction="horizontal" size="xxs"><span>Amazon Nova (Amazon Bedrock)</span><Icon name="gen-ai" size="small" ariaLabel="Generative AI" /></SpaceBetween>,
                value: 'A bounded priority adjustment during discovery, and the migration assessment (complexity, deprecated APIs, breaking changes, dependency issues, risks).',
              },
              {
                label: 'AWS Transform',
                value: (
                  <Box>
                    The <Box variant="code">AWS/python-version-upgrade</Box>, <Box variant="code">AWS/lambda-nodejs-runtime-upgrade</Box>{' '}
                    and <Box variant="code">AWS/dotnet-lambda-upgrade</Box> managed transformations (one per runtime family, see{' '}
                    <Box variant="code">backend/transform_runtimes.py</Box>), run by the <Box variant="code">atx</Box> CLI inside CodeBuild.
                  </Box>
                ),
              },
              {
                label: 'What is modified',
                value: 'Nothing in your account except the pushed branch in the source repository; no Lambda function is changed.',
              },
            ]}
          />
        </Section>

        <Section id="git-hosts" title="Other git hosts">
          <Box variant="p">
            This demo targets AWS CodeCommit. GitLab, GitHub and Bitbucket differ only in how the build
            authenticates (the secret named by <Box variant="code">secretRef</Box> in Settings) and in the
            branch link; the flow itself is a plain <Box variant="code">git clone</Box> and{' '}
            <Box variant="code">git push</Box> of one branch.
          </Box>
        </Section>
      </SpaceBetween>
    </ContentLayout>
  );
}
