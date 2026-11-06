# AWS Services Lifecycle Tracker

> **This solution has moved.** It is no longer maintained or deployable from this repository.
>
> **New location:** [awslabs/support-solutions-aws, Operations/service-lifecycle-tracker](https://github.com/awslabs/support-solutions-aws/tree/main/Operations/service-lifecycle-tracker)

Use the new location for all new deployments, issues and contributions to this version.

A second version is in development in this repository: [AWS Services Lifecycle Tracker V2 (work in progress)](../aws-services-lifecycle-tracker-v2/README.md). It starts from the same code base and may change without notice.

## What it does

Tracks AWS service deprecations, finds the resources in your account that are affected, and manages remediation. The whole refresh (web extraction, account scan, reconcile, notify) runs as one AWS Lambda durable function execution, with hybrid HTML parsing and Amazon Nova normalization for the extraction step. An admin dashboard (React and Cloudscape behind Amazon Cognito) shows the inventory, the Extended Support cost exposure and a plan of action.

## Last version in this repository

The final version at this path is commit `bf314a2` (2026-11-03), full hash `bf314a2419b81436807e1894f6f3b20f1762246b`. To read the code as it was before the move:

```bash
git show bf314a2:operations-automation/aws-services-lifecycle-tracker/README.md
git ls-tree -r --name-only bf314a2 operations-automation/aws-services-lifecycle-tracker
```

## Contributing

We welcome community contributions! Please see [CONTRIBUTING.md](../../CONTRIBUTING.md) for guidelines.

## Security

See [CONTRIBUTING](../../CONTRIBUTING.md#security-issue-notifications) for more information.

## License

This library is licensed under the MIT-0 License. See the [LICENSE](../../LICENSE) file.
