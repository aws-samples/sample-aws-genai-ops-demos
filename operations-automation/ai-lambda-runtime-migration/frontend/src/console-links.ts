// Deep links into the AWS console. The console host is derived from the
// region's partition (never a literal console host): the commercial partition,
// China and GovCloud each have their own DNS suffix.
import type { MigrationFunction } from './api';

const consoleDnsSuffix = (region: string): string => {
  if (region.startsWith('cn-')) return 'amazonaws.cn';
  if (region.startsWith('us-gov-')) return 'amazonaws-us-gov.com';
  return 'aws.amazon.com';
};

const consoleHost = (region: string): string => `https://${region}.console.${consoleDnsSuffix(region)}`;

// Function name (with any qualifier) from a Lambda ARN; the ARN itself when it
// has no function segment.
export const funcName = (arn: string): string => arn.match(/function:(.+)/)?.[1] || arn;

// Deep link to the function in the Lambda console, for the region it lives in.
// The console opens it when the user is signed into that function's account
// (functions may live in spoke accounts); otherwise the console prompts to
// switch account. Strips any version/alias qualifier from the name.
export const lambdaConsoleUrl = (f: MigrationFunction): string | null => {
  const name = funcName(f.function_arn).split(':')[0];
  if (!name || !f.region) return null;
  return `${consoleHost(f.region)}/lambda/home?region=${f.region}#/functions/${encodeURIComponent(name)}?tab=code`;
};

// The repository name is the last path segment of the CodeCommit clone URL
// (https://git-codecommit.<region>.amazonaws.com/v1/repos/<name>).
export const repoNameFromCloneUrl = (cloneUrl?: string): string =>
  (cloneUrl || '').replace(/\/$/, '').split('/').pop() || '';

// The pushed upgrade branch in the CodeCommit console. The branch is not
// URL-encoded as a whole: it contains '/' and the console expects the raw ref
// path; its characters are [A-Za-z0-9.-/] by construction (derive_job_id +
// target runtime).
export const codecommitBranchUrl = (region: string, repoName: string, branch: string): string | null =>
  region && repoName && branch
    ? `${consoleHost(region)}/codesuite/codecommit/repositories/${encodeURIComponent(repoName)}/browse/refs/heads/${branch}?region=${region}`
    : null;
