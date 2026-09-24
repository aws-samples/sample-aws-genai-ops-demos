---
inclusion: fileMatch
fileMatchPattern: '**/{cdk/**/app.py,cdk/**/app.ts,terraform/*.tf}'
---

# Solution Adoption Tracking Requirements

## Overview

All demos in the GenAI Ops Demo Library must implement solution adoption tracking to measure usage and adoption patterns. This enables data-driven decisions for roadmap planning and demonstrates the value of our operational GenAI solutions.

## Tracking Implementation Requirements

### Assigned Tracking ID
**Repository Tracking ID**: `uksb-do9bhieqqh`

This ID must be included in **every new demo** to enable adoption tracking through the AWS Solution Adoption Dashboard.

### Dual-Tag Strategy
Each demo must use exactly two tags in the following format:
```
(tag:demo-name,pillar)
```

**Operational Pillars:**
- `operations-automation` - Lifecycle tracking, model migrations, legacy system automation
- `security` - Shift-left security, compliance automation, vulnerability detection  
- `cost-optimization` - GenAI spend control, cost visibility, budget management
- `observability` - Incident analysis, anomaly detection, postmortems
- `resilience` - Failure detection, recovery automation, capacity management

### Implementation Rules

#### Single Stack Tracking Per Demo
- **CRITICAL**: Only the main/primary stack should include tracking code
- Additional stacks within the same demo must NOT include tracking codes
- This prevents duplicate usage metrics that would skew adoption data

#### Best Practices (Lessons Learned)
- **Use CDK App Files**: Add tracking in `app.py` or `app.ts` files, not in stack classes
- **Short Descriptions**: Keep descriptions concise and meaningful
- **Tags at End**: Place tracking ID and tags at the end of the description
- **Human-Readable First**: Start with descriptive text, append tracking information

#### CDK Implementation (Preferred - App File)
```python
# In app.py
YourDemoStack(
    app,
    "YourDemoStack",
    description="Brief demo description (uksb-do9bhieqqh)(tag:your-demo-name,pillar-name)",
)
```

```typescript
// In app.ts
new YourDemoStack(app, 'YourDemoStack', {
  description: 'Brief demo description (uksb-do9bhieqqh)(tag:your-demo-name,pillar-name)',
});
```

#### CDK Implementation (Alternative - Stack Class)
```python
class YourDemoStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, 
                        description="Brief demo description (uksb-do9bhieqqh)(tag:your-demo-name,pillar-name)",
                        **kwargs)
```

#### Terraform path (when a demo also ships Terraform on top of CDK)

The Solution Adoption Dashboard counts deployments by reading the `Description` of **CloudFormation stacks**; Terraform resources are invisible to it and Terraform support is not on its roadmap (confirmed by the dashboard owners in `#saes-metrics-interest`). A Terraform deployment must therefore create one **zero-cost CloudFormation marker stack** whose only job is to carry the tracking code. Rules:

- Same tracking ID and **exactly the same tags** as the CDK main stack of that demo (one demo, one tag pair, whichever path deployed it)
- Marker resource: `AWS::CloudFormation::WaitConditionHandle` (no cost, no side effect, no permissions)
- Stack name with region suffix, like every other stack: `<Demo>Tracking-${var.region}`
- Opt-out variable `enable_deployment_metrics`, `bool`, default `true`, documented in the README ("set to false to opt out of adoption metrics")
- Only this marker carries tracking on the Terraform path; the two IaC paths never coexist in one account and region, so exactly one tracked stack exists per deployment either way

```hcl
# terraform/tracking.tf
variable "enable_deployment_metrics" {
  description = "Create a zero-cost CloudFormation marker stack so this deployment is counted in AWS solution adoption metrics. Set to false to opt out."
  type        = bool
  default     = true
}

resource "aws_cloudformation_stack" "tracking" {
  count = var.enable_deployment_metrics ? 1 : 0
  name  = "YourDemoTracking-${var.region}"

  template_body = jsonencode({
    AWSTemplateFormatVersion = "2010-09-09"
    Description              = "Your Demo, Terraform deployment marker (uksb-do9bhieqqh)(tag:your-demo-name,pillar-name)"
    Resources = {
      Marker = { Type = "AWS::CloudFormation::WaitConditionHandle" }
    }
  })
}
```

Reference implementation: `operations-automation/aws-services-lifecycle-tracker/terraform/tracking.tf`.

#### CloudFormation Implementation (Not Accepted for New Demos)
All new demos must use AWS CDK. The example below is for reference only if maintaining legacy demos:
```yaml
AWSTemplateFormatVersion: '2010-09-09'
Description: 'Brief demo description (uksb-do9bhieqqh)(tag:your-demo-name,pillar-name)'
```

### Demo Naming Convention
- Use lowercase with hyphens: `ai-password-reset`, `lifecycle-tracker`
- Keep names descriptive but concise
- Avoid generic terms like `demo` or `sample` in the tag name

## Common Mistakes and Solutions

### ❌ Wrong: Tracking in Stack Class Constructor

**Don't do this:**
```python
# infrastructure/cdk/lib/my_stack.py
class MyDemoStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(
            scope, 
            construct_id,
            description="Demo description (uksb-do9bhieqqh)(tag:demo,pillar)",  # ❌ Wrong location
            **kwargs
        )
```

**Why it's wrong:**
- Mixes tracking with stack logic
- Harder to find and update across demos
- Inconsistent with repository standards

---

### ✅ Correct: Tracking in App File

**Do this instead:**
```python
# infrastructure/cdk/app.py
from aws_cdk import App
from lib.my_stack import MyDemoStack

app = App()

MyDemoStack(
    app,
    "MyDemoStack",
    description="AI-powered demo for operational automation (uksb-do9bhieqqh)(tag:demo-name,operations-automation)",  # ✅ Correct location
)

app.synth()
```

**Why it's correct:**
- Keeps tracking separate from stack implementation
- Easy to find in app files across all demos
- Consistent pattern for maintenance

---

### ❌ Wrong: Multiple Stacks with Tracking

**Don't do this:**
```python
# app.py
DataStack(
    app,
    "DataStack",
    description="Data layer (uksb-do9bhieqqh)(tag:demo,pillar)",  # ❌ Duplicate tracking
)

ApiStack(
    app,
    "ApiStack",
    description="API layer (uksb-do9bhieqqh)(tag:demo,pillar)",  # ❌ Duplicate tracking
)
```

**Why it's wrong:**
- Creates duplicate metrics
- Skews adoption data
- Makes analytics unreliable

---

### ✅ Correct: Single Stack with Tracking

**Do this instead:**
```python
# app.py
data_stack = DataStack(
    app,
    "DataStack",
    description="Data layer for demo",  # ✅ No tracking
)

ApiStack(
    app,
    "ApiStack",
    description="AI-powered demo API (uksb-do9bhieqqh)(tag:demo-name,pillar)",  # ✅ Only main stack
    data_table_name=data_stack.table.table_name,
)
```

**Why it's correct:**
- Single tracking point per demo
- Accurate adoption metrics
- Clear which stack is the "main" stack

---

### ❌ Wrong: Generic or Missing Tags

**Don't do this:**
```python
description="Demo (uksb-do9bhieqqh)(tag:demo,pillar)"  # ❌ Too generic
description="Password reset demo (uksb-do9bhieqqh)"    # ❌ Missing pillar tag
```

**Why it's wrong:**
- Generic names don't identify the demo
- Missing pillar prevents categorization
- Breaks analytics and reporting

---

### ✅ Correct: Descriptive Tags

**Do this instead:**
```python
description="AI Password Reset Chatbot (uksb-do9bhieqqh)(tag:password-reset,operations-automation)"  # ✅ Clear and complete
```

**Why it's correct:**
- Descriptive demo name
- Clear pillar categorization
- Enables proper analytics

---

## Real-World Examples

### Example 1: Password Reset Chatbot (TypeScript)
```typescript
// operations-automation/ai-password-reset-chatbot/cdk/bin/app.ts
import * as cdk from 'aws-cdk-lib';
import { PasswordResetInfraStack } from '../lib/password-reset-infra-stack';

const app = new cdk.App();

const infraStack = new PasswordResetInfraStack(app, 'PasswordResetInfra', {
  env,
  description: 'Password Reset Chatbot: Container registry, build pipeline, and IAM roles (uksb-do9bhieqqh)(tag:password-reset,operations-automation)',
});
```

### Example 2: Lifecycle Tracker (TypeScript)
```typescript
// operations-automation/aws-services-lifecycle-tracker/cdk/bin/app.ts
new PipelineStack(app, `AWSServicesLifecycleTrackerPipeline-${region}`, {
  env: { region },
  lifecycleTable: dataStack.lifecycleTable,
  configTable: dataStack.configTable,
  description: 'AWS Services Lifecycle Tracker Pipeline: Lambda durable function refreshing deprecation data and account inventory (uksb-do9bhieqqh)(tag:lifecycle-tracker,operations-automation)',
});
```

The same demo's Terraform path carries the identical tag pair on its marker stack (`terraform/tracking.tf`), see "Terraform path" above.

### Example 3: IT Portal Demo (Python)
```python
# operations-automation/anycompany-it-demo-portal/infrastructure/cdk/app.py
from aws_cdk import App
from lib.portal_stack import AnyCompanyITPortalStack

app = App()

AnyCompanyITPortalStack(
    app, 
    "AnyCompanyITPortalStack",
    description="Multi-portal IT demo environment for AI automation workflows (uksb-do9bhieqqh)(tag:it-portal-demo,operations-automation)",
)

app.synth()
```

### Example 4: Graviton Migration (Python)
```python
# cost-optimization/ai-graviton-migration-assessment/infrastructure/cdk/app.py
GravitonAssessmentStack(
    app,
    "GravitonAssessmentStack",
    description="Graviton migration assessment and cost optimization analysis (uksb-do9bhieqqh)(tag:graviton-migration,cost-optimization)",
)
```

---

## Current Demo Tags

### Operations Automation Pillar
- `ai-documentation-generation`: `(tag:doc-generation,operations-automation)`
- `ai-legacy-system-browser-automation`: `(tag:legacy-automation,operations-automation)`
- `ai-password-reset-chatbot`: `(tag:password-reset,operations-automation)`
- `aws-services-lifecycle-tracker`: `(tag:lifecycle-tracker,operations-automation)`
- `anycompany-it-demo-portal`: `(tag:it-portal-demo,operations-automation)`

### Cost Optimization Pillar
- `ai-graviton-migration-assessment`: `(tag:graviton-migration,cost-optimization)`

## Implementation Checklist

For every new demo, ensure:

- [ ] **Tracking in app file**: Add tracking to CDK app file (`app.py` or `app.ts`) for consistency
- [ ] **Short description**: Use concise, meaningful description before tracking
- [ ] **Tags at end**: Place `(uksb-do9bhieqqh) (tag: demo-name, pillar)` at end of description
- [ ] **Two tags assigned** following naming convention (demo-name, pillar)
- [ ] **Correct pillar selected** based on operational use case
- [ ] **Only main stack tagged** to avoid duplicate metrics
- [ ] **Tag documented** in demo README.md for reference
- [ ] **Terraform path (if any)**: CloudFormation marker stack with the same tag pair and an `enable_deployment_metrics` opt-out

## Benefits

This tracking enables:
- **Demo-level analytics**: Individual demo adoption rates
- **Pillar-level insights**: Which operational areas are most popular
- **Geographic distribution**: Where demos are being deployed
- **Success metrics**: Deployment success rates and patterns
- **Roadmap planning**: Data-driven decisions on future demo development

## Compliance

All demos must implement this tracking before being considered complete. This is a mandatory requirement for inclusion in the GenAI Ops Demo Library.