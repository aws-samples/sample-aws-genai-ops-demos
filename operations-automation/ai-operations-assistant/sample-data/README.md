# Sample Data

Example API responses for testing and development of the G.O.A.T. multi-agent system. Each file contains realistic but synthetic data matching the structure returned by the corresponding AWS APIs.

## Files

| File | Description | AWS API Source |
|------|-------------|----------------|
| `cost-data.json` | Cost Explorer monthly breakdown by service, forecast, and Cost Optimization Hub recommendations | `ce:GetCostAndUsage`, `ce:GetCostForecast`, Cost Optimization Hub |
| `health-events.json` | Health Dashboard events (issue, scheduled change, notification) with affected entities | `health:DescribeEvents`, `health:DescribeAffectedEntities` |
| `support-cases.json` | Support cases with severity levels, statuses, and communication threads | `support:DescribeCases`, `support:DescribeCommunications` |
| `ta-recommendations.json` | Trusted Advisor checks and recommendations categorized by pillar (cost, security) | `trustedadvisor:DescribeTrustedAdvisorChecks`, `trustedadvisor:DescribeTrustedAdvisorCheckResult` |
| `cur-results.json` | Athena query results from CUR data with resource-level costs and usage patterns | `athena:GetQueryResults` |

## Usage

These files are used for:

- **Local development** — test frontend rendering without live AWS API calls
- **Unit/property tests** — validate data formatting and visualization logic
- **Demo walkthroughs** — show realistic output without requiring AWS service access

All account IDs, resource IDs, and ARNs are synthetic placeholders.
