# Architecture - Legacy System Automation with AgentCore Browser Tool

## Overview

This demo implements browser-based automation for legacy systems using Amazon Nova Act with AgentCore Browser Tool. The key difference from the [basic demo](../legacy-system-automation/) is that the browser runs in AWS cloud rather than locally, providing scalability, session recording, and enterprise features.

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              Local Environment                               │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                     Python Orchestrator                              │   │
│  │  • Coordinates automation workflow                                   │   │
│  │  • Handles error recovery and retries                               │   │
│  │  • Processes results                                                │   │
│  └────────────────────────────────┬────────────────────────────────────┘   │
└───────────────────────────────────┼─────────────────────────────────────────┘
                                    │ WebSocket (CDP)
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              AWS Cloud                                       │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                   AgentCore Browser Tool                             │   │
│  │  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐     │   │
│  │  │  Managed Chrome │  │  Session Mgmt   │  │  Live View      │     │   │
│  │  │  Browser        │  │  & Recording    │  │  Streaming      │     │   │
│  │  └────────┬────────┘  └────────┬────────┘  └────────┬────────┘     │   │
│  │           │                    │                    │               │   │
│  └───────────┼────────────────────┼────────────────────┼───────────────┘   │
│              │                    │                    │                    │
│              ▼                    ▼                    ▼                    │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐            │
│  │  Nova Act       │  │  S3 Bucket      │  │  AWS Console    │            │
│  │  Model          │  │  (Recordings)   │  │  (Live View)    │            │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘            │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           Target Systems                                     │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐        │
│  │ phpMyAdmin  │  │ Legacy ERP  │  │ Internal    │  │ Vendor      │        │
│  │ (Demo)      │  │ Systems     │  │ Tools       │  │ Portals     │        │
│  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘        │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Components

### 1. Python Orchestrator (Local)

The local script that coordinates the automation workflow.

```python
from bedrock_agentcore.tools.browser_client import browser_session
from nova_act import NovaAct

with browser_session(region) as client:
    ws_url, headers = client.generate_ws_headers()
    
    with NovaAct(
        cdp_endpoint_url=ws_url,
        cdp_headers=headers,
        nova_act_api_key=api_key,
        starting_page=url,
    ) as nova:
        nova.act("Natural language instruction")
```

**Responsibilities:**
- Initialize AgentCore browser session
- Connect Nova Act to cloud browser via CDP
- Send natural language instructions
- Handle errors and retries
- Process and report results

### 2. AgentCore Browser Tool (AWS Cloud)

Managed Chrome browser running in AWS infrastructure.

**Features:**
- Isolated browser instances per session
- Automatic session management
- WebSocket connection via Chrome DevTools Protocol (CDP)
- Session recording to S3
- Live view streaming to AWS Console
- VPC connectivity for internal systems

**Session Lifecycle:**
```
Start Session → Browser Ready → Execute Actions → Stop Session
                     │                │
                     ▼                ▼
              Live View         Recording Upload
              (Real-time)       (On Completion)
```


### 3. Nova Act Model

AI model that interprets natural language instructions and executes browser actions.

**Capabilities:**
- Natural language understanding of browser tasks
- Visual understanding of web interfaces
- Intelligent element location
- Context-aware navigation
- Data extraction and structuring

**Authentication:**
- Uses AWS IAM via `@workflow` decorator (same as basic demo)
- No API key required
- Workflow definition created automatically

**Integration with AgentCore:**
- Connects via CDP WebSocket URL
- Uses authentication headers from AgentCore
- Executes actions on cloud browser
- Returns structured results

### 4. Session Recording (Optional)

Automatic capture of browser sessions for audit and debugging.

**Recording Flow:**
```
Browser Actions → DOM Mutations → Batch Upload → S3 Storage
                                      │
                                      ▼
                              Console Replay
```

**Storage Structure:**
```
s3://bucket/browser-recordings/
  └── session-id/
      ├── batch_1.ndjson.gz
      ├── batch_2.ndjson.gz
      └── batch_3.ndjson.gz
```

**Replay Features:**
- Video playback with timeline
- DOM inspection at any point
- Network request history
- Console log capture
- User action timeline

### 5. Live View

Real-time browser viewing via AWS Console.

**Technology:**
- Amazon DCV (Desktop Cloud Visualization)
- WebSocket streaming
- Interactive controls

**Capabilities:**
- Watch automation in real-time
- Take over control manually
- Release control back to automation
- Terminate session

## Data Flow

### Automation Execution Flow

```
1. Initialize
   ├── Create AgentCore browser session
   ├── Get WebSocket URL and auth headers
   └── Connect Nova Act to cloud browser
           │
           ▼
2. Execute
   ├── Send natural language instruction
   ├── Nova Act interprets and plans actions
   ├── Actions execute on cloud browser
   └── Results returned to orchestrator
           │
           ▼
3. Verify
   ├── Query page state
   ├── Extract data if needed
   └── Validate expected outcome
           │
           ▼
4. Cleanup
   ├── Stop browser session
   ├── Upload final recording (if enabled)
   └── Return results
```

### Connection Architecture

```
Local Script                    AWS Cloud
     │                              │
     │  1. Start Session            │
     │─────────────────────────────▶│
     │                              │ Create browser instance
     │  2. WebSocket URL + Headers  │
     │◀─────────────────────────────│
     │                              │
     │  3. CDP Connection           │
     │═════════════════════════════▶│
     │  (WebSocket)                 │ Browser ready
     │                              │
     │  4. Nova Act Commands        │
     │─────────────────────────────▶│
     │                              │ Execute actions
     │  5. Results                  │
     │◀─────────────────────────────│
     │                              │
     │  6. Stop Session             │
     │─────────────────────────────▶│
     │                              │ Cleanup + upload recording
```

## Security Design

### Authentication Flow

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  AWS IAM        │────▶│  AgentCore      │────▶│  Browser        │
│  Credentials    │     │  API            │     │  Session        │
└─────────────────┘     └─────────────────┘     └─────────────────┘
        │                                               │
        │               ┌─────────────────┐             │
        └──────────────▶│  Nova Act       │─────────────┘
                        │  (@workflow)    │  (CDP connection)
                        └─────────────────┘
```

Both AgentCore Browser and Nova Act use AWS IAM credentials:
- AgentCore: via boto3/AWS SDK
- Nova Act: via `@workflow` decorator (creates workflow definition)
```

### Credential Management

- **AWS IAM**: Used for both AgentCore and Nova Act (via @workflow decorator)
- **No API Key Required**: IAM handles all authentication
- **Legacy System Credentials**: Store in AWS Secrets Manager

### Network Security

**Public Network Mode:**
- Browser can access public internet
- Suitable for external legacy systems

**VPC Network Mode:**
- Browser connects to your VPC
- Access internal systems without VPN
- Configure via VPC endpoints

### Session Isolation

- Each session runs in isolated browser instance
- No shared state between sessions
- Session data cleared on termination
- Recordings encrypted at rest in S3

## Comparison with Local Execution

### Local Nova Act (Basic Demo)

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  Python Script  │────▶│  Nova Act SDK   │────▶│  Local Chrome   │
│  (Local)        │     │  (Local)        │     │  (Local)        │
└─────────────────┘     └─────────────────┘     └─────────────────┘
```

**Pros:** Simple setup, visual debugging
**Cons:** Requires local browser, no audit trail, single session

### AgentCore Browser (This Demo)

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  Python Script  │────▶│  AgentCore      │────▶│  Cloud Chrome   │
│  (Local)        │     │  Browser Tool   │     │  (AWS)          │
└─────────────────┘     └─────────────────┘     └─────────────────┘
```

**Pros:** No local browser, session recording, live view, VPC access, scalable
**Cons:** Requires AWS setup, network latency

## Scalability Patterns

### Parallel Sessions

```python
import asyncio
from bedrock_agentcore.tools.browser_client import browser_session

async def automate_system(system_url, instruction):
    with browser_session(region) as client:
        ws_url, headers = client.generate_ws_headers()
        with NovaAct(cdp_endpoint_url=ws_url, ...) as nova:
            return nova.act(instruction)

# Run multiple sessions in parallel
tasks = [
    automate_system(url1, "instruction1"),
    automate_system(url2, "instruction2"),
    automate_system(url3, "instruction3"),
]
results = await asyncio.gather(*tasks)
```

### Queue-Based Processing

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   SQS       │────▶│   Lambda    │────▶│  AgentCore  │
│   Queue     │     │   Trigger   │     │  Browser    │
└─────────────┘     └─────────────┘     └─────────────┘
```

## Monitoring

### CloudWatch Metrics

- Session duration
- Success/failure rates
- Action counts per session

### CloudWatch Logs

- Browser session events
- Error details
- Performance metrics

### Session History

- View all sessions in AWS Console
- Filter by status, time, browser ID
- Access recordings for completed sessions

## Extension Points

### Custom Browser Configuration

Create browsers with specific settings:
- Recording enabled/disabled
- Network mode (public/VPC)
- Execution role for S3 access
- Session timeout duration

### Integration with Other Services

- **Step Functions**: Orchestrate complex multi-system workflows
- **EventBridge**: Trigger automation on events
- **Lambda**: Serverless execution wrapper
- **Secrets Manager**: Secure credential storage
