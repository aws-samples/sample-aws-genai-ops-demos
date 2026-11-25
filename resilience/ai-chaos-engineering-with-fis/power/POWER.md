---
name: aws-chaos-engineering
displayName: AWS Chaos Engineering
description: Natural language generation of AWS FIS experiment templates with validation and current capabilities caching
keywords: ["chaos engineering", "fault injection", "resilience testing", "FIS", "AWS FIS", "experiment", "failure testing", "disaster recovery testing", "chaos", "fault", "injection", "resilience", "reliability", "chaos monkey", "chaos testing", "failure simulation", "system resilience", "infrastructure testing"]
author: AWS GenAI Ops Demo Library
---

# AWS Chaos Engineering Kiro Power

Transform natural language descriptions into validated AWS Fault Injection Service (FIS) experiment templates. This power enables SREs and DevOps engineers to create chaos engineering experiments without learning complex JSON syntax, while ensuring generated templates use current AWS capabilities.

## Overview

The AWS Chaos Engineering Kiro Power consists of a custom MCP server that provides FIS data caching and validation tools. It replicates and improves upon the AWS blog approach by providing dynamic, current FIS capabilities instead of static hardcoded lists.

### Automatic Activation

This power automatically activates when users mention any of these keywords in their messages:

**Core Chaos Engineering Terms:**
- chaos engineering, chaos, fault injection, fault, injection
- resilience testing, resilience, reliability
- failure testing, disaster recovery testing

**AWS FIS Specific Terms:**
- FIS, AWS FIS, experiment
- fault injection simulator

**Related Operational Terms:**
- chaos monkey, chaos testing, failure simulation
- system resilience, infrastructure testing

When any of these keywords are detected, Kiro will automatically load this power and make the chaos engineering tools available for generating FIS experiment templates.

### Key Features

- **Natural Language to FIS Templates**: Describe chaos experiments in plain English and get deployable JSON
- **Current AWS Capabilities**: Always uses up-to-date FIS actions and resource types from AWS APIs
- **Template Validation**: Validates generated templates against current AWS FIS capabilities
- **Intelligent Caching**: 24-hour TTL cache with automatic refresh instructions
- **Proper MCP Architecture**: Agent-orchestrated interactions between MCP servers

### Architecture

```
User Input → Kiro Agent → aws-chaos-engineering MCP Server → Validated FIS Template
                ↓
            AWS MCP Server (for fresh FIS data when needed)
```

## Available MCP Servers

This power configures two MCP servers that work together:

### aws-chaos-engineering Server
Custom MCP server providing FIS-specific tools:

- **get_valid_fis_actions**: Returns cached AWS FIS actions and resource types
- **validate_fis_template**: Validates generated templates against current capabilities  
- **refresh_valid_fis_actions_cache**: Updates cache with fresh data from AWS APIs

### aws-mcp Server
Official AWS MCP server for accessing AWS APIs:

- Provides access to FIS APIs for fetching current capabilities
- Used by agents to refresh cached data when needed
- Handles AWS authentication and API calls

## Tool Usage Examples

### Getting Current FIS Capabilities

```python
# Agent calls this to get cached FIS data for system prompts
result = get_valid_fis_actions(region="us-east-1")

# If cache is stale, result includes refresh instructions:
# "Cache is stale. Please refresh using AWS MCP server: call describe_fis_actions..."
```

### Validating Generated Templates

```python
# Agent validates generated FIS templates
template = {
    "description": "Stop EC2 instances experiment",
    "actions": {
        "StopInstances": {
            "actionId": "aws:ec2:stop-instances",
            "targets": {
                "Instances": "MyEC2Instances"
            }
        }
    }
}

validation = validate_fis_template(template=template)
# Returns: valid=True/False, errors=[], warnings=[], invalid_actions=[], etc.
```

### Refreshing Cache Data

```python
# Agent fetches fresh data from AWS MCP server, then updates cache
fresh_data = {
    "fis_actions": [...],  # From AWS APIs
    "resource_types": [...],  # From AWS APIs
}

result = refresh_valid_fis_actions_cache(
    region="us-east-1", 
    fis_data=fresh_data
)
```

### Generating System Prompts

```python
# Agent generates complete system prompt with current FIS capabilities
user_architecture = "Web application with EC2 instances, ALB, and RDS database"

prompt_result = generate_fis_system_prompt(
    user_architecture=user_architecture,
    region="us-east-1"
)

# Returns complete system prompt ready for LLM with:
# - Current FIS actions and resource types
# - Safety guidelines and constraints  
# - Few-shot examples from AWS documentation
# - User's architecture description
```

## Common Workflows

### Basic Chaos Experiment Generation

1. **User Request**: "Create a chaos experiment to stop random EC2 instances in my production environment"

2. **Agent Activation**: Power activates on chaos engineering keywords

3. **Capability Check**: Agent calls `get_valid_fis_actions()` to get current FIS capabilities

4. **Cache Refresh** (if needed): If cache is stale, agent:
   - Calls AWS MCP server to fetch fresh FIS data
   - Updates cache using `refresh_valid_fis_actions_cache()`
   - Retrieves fresh capabilities

5. **System Prompt Generation**: Agent calls `generate_fis_system_prompt()` with user's architecture description to create complete prompt with current FIS capabilities

6. **Template Generation**: Agent uses generated system prompt with LLM to create valid FIS experiment template

7. **Validation**: Agent calls `validate_fis_template()` to ensure template correctness

8. **Result**: User receives deployable FIS JSON template ready for AWS console

### Advanced Multi-Service Experiment

1. **Complex Request**: "Design a chaos experiment that simulates database failover and network latency for my microservices architecture"

2. **System Prompt Creation**: Agent generates comprehensive system prompt with:
   - Current FIS capabilities for database and network actions
   - User's microservices architecture description
   - Safety guidelines for multi-service experiments

3. **Multi-Step Generation**: Agent creates comprehensive experiment with:
   - RDS failover actions
   - Network latency injection
   - Multiple resource targets
   - Appropriate stop conditions

4. **Validation & Refinement**: Template validated and refined based on current AWS capabilities

5. **Safety Review**: Agent includes safety guidelines and rollback procedures

## Best Practices

### Safety Guidelines

- **Always use stop conditions** in FIS experiments to prevent runaway failures
- **Start with non-production environments** to validate experiment behavior
- **Define clear success criteria** before running experiments
- **Have rollback procedures** documented and tested
- **Monitor experiment impact** using CloudWatch and other observability tools

### Template Design

- **Use specific resource targeting** instead of broad wildcards
- **Include appropriate IAM roles** with minimal required permissions
- **Set reasonable experiment duration** to limit blast radius
- **Add descriptive names and tags** for experiment tracking
- **Test templates in staging** before production deployment

### Operational Excellence

- **Regular capability updates**: Cache refreshes automatically, but manual refresh available
- **Validation before deployment**: Always validate templates before AWS console deployment
- **Experiment documentation**: Include business context and expected outcomes
- **Post-experiment analysis**: Review results and update procedures

## Configuration

### Prerequisites

- **AWS CLI**: Version 2.31.13 or later
- **AWS Credentials**: Configured with appropriate FIS permissions
- **Python**: 3.8 or later for MCP server installation
- **uvx**: For MCP server package management

### Required AWS Permissions

The AWS credentials must have permissions for:

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "fis:ListActions",
                "fis:ListExperimentTemplates",
                "fis:GetAction",
                "fis:DescribeAction"
            ],
            "Resource": "*"
        }
    ]
}
```

### MCP Server Installation

The power automatically configures MCP servers via `mcp.json`. Manual installation:

```bash
# Install the aws-chaos-engineering MCP server
uvx --from "git+https://github.com/aws-samples/sample-aws-genai-ops-demos@08d2abfc5405820aa66c4847bde0617209d8df97#subdirectory=resilience/ai-chaos-engineering-with-fis" aws-chaos-engineering

# Install the AWS MCP server (if not already available)
uvx mcp-proxy-for-aws@latest
```

### AWS CLI Setup

Ensure AWS CLI is configured with appropriate region and credentials:

```bash
# Configure AWS CLI
aws configure

# Verify FIS access
aws fis list-actions --region us-east-1

# Test credentials
aws sts get-caller-identity
```

## Troubleshooting

### Common Issues

#### "Cache is stale" Messages

**Problem**: Agent reports cache needs refresh but refresh fails

**Solutions**:
1. Verify AWS credentials have FIS permissions
2. Check AWS CLI configuration and region settings
3. Ensure network connectivity to AWS APIs
4. Try manual cache refresh with fresh data

#### Template Validation Failures

**Problem**: Generated templates fail validation

**Solutions**:
1. Check if FIS actions are available in your AWS region
2. Verify resource types match your AWS account resources
3. Update cache to get latest FIS capabilities
4. Review AWS FIS documentation for action requirements

#### MCP Server Connection Issues

**Problem**: Power fails to activate or tools are unavailable

**Solutions**:
1. Verify uvx installation and PATH configuration
2. Check MCP server logs for startup errors
3. Restart Kiro to reload MCP server configuration
4. Validate mcp.json configuration syntax

### Debug Commands

```bash
# Test MCP server directly
uvx --from "git+https://github.com/aws-samples/sample-aws-genai-ops-demos@08d2abfc5405820aa66c4847bde0617209d8df97#subdirectory=resilience/ai-chaos-engineering-with-fis" aws-chaos-engineering

# Check AWS MCP server connectivity
uvx mcp-proxy-for-aws@latest --help

# Verify AWS permissions
aws fis list-actions --region us-east-1 --no-paginate

# Check cache directory (varies by OS)
# Windows: %LOCALAPPDATA%\aws-chaos-engineering\cache
# macOS/Linux: ~/.cache/aws-chaos-engineering
```

### Getting Help

1. **Check AWS FIS Documentation**: [AWS Fault Injection Simulator User Guide](https://docs.aws.amazon.com/fis/)
2. **Review MCP Server Logs**: Check Kiro MCP server panel for error messages
3. **Validate AWS Setup**: Ensure proper permissions and region configuration
4. **Test Components Separately**: Verify AWS CLI, MCP servers, and permissions independently

## Advanced Usage

### Custom System Prompts

The power provides structured FIS data for agent system prompts. Advanced users can customize prompt templates to:

- Include specific safety guidelines for their environment
- Add organization-specific experiment patterns
- Integrate with existing incident response procedures
- Customize validation rules for compliance requirements

### Integration with CI/CD

Generated FIS templates can be integrated into automated testing pipelines:

1. Generate templates using natural language descriptions
2. Validate templates in staging environments
3. Store validated templates in version control
4. Deploy experiments as part of resilience testing suites

### Multi-Region Experiments

The power supports multi-region chaos engineering:

- Cache FIS capabilities for multiple AWS regions
- Generate region-specific experiment templates
- Coordinate cross-region failure scenarios
- Validate templates against region-specific capabilities

## Related Resources

- [AWS Fault Injection Simulator](https://aws.amazon.com/fis/)
- [Chaos Engineering Principles](https://principlesofchaos.org/)
- [AWS Well-Architected Reliability Pillar](https://docs.aws.amazon.com/wellarchitected/latest/reliability-pillar/)
- [MCP Protocol Documentation](https://modelcontextprotocol.io/)