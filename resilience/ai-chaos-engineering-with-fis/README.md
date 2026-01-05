# AWS Chaos Engineering MCP Server

An MCP (Model Context Protocol) server for AWS FIS (Fault Injection Simulator) operations, enabling natural language generation of chaos engineering experiment templates. This server is designed to be packaged as a Kiro Power for seamless integration with AI-assisted development workflows.

> **Inspired by**: [Chaos engineering made clear: Generate AWS FIS experiments using natural language through Amazon Bedrock](https://aws.amazon.com/blogs/publicsector/chaos-engineering-made-clear-generate-aws-fis-experiments-using-natural-language-through-amazon-bedrock/)

## Overview

This MCP server transforms natural language descriptions into validated AWS Fault Injection Service (FIS) experiment templates. It builds upon the approach described in the AWS blog post by providing dynamic, current FIS capabilities instead of static hardcoded lists, while maintaining proper MCP architecture where agents orchestrate all server interactions.

### Key Capabilities

- **Natural Language to FIS Templates**: Convert plain English descriptions into deployable JSON CloudFormation templates
- **Current AWS Capabilities**: Always uses up-to-date FIS actions and resource types from AWS APIs
- **Template Validation**: Validates generated templates against current AWS FIS capabilities
- **Intelligent Caching**: 24-hour TTL cache with automatic refresh instructions
- **Agent Orchestration**: Proper MCP architecture with agent-coordinated server interactions

## Features

- **Local Caching**: File-based caching of FIS capabilities with automatic TTL management
- **Template Validation**: Validates action IDs and resource types (not IAM permissions or ARNs)
- **Agent Orchestration**: Follows MCP architecture where agents orchestrate all server interactions
- **Error Handling**: Comprehensive error handling with MCP-compliant responses
- **Cross-Platform**: Works on Windows, macOS, and Linux
- **Kiro Power Integration**: Packaged as a complete Kiro Power with documentation and steering files

## Installation and Setup

### Prerequisites

Before installing the MCP server, ensure you have:

- **Python 3.8+**: Required for MCP server execution
- **uvx**: Recommended package manager for MCP servers
- **AWS CLI 2.31.13+**: For AWS service access
- **AWS Credentials**: Configured with appropriate FIS permissions
- **Kiro**: For MCP client functionality (if using as Kiro Power)

### Quick Installation

```bash
# Install the MCP server
uv tool install .

# Verify installation
uvx aws-chaos-engineering --help
```

### AWS Configuration

#### 1. Configure AWS CLI

```bash
# Configure AWS credentials and region
aws configure

# Verify configuration
aws sts get-caller-identity
```

#### 2. Verify FIS Access

```bash
# Test FIS service access
aws fis list-actions --region us-east-1 --no-paginate

# Check available regions
aws ec2 describe-regions --query 'Regions[].RegionName' --output table
```

#### 3. Required IAM Permissions

Your AWS credentials must have the following permissions:

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

### Kiro Power Setup

#### 1. MCP Configuration

Add to your Kiro `mcp.json` configuration:

```json
{
  "mcpServers": {
    "aws-chaos-engineering": {
      "command": "uvx",
      "args": ["aws-chaos-engineering"],
      "disabled": false,
      "autoApprove": [
        "get_valid_fis_actions", 
        "validate_fis_template", 
        "refresh_valid_fis_actions_cache"
      ]
    },
    "aws-mcp": {
      "command": "uvx",
      "timeout": 100000,
      "transport": "stdio",
      "args": [
        "mcp-proxy-for-aws@latest", 
        "https://aws-mcp.us-east-1.api.aws/mcp", 
        "--metadata", 
        "AWS_REGION=us-west-2"
      ],
      "disabled": false,
      "autoApprove": []
    }
  }
}
```

#### 2. Power Installation

If using as a complete Kiro Power:

1. Copy the `power/` directory to your Kiro powers location
2. Restart Kiro to load the new power
3. Verify power activation with chaos engineering keywords

### Verification

#### Test MCP Server Installation

```bash
# Test server startup (should start and wait for input)
uvx aws-chaos-engineering

# Test with sample MCP message (in another terminal)
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}' | uvx aws-chaos-engineering
```

**Expected Output:**
After running the echo command, you should see a JSON response similar to:
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "protocolVersion": "2024-11-05",
    "capabilities": {
      "tools": {
        "listChanged": true
      }
    },
    "serverInfo": {
      "name": "aws-chaos-engineering",
      "version": "0.1.0"
    }
  }
}
```

This confirms the MCP server is properly installed and responding to protocol messages.

#### Cache Location

The MCP server creates a local cache to store FIS capabilities data:

- **Windows**: `%USERPROFILE%\.aws-chaos-engineering\`
- **Unix/Linux/macOS**: `~/.cache/aws-chaos-engineering/`

Cache files are named by region (e.g., `fis_actions_us-east-1.json`) and automatically refresh every 24 hours.

#### Test Integration

Run the integration test suite:

```bash
cd aws-chaos-engineering
python test_integration.py
```

Expected output:
```
AWS Chaos Engineering Kiro Power - Integration Tests
============================================================
Testing uvx installation...
✓ uvx installation successful

Testing MCP server startup...
✓ MCP server startup successful

Testing cache functionality...
✓ Cache functionality working

Testing validation functionality...
✓ Validation functionality working

Testing end-to-end workflow...
✓ End-to-end workflow successful

============================================================
Tests completed: 5 passed, 0 failed
✓ All integration tests passed!
```

## Usage

### As Kiro Power (Recommended)

The server is designed to work seamlessly as a Kiro Power with automatic activation:

#### Automatic Activation Keywords

The power activates automatically when you mention any of these keywords:

- **Core Terms**: "chaos engineering", "fault injection", "resilience testing"
- **AWS FIS**: "FIS", "AWS FIS", "experiment", "fault injection simulator"  
- **Failure Testing**: "failure testing", "disaster recovery testing", "chaos"
- **Related**: "chaos monkey", "chaos testing", "failure simulation"

#### Example Usage

Let's walk through a complete workflow to understand how each component works together:

**User Request:**
```
"I want to create a chaos engineering experiment to test my RDS failover capabilities"
```

**Complete Workflow:**

```
┌─────────────────┐    ┌─────────────────┐    ┌───────────────────────┐   ┌───────────────────┐
│      USER       │    │   KIRO AGENT    │    │ aws-chaos-engeneering │   │   AWS-MCP Server  │
│                 │    │  (MCP Client)   │    │       MCP Server      │   │    "Companion"    │
└─────────────────┘    └─────────────────┘    └───────────────────────┘   └───────────────────┘
         │                       │                       │                       │
         │ 1. Natural language   │                       │                       │
         │ chaos request         │                       │                       │
         ├──────────────────────►│                       │                       │
         │                       │                       │                       │
         │                       │ 2. Detects keywords   │                       │
         │                       │ & activates power     │                       │
         │                       │                       │                       │
         │                       │ 3. get_valid_fis_     │                       │
         │                       │ actions(region)       │                       │
         │                       ├──────────────────────►│                       │
         │                       │                       │                       │
         │                       │ 4. Cache status       │                       │
         │                       │ (fresh/stale/empty)   │                       │
         │                       │◄──────────────────────┤                       │
         │                       │                       │                       │
         │                       │ 5. IF STALE: Call     │                       │
         │                       │ describe_fis_actions  │                       │
         │                       ├───────────────────────┼──────────────────────►│
         │                       │                       │                       │
         │                       │ 6. IF STALE: Call     │                       │
         │                       │ describe_fis_resource │                       │
         │                       │ _types                │                       │
         │                       ├───────────────────────┼──────────────────────►│
         │                       │                       │                       │
         │                       │ 7. Fresh FIS data     │                       │
         │                       │ (actions + resources) │                       │
         │                       │◄──────────────────────┼───────────────────────┤
         │                       │                       │                       │
         │                       │ 8. refresh_valid_fis_ │                       │
         │                       │ actions_cache(data)   │                       │
         │                       ├──────────────────────►│                       │
         │                       │                       │                       │
         │                       │ 9. Cache updated      │                       │
         │                       │◄──────────────────────┤                       │
         │                       │                       │                       │
         │                       │ 10. Agent builds      │                       │
         │                       │ system prompt with    │                       │
         │                       │ current FIS caps      │                       │
         │                       │                       │                       │
         │                       │ 11. Agent executes    │                       │
         │                       │ LLM call to generate  │                       │
         │                       │ experiment template   │                       │                       │
         │                       │                       │                       │
         │                       │ 12. validate_fis_     │                       │
         │                       │ template(template)    │                       │
         │                       ├──────────────────────►│                       │
         │                       │                       │                       │
         │                       │ 13. Validation result │                       │
         │                       │ (valid/invalid+errors)│                       │
         │                       │◄──────────────────────┤                       │
         │                       │                       │                       │
         │ 14. Deployable        │                       │                       │
         │ CloudFormation JSON   │                       │                       │
         │◄──────────────────────┤                       │                       │
         │                       │                       │                       │
```

**All MCP Tools Used in Workflow:**

**aws-chaos-engineering MCP Server (3 tools):**
- `get_valid_fis_actions` - Step 3: Check cache status and retrieve FIS capabilities
- `refresh_valid_fis_actions_cache` - Step 8: Update cache with fresh AWS data  
- `validate_fis_template` - Step 12: Validate generated templates against FIS capabilities

**AWS MCP Server (2 tools):**
- `describe_fis_actions` - Step 5: Fetch current FIS actions from AWS APIs
- `describe_fis_resource_types` - Step 6: Fetch current FIS resource types from AWS APIs

**Component Responsibilities:**

### 🧑‍💻 User
- **Provides**: Natural language description of desired chaos experiment
- **Receives**: Validated, deployable CloudFormation template
- **Example**: "Test RDS failover", "Stop EC2 instances in production"

### 🤖 Kiro Agent (MCP Client)
- **Keyword Detection**: Automatically activates on chaos engineering terms
- **Orchestration**: Coordinates all MCP server interactions (calls all 5 tools)
- **Cache Management**: Decides when to refresh stale cache data
- **Prompt Building**: Agent builds system prompts with current FIS capabilities from cache
- **LLM Execution**: Agent executes calls to AI models to generate experiment templates
- **Validation**: Ensures templates are technically valid before delivery

### 🔧 aws-chaos-engineering MCP Server
- **Cache Management**: Stores FIS capabilities with 24-hour TTL
- **Template Validation**: Checks action IDs and resource types against AWS capabilities
- **Agent Instructions**: Provides clear guidance when cache refresh is needed
- **No Direct AWS Access**: Relies on agent to fetch fresh data via AWS MCP server

### ☁️ AWS MCP Server
- **AWS API Access**: Direct connection to AWS FIS service
- **Real-time Data**: Fetches current FIS actions and resource types
- **Authentication**: Handles AWS credentials and permissions
- **Regional Support**: Provides region-specific FIS capabilities

**Key Architecture Principles:**
- **Agent Orchestration**: Only the Kiro Agent coordinates between servers
- **No Server-to-Server Communication**: MCP servers never call each other directly
- **Intelligent Caching**: Minimizes AWS API calls while ensuring current data
- **Fail-Fast Validation**: Catches technical errors before deployment

### As Standalone MCP Server

For direct MCP client integration:

```json
{
  "mcpServers": {
    "aws-chaos-engineering": {
      "command": "uvx",
      "args": ["aws-chaos-engineering"],
      "disabled": false,
      "autoApprove": ["get_valid_fis_actions", "validate_fis_template", "refresh_valid_fis_actions_cache"]
    }
  }
}
```

### Available Tools

The MCP server provides three main tools for chaos engineering workflows:

#### `get_valid_fis_actions`

Returns cached AWS FIS actions and resource types for agent system prompts.

**Parameters:**
- `region` (optional): AWS region (default: "us-east-1")

**Returns:**
- **Fresh Cache**: FIS actions and resource types data
- **Stale Cache**: Instructions for agent to refresh via AWS MCP server
- **Empty Cache**: Instructions to fetch initial data

**Example Response (Fresh Cache):**
```json
{
  "status": "fresh",
  "fis_actions": [
    {
      "id": "aws:ec2:stop-instances",
      "description": "Stops EC2 instances with optional restart"
    },
    {
      "id": "aws:rds:failover-db-cluster", 
      "description": "Forces failover of RDS Multi-AZ deployments"
    }
  ],
  "resource_types": [
    {
      "type": "aws:ec2:instance",
      "description": "EC2 virtual machine instances"
    },
    {
      "type": "aws:rds:cluster",
      "description": "RDS database clusters"
    }
  ],
  "last_updated": "2024-12-23T18:00:00Z",
  "region": "us-east-1"
}
```

**Example Response (Stale Cache):**
```json
{
  "status": "stale",
  "message": "Cache is older than 24 hours. Please refresh using AWS MCP server.",
  "instructions": "Call aws-mcp server: describe_fis_actions and describe_fis_resource_types, then use refresh_valid_fis_actions_cache",
  "last_updated": "2024-12-22T10:00:00Z"
}
```

#### `validate_fis_template`

Validates a generated FIS experiment template against cached capabilities.

**Parameters:**
- `template` (required): FIS experiment template as a dictionary

**Returns:**
- Validation results with errors, warnings, and invalid items

**Example Usage:**
```python
template = {
    "description": "Stop EC2 instances experiment",
    "actions": {
        "StopInstances": {
            "actionId": "aws:ec2:stop-instances",
            "targets": {"Instances": "MyEC2Instances"}
        }
    },
    "targets": {
        "MyEC2Instances": {
            "resourceType": "aws:ec2:instance",
            "resourceTags": {"Environment": "test"}
        }
    }
}

result = validate_fis_template(template=template)
```

**Example Response (Valid):**
```json
{
  "valid": true,
  "errors": [],
  "warnings": [
    "Ensure stop conditions are configured for safety"
  ],
  "invalid_actions": [],
  "invalid_resource_types": [],
  "validation_timestamp": "2024-12-23T18:30:00Z"
}
```

**Example Response (Invalid):**
```json
{
  "valid": false,
  "errors": [
    "Action 'aws:invalid:action' is not available in current FIS capabilities"
  ],
  "warnings": [],
  "invalid_actions": ["aws:invalid:action"],
  "invalid_resource_types": [],
  "validation_timestamp": "2024-12-23T18:30:00Z"
}
```

#### `refresh_valid_fis_actions_cache`

Updates the cache with fresh data provided by agents from AWS MCP server.

**Parameters:**
- `region` (optional): AWS region (default: "us-east-1")
- `fis_data` (required): Fresh FIS data from AWS MCP server

**Returns:**
- Cache update status and timestamp

**Example Usage:**
```python
# Agent fetches fresh data from AWS MCP server first
fresh_data = {
    "fis_actions": [...],  # From AWS APIs
    "resource_types": [...],  # From AWS APIs
}

result = refresh_valid_fis_actions_cache(
    region="us-east-1", 
    fis_data=fresh_data
)
```

**Example Response:**
```json
{
  "success": true,
  "message": "Cache updated successfully",
  "timestamp": "2024-12-23T18:45:00Z",
  "region": "us-east-1",
  "actions_count": 15,
  "resource_types_count": 8
}
```

## Architecture Overview

The AWS Chaos Engineering MCP Server follows a distributed architecture where agents orchestrate interactions between multiple MCP servers:

```
User Input → Kiro Agent → aws-chaos-engineering MCP Server → Validated FIS Template
                ↓
            AWS MCP Server (for fresh FIS data when needed)
```

### Data Flow

1. **User Request**: Natural language chaos engineering description
2. **Agent Activation**: Kiro agent detects chaos engineering keywords
3. **Capability Check**: Agent calls `get_valid_fis_actions()` to check cache
4. **Cache Refresh** (if needed): Agent fetches fresh data from AWS MCP server
5. **System Prompt**: Agent generates complete prompt with current FIS capabilities
6. **Template Generation**: LLM creates FIS experiment template
7. **Validation**: Agent validates template using `validate_fis_template()`
8. **Result**: User receives deployable CloudFormation template



## Validation Scope and Limitations

The FIS template validator is designed to check technical compatibility with AWS FIS service capabilities, not business logic or security policies.

### What the Validator Checks ✅

- **Action IDs**: Verifies action IDs exist in current FIS capabilities
- **Resource Types**: Validates resource types are supported by FIS
- **Template Structure**: Basic JSON structure and required fields
- **Action-Resource Compatibility**: Ensures actions can target specified resource types

### What the Validator Does NOT Check ❌

- **IAM Permissions**: Does not validate service roles or user permissions
- **ARNs and Resource Identifiers**: Does not check if specific resources exist
- **Business Logic**: Does not validate experiment design or safety
- **Stop Conditions**: Does not verify CloudWatch alarms or stop condition logic
- **Resource Tags**: Does not validate tag existence or values
- **Network Configuration**: Does not check VPC, subnet, or security group settings
- **Compliance**: Does not enforce organizational policies or compliance rules

### Validation Examples

#### Valid Template (Passes Validation)
```json
{
  "actions": {
    "StopInstances": {
      "actionId": "aws:ec2:stop-instances",  // ✅ Valid action ID
      "targets": {"Instances": "MyInstances"}
    }
  },
  "targets": {
    "MyInstances": {
      "resourceType": "aws:ec2:instance",    // ✅ Valid resource type
      "resourceTags": {"Environment": "test"}
    }
  }
}
```

#### Invalid Template (Fails Validation)
```json
{
  "actions": {
    "InvalidAction": {
      "actionId": "aws:invalid:action",      // ❌ Invalid action ID
      "targets": {"Resources": "MyResources"}
    }
  },
  "targets": {
    "MyResources": {
      "resourceType": "aws:invalid:type",    // ❌ Invalid resource type
      "resourceArns": ["arn:aws:ec2:us-east-1:123456789012:instance/i-1234567890abcdef0"]
    }
  }
}
```

### Validation Philosophy

The validator follows the principle of **"fail fast on technical issues, defer to deployment for business logic"**. This means:

1. **Technical Validation**: Catch obvious technical errors early (invalid action IDs, unsupported resource types)
2. **Business Logic Deferral**: Let AWS FIS service handle business logic validation during deployment
3. **Security Deferral**: Let IAM and AWS security services handle permission validation
4. **Resource Existence**: Let AWS APIs validate actual resource existence at runtime

This approach ensures the validator remains focused, fast, and doesn't duplicate AWS service validation logic.

## Troubleshooting Guide

### Common Installation Issues

#### uvx Command Not Found

**Problem**: `uvx: command not found` when trying to install

**Solutions**:
1. **Install uvx**: 
   ```bash
   # Using pip
   pip install uv
   
   # Using pipx (if available)
   pipx install uv
   
   # Using homebrew (macOS)
   brew install uv
   ```

2. **Verify PATH**: Ensure uvx is in your PATH
   ```bash
   which uvx  # Unix/Linux/macOS
   where uvx  # Windows
   ```

3. **Alternative Installation**:
   ```bash
   pip install aws-chaos-engineering
   ```

#### Python Version Compatibility

**Problem**: Installation fails with Python version errors

**Solutions**:
1. **Check Python Version**:
   ```bash
   python --version  # Should be 3.8+
   ```

2. **Use Specific Python Version**:
   ```bash
   python3.8 -m pip install aws-chaos-engineering
   ```

3. **Virtual Environment**:
   ```bash
   python -m venv chaos-env
   source chaos-env/bin/activate  # Unix/Linux/macOS
   chaos-env\Scripts\activate     # Windows
   pip install aws-chaos-engineering
   ```

### AWS Configuration Issues

#### AWS Credentials Not Found

**Problem**: `NoCredentialsError` or `Unable to locate credentials`

**Solutions**:
1. **Configure AWS CLI**:
   ```bash
   aws configure
   # Enter: Access Key ID, Secret Access Key, Region, Output format
   ```

2. **Verify Credentials**:
   ```bash
   aws sts get-caller-identity
   ```

3. **Alternative Credential Methods**:
   ```bash
   # Environment variables
   export AWS_ACCESS_KEY_ID=your-key
   export AWS_SECRET_ACCESS_KEY=your-secret
   export AWS_DEFAULT_REGION=us-east-1
   
   # IAM roles (for EC2 instances)
   # Credentials automatically available via instance metadata
   ```

#### FIS Service Access Denied

**Problem**: `AccessDenied` errors when accessing FIS service

**Solutions**:
1. **Check IAM Permissions**:
   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       {
         "Effect": "Allow",
         "Action": [
           "fis:ListActions",
           "fis:GetAction",
           "fis:DescribeAction"
         ],
         "Resource": "*"
       }
     ]
   }
   ```

2. **Verify Region Support**:
   ```bash
   # Check if FIS is available in your region
   aws fis list-actions --region us-east-1
   ```

3. **Test with Different Region**:
   ```bash
   aws fis list-actions --region us-west-2
   ```

### MCP Server Issues

#### Server Fails to Start

**Problem**: MCP server exits immediately or fails to start

**Solutions**:
1. **Check Installation**:
   ```bash
   uvx aws-chaos-engineering --help
   ```

2. **Test Direct Execution**:
   ```bash
   python -m aws_chaos_engineering
   ```

3. **Check Dependencies**:
   ```bash
   pip list | grep fastmcp
   pip list | grep pydantic
   ```

4. **View Error Logs**:
   ```bash
   uvx aws-chaos-engineering 2>&1 | tee server.log
   ```

#### MCP Communication Errors

**Problem**: Kiro cannot communicate with MCP server

**Solutions**:
1. **Verify MCP Configuration**:
   ```json
   {
     "mcpServers": {
       "aws-chaos-engineering": {
         "command": "uvx",
         "args": ["aws-chaos-engineering"],
         "disabled": false
       }
     }
   }
   ```

2. **Check Server Status in Kiro**:
   - Open Kiro MCP server panel
   - Look for aws-chaos-engineering server status
   - Check for error messages

3. **Restart MCP Server**:
   - Restart Kiro to reload MCP configuration
   - Or manually restart server from Kiro panel

### Cache Issues

If you encounter cache-related errors, check the MCP server logs for specific error messages. The cache automatically refreshes every 24 hours.

### Validation Issues

#### All Templates Fail Validation

**Problem**: Even valid templates are rejected

**Solutions**:
1. **Check Cache Status**:
   - Ensure cache has been populated
   - Verify cache contains FIS actions and resource types

2. **Update Cache**:
   - Clear cache and trigger refresh
   - Verify AWS credentials and permissions

3. **Test with Simple Template**:
   ```json
   {
     "actions": {
       "StopInstances": {
         "actionId": "aws:ec2:stop-instances",
         "targets": {"Instances": "MyInstances"}
       }
     }
   }
   ```

#### Validation Too Strict

**Problem**: Valid AWS templates are rejected

**Solutions**:
1. **Check FIS Capabilities**:
   ```bash
   # Verify action exists in AWS
   aws fis list-actions --region us-east-1 | grep stop-instances
   ```

2. **Update to Latest Version**:
   ```bash
   uvx upgrade aws-chaos-engineering
   ```

3. **Report Issue**: If action should be valid, report as bug

### Kiro Power Issues

#### Power Doesn't Activate

**Problem**: Chaos engineering keywords don't trigger power activation

**Solutions**:
1. **Check Keywords**: Use specific activation keywords:
   - "chaos engineering"
   - "fault injection"
   - "AWS FIS"
   - "resilience testing"

2. **Verify Power Installation**:
   - Check power is loaded in Kiro
   - Verify mcp.json configuration

3. **Manual Activation**:
   - Explicitly mention the power name
   - Use Kiro power selection interface

#### Generated Templates Don't Work

**Problem**: Templates pass validation but fail in AWS console

**Solutions**:
1. **Check IAM Permissions**: Ensure FIS service role has required permissions
2. **Verify Resource Existence**: Ensure target resources exist and are properly tagged
3. **Review Stop Conditions**: Configure appropriate CloudWatch alarms
4. **Test in Staging**: Always test templates in non-production first

### Performance Issues

#### Slow Response Times

**Problem**: MCP server responds slowly

**Solutions**:
1. **Check Cache Status**: Ensure cache is fresh and not being rebuilt frequently
2. **Verify Network**: Test AWS API response times
3. **Reduce Cache TTL**: Modify cache settings if needed
4. **Monitor Resources**: Check system CPU and memory usage

#### High Memory Usage

**Problem**: Server consumes excessive memory

**Solutions**:
1. **Restart Server**: Restart MCP server to clear memory
2. **Check Cache Size**: Large cache files may consume memory
3. **Update Version**: Newer versions may have memory optimizations

### Debug Commands

#### System Information
```bash
# Check Python version
python --version

# Check installed packages
pip list | grep -E "(aws-chaos|fastmcp|pydantic)"

# Check AWS CLI version
aws --version

# Check uvx version
uvx --version
```

#### AWS Connectivity
```bash
# Test AWS credentials
aws sts get-caller-identity

# Test FIS service access
aws fis list-actions --region us-east-1 --no-paginate

# Test different regions
aws ec2 describe-regions --query 'Regions[].RegionName' --output table
```

#### Cache Inspection
Cache files are stored in the locations mentioned above and automatically managed by the server.

#### MCP Server Testing
```bash
# Test server startup
uvx aws-chaos-engineering &
SERVER_PID=$!

# Send test message
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}' | nc localhost 3000

# Clean up
kill $SERVER_PID
```

### Getting Additional Help

#### Log Collection

When reporting issues, collect these logs:

1. **MCP Server Logs**:
   ```bash
   uvx aws-chaos-engineering 2>&1 | tee mcp-server.log
   ```

2. **Kiro Logs**: Check Kiro application logs for MCP communication errors

3. **AWS CLI Logs**:
   ```bash
   aws fis list-actions --region us-east-1 --debug 2>&1 | tee aws-debug.log
   ```

4. **System Information**:
   ```bash
   # Create system info report
   echo "Python: $(python --version)" > system-info.txt
   echo "OS: $(uname -a)" >> system-info.txt
   echo "AWS CLI: $(aws --version)" >> system-info.txt
   pip list >> system-info.txt
   ```

#### Support Resources

1. **AWS FIS Documentation**: [AWS Fault Injection Simulator User Guide](https://docs.aws.amazon.com/fis/)
2. **MCP Protocol**: [Model Context Protocol Documentation](https://modelcontextprotocol.io/)
3. **Kiro Documentation**: Check Kiro help and documentation
4. **GitHub Issues**: Report bugs and feature requests on the project repository

#### Common Error Patterns

| Error Message | Likely Cause | Solution |
|---------------|--------------|----------|
| `NoCredentialsError` | AWS credentials not configured | Run `aws configure` |
| `AccessDenied` | Insufficient IAM permissions | Add FIS permissions to IAM user/role |
| `Cache file corrupted` | Corrupted cache file | Clear cache directory |
| `Connection timeout` | Network connectivity issues | Check internet connection and AWS endpoints |
| `Invalid action ID` | Action not available in region | Check FIS service availability in region |
| `MCP server not responding` | Server startup failure | Check server logs and dependencies |

## Development

### Development Setup

```bash
# Clone repository
git clone <repository-url>
cd aws-chaos-engineering

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Unix/Linux/macOS
venv\Scripts\activate     # Windows

# Install in development mode
pip install -e ".[dev]"
```

### Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=aws_chaos_engineering

# Run specific test categories
pytest tests/test_cache_management.py
pytest tests/test_validation_correctness.py

# Run property-based tests
pytest tests/test_complete_agent_workflow.py -v

# Run integration tests
python test_integration.py
```

### Code Quality

```bash
# Format code
black src/
isort src/

# Type checking
mypy src/

# Lint code
flake8 src/

# Run all quality checks
black src/ && isort src/ && mypy src/ && flake8 src/
```

### Building and Publishing

```bash
# Build package
python -m build

# Check package
twine check dist/*

# Publish to PyPI (maintainers only)
twine upload dist/*
```

### Project Structure

```
aws-chaos-engineering/
├── src/
│   └── aws_chaos_engineering/
│       ├── __init__.py
│       ├── __main__.py          # MCP server entry point
│       ├── server.py            # MCP tool implementations
│       ├── fis_cache.py         # Cache management
│       └── validators.py        # Template validation
├── power/                       # Kiro Power package
│   ├── POWER.md                 # Kiro Power documentation
│   ├── mcp.json                 # MCP server configuration
│   └── steering/                # Kiro Power steering files
│       ├── getting-started.md
│       └── advanced-patterns.md
├── tests/                       # Test suite
├── pyproject.toml              # Package configuration
├── README.md                   # This file
└── test_integration.py         # Integration tests
```

## Security Considerations

### Credential Management

- **Never hardcode credentials** in source code
- **Use IAM roles** when possible instead of access keys
- **Rotate credentials** regularly
- **Use least privilege** principle for IAM permissions

### Cache Security

- **Cache location**: Uses user-specific cache directories
- **File permissions**: Cache files are user-readable only
- **No sensitive data**: Cache contains only public FIS capability data
- **Automatic cleanup**: Corrupted cache files are automatically removed

### Network Security

- **HTTPS only**: All AWS API calls use HTTPS
- **No external dependencies**: Only communicates with AWS APIs
- **Local operation**: MCP server runs locally, no external servers

## Performance Optimization

- **Intelligent Caching**: 24-hour TTL with per-region cache files
- **Memory Management**: Streaming JSON processing and automatic cleanup
- **Network Optimization**: Connection reuse and compression support

## Changelog

### Version 0.1.0 (Current)

**Features:**
- Initial MCP server implementation
- FIS actions and resource types caching
- Template validation against current AWS capabilities
- Agent-orchestrated cache refresh workflow
- Cross-platform support (Windows, macOS, Linux)
- Kiro Power packaging with documentation and steering files

**Tools:**
- `get_valid_fis_actions`: Retrieve cached FIS capabilities
- `validate_fis_template`: Validate experiment templates
- `refresh_valid_fis_actions_cache`: Update cache with fresh data

**Architecture:**
- FastMCP v2 framework with @mcp.tool decorators
- File-based caching with 24-hour TTL
- Proper MCP architecture with agent orchestration
- Comprehensive error handling and logging

## Contributing

We welcome community contributions! Please see [CONTRIBUTING.md](../../CONTRIBUTING.md) for guidelines.

## Security

See [CONTRIBUTING](../../CONTRIBUTING.md#security-issue-notifications) for more information.

## License

This library is licensed under the MIT-0 License. See the [LICENSE](../../LICENSE) file.