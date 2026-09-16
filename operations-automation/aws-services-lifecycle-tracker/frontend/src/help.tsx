// Help panel content per page (Cloudscape help system): pages put an Info link
// in their header, App renders the matching HelpPanel in the AppLayout tools slot.
import { createContext, useContext, ReactNode } from 'react';
import Box from '@cloudscape-design/components/box';
import HelpPanel from '@cloudscape-design/components/help-panel';
import Icon from '@cloudscape-design/components/icon';
import Link from '@cloudscape-design/components/link';

export const HelpContext = createContext<() => void>(() => {});

// Header `info` slot: opens the tools panel for the current page
export const InfoLink = () => {
  const openHelp = useContext(HelpContext);
  return <Link variant="info" onFollow={(e) => { e.preventDefault(); openHelp(); }} href="#">Info</Link>;
};

const Dl = ({ items }: { items: Array<[string, ReactNode]> }) => (
  <dl>
    {items.map(([term, def]) => (
      <div key={term}>
        <dt>{term}</dt>
        <dd>{def}</dd>
      </div>
    ))}
  </dl>
);

const DOCS = 'https://github.com/aws-samples/sample-aws-genai-ops-demos/tree/main/operations-automation/aws-services-lifecycle-tracker';

// Transparency block (Cloudscape generative AI principles: capabilities and
// limits, user control, errors). Reused on every page that shows AI output.
const GenAiNotice = () => (
  <>
    <h3><Icon name="gen-ai" size="small" /> Generative AI in this demo</h3>
    <p>The catalog is the only generative AI output. During a refresh, Amazon Bedrock (Amazon Nova 2 Lite) reads the version tables of each AWS documentation page listed on Sources &amp; coverage and returns the version facts as structured data: version, dates, status. Nothing else is generated: the account scan, the matching, the Health cross-check and the cost estimate are plain code.</p>
    <Dl items={[
      ['What can go wrong', 'A date read from a badly formatted table, a version missed, a status inferred wrongly. Every fact keeps a link to its source page: verify before you act on it.'],
      ['Your control', 'Disable a service on Sources & coverage to stop extracting it, or re-run one service there. The catalog has no effect on your AWS resources.'],
      ['Errors', 'A page the model could not turn into facts counts as a failed extraction: see Success rate on Sources & coverage and the refresh steps on My exposure.'],
      ['Your data', 'Only public documentation pages are sent to the model. Resource names, ARNs and account IDs never leave your account.'],
    ]} />
  </>
);

const HELP: Record<string, { header: string; content: ReactNode }> = {
  '/dashboard': {
    header: 'My exposure',
    content: (
      <>
        <p>How much of what runs in your accounts sits on a version AWS is retiring, and what it may cost.</p>
        <h3>Refresh</h3>
        <p>One end-to-end run, executed server-side as a Lambda durable function; it continues if you leave the page.</p>
        <ol>
          <li>Catalog (generative AI, sparkle icon): Amazon Bedrock extracts the deprecation facts from the AWS documentation for every enabled service.</li>
          <li>Scan: lists resources in your accounts and regions with the account scanners (see Sources &amp; coverage) and matches each version against the catalog.</li>
          <li>Reconcile: updates your inventory, cross-checks open AWS Health notices and publishes a summary to SNS.</li>
        </ol>
        <h3>Summary figures</h3>
        <Dl items={[
          ['Past end of life', 'Resources on a version whose end of support date has passed.'],
          ['Ending in 90 days / in a year', 'Resources with a deadline in that window.'],
          ['Fine for now', 'Resources on a supported version, or on a version the catalog does not know.'],
          ['Extended Support now', 'What RDS and Aurora Extended Support bills today at current size, always on, and what is coming with its start month. A list-price estimate, not a bill.'],
        ]} />
        <GenAiNotice />
      </>
    ),
  },
  '/resources': {
    header: 'My resources',
    content: (
      <>
        <p>Every runtime, engine or platform version the account scan found, one row per version, account and region. Open a row to list the resources behind it.</p>
        <h3>Status</h3>
        <Dl items={[
          ['End of life', 'The version is no longer supported; AWS may block updates or force upgrades.'],
          ['Deprecated', 'Announced for retirement, with a date.'],
          ['Past standard support', 'RDS or Aurora versions billing Extended Support.'],
          ['Ending within a year', 'Standard support ends within 12 months.'],
          ['Supported', 'Nothing to do for now.'],
          ['Not matched', 'Found in your account but absent from the catalog: check the version manually.'],
        ]} />
        <h3>Cost exposure</h3>
        <p>RDS and Aurora only. Price List rate for this region times vCPU (or ACU) hours, always on; Multi-AZ counts twice. Serverless v2 uses max ACU.</p>
        <h3>Console links</h3>
        <p>They open in the account your browser is signed into. With several accounts, sign in to the account shown on the row first; AWS Health events are visible only from their own account.</p>
      </>
    ),
  },
  '/catalog': {
    header: 'Catalog',
    content: (
      <>
        <p>The deprecation facts extracted from the AWS documentation: one row per service version with its dates and the source page. This is what your resources are matched against.</p>
        <h3>Scope</h3>
        <Dl items={[
          ['Needs attention', 'Versions that are end of life, deprecated, past standard support or ending within a year.'],
          ['Everything', 'Including versions still supported.'],
          ['Only what I run', 'Restrict to versions found in your accounts.'],
        ]} />
        <GenAiNotice />
      </>
    ),
  },
  '/timeline': {
    header: 'Timeline',
    content: (
      <>
        <p>Deadlines ordered by date, grouped by horizon.</p>
        <Dl items={[
          ['My resources', 'Only the versions running in your accounts; a version in several accounts or regions is one entry. Dates that passed in the last year are shown too.'],
          ['Whole catalog', 'Every upcoming deadline in the catalog, whether or not you run the version.'],
        ]} />
        <h3>Deadline kinds</h3>
        <p>Deprecation, end of standard support, end of support, retirement, blocked updates or creation (Lambda), end of Extended Support. A version can have several.</p>
      </>
    ),
  },
  '/plan-of-action': {
    header: 'Plan of Action',
    content: (
      <>
        <p>Who upgrades what, by when. A plan is attached to one version running in your accounts; the owner shows on that row in My resources.</p>
        <h3>Status</h3>
        <Dl items={[
          ['Not started', 'Assigned, no work yet.'],
          ['In progress', 'Upgrade under way.'],
          ['Blocked', 'Waiting on something; sorted first.'],
          ['Completed', 'Done; the row disappears from My resources after the next scan if the old version is gone.'],
        ]} />
        <p>Plans are stored in this demo's DynamoDB table only.</p>
      </>
    ),
  },
  '/services': {
    header: 'Sources & coverage',
    content: (
      <>
        <p>Where the catalog comes from and what the account scan covers.</p>
        <Dl items={[
          ['Enabled', 'Disabled services are skipped by Refresh and by the schedule.'],
          ['Account scanner', 'Services with a scanner have their resources listed and matched; "facts only" means the catalog knows the service but nothing is scanned for it.'],
          ['Catalog updated / Updates / Duration / Success rate', 'History of the extraction for that service.'],
          ['Scan targets', 'Which accounts (single account or an AWS Organizations root or OU) and regions the scan covers; changes apply at the next Refresh.'],
        ]} />
        <h3>Extended Support pricing and AWS Health</h3>
        <p>The footer says whether the Price List API answered for this region and whether the AWS Health cross-check ran (it needs Business Support or higher in the scanned account).</p>
        <GenAiNotice />
      </>
    ),
  },
};

export const helpFor = (pathname: string) => HELP[pathname];

export const HelpContent = ({ pathname }: { pathname: string }) => {
  const h = helpFor(pathname);
  if (!h) return null;
  return (
    <HelpPanel
      header={<h2>{h.header}</h2>}
      footer={
        <Box>
          <h3>Learn more</h3>
          <Link href={DOCS} external>README and architecture</Link>
        </Box>
      }
    >
      {h.content}
    </HelpPanel>
  );
};
