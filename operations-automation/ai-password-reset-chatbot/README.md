# AI Password Reset Chatbot

A conversational chatbot that guides users through Cognito's native password reset flow using Amazon Bedrock AgentCore Runtime, AgentCore Memory, and Strands Agents with Nova 2 Lite.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────────────┐
│                              Frontend (React + CloudFront)                          │
│  ┌─────────────┐    ┌─────────────────┐    ┌─────────────────────────────────────┐  │
│  │   Browser   │--->│  Cognito        │--->│         AWS Credentials             │  │
│  │   (React)   │    │  Identity Pool  │    │      (Temporary SigV4 Auth)         │  │
│  │             │    │ (Unauth Role)   │    │                                     │  │
│  │ Anonymous   │    │                 │    │                                     │  │
│  │  Access     │    │  AWS Creds      │    │                                     │  │
│  └─────────────┘    └─────────────────┘    └─────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      v SigV4 Signed Requests
┌────────────────────────────────────────────────────────────────────────────────────┐
│                        Amazon Bedrock AgentCore Runtime                            │
│                                                                                    │
│  ┌─────────────────────────────────────────────────────────────────────────────┐   │
│  │                          AgentCore Memory                                   │   │
│  │  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────────────┐  │   │
│  │  │ Session Storage │  │ Conversation    │  │    Context Persistence      │  │   │
│  │  │   (8hr max)     │  │    History      │  │   (Email, Codes, State)     │  │   │
│  │  └─────────────────┘  └─────────────────┘  └─────────────────────────────┘  │   │
│  └─────────────────────────────────────────────────────────────────────────────┘   │
│                                      │                                             │
│                                      v                                             │
│  ┌─────────────────────────────────────────────────────────────────────────────┐   │
│  │                         Strands Agent Framework                             │   │
│  │                                                                             │   │
│  │  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────────────┐  │   │
│  │  │ Amazon Nova     │  │ Agent Tools     │  │    Conversation Manager     │  │   │
│  │  │   2 Lite        │  │                 │  │   (Session Awareness)       │  │   │
│  │  │                 │  │ • initiate_     │  │                             │  │   │
│  │  │ (Foundation     │  │   password_     │  │                             │  │   │
│  │  │  Model)         │  │   reset()       │  │                             │  │   │
│  │  │                 │  │                 │  │                             │  │   │
│  │  │ • Reasoning     │  │ • complete_     │  │                             │  │   │
│  │  │ • Tool Calling  │  │   password_     │  │                             │  │   │
│  │  │ • Streaming     │  │   reset()       │  │                             │  │   │
│  │  └─────────────────┘  └─────────────────┘  └─────────────────────────────┘  │   │
│  └─────────────────────────────────────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      v Cognito API Calls
┌─────────────────────────────────────────────────────────────────────────────────────┐
│                           Amazon Cognito User Pool                                  │
│                                                                                     │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────────────────────┐  │
│  │ User Accounts   │  │ Password Reset  │  │         Email/SMS Delivery          │  │
│  │                 │  │                 │  │                                     │  │
│  │ • Credentials   │  │ • Code Gen      │  │ • Verification Codes                │  │
│  │ • Profiles      │  │ • Validation    │  │ • Reset Instructions                │  │
│  │ • Status        │  │ • Rate Limiting │  │ • Security Notifications            │  │
│  │ • Policies      │  │ • Expiration    │  │                                     │  │
│  └─────────────────┘  └─────────────────┘  └─────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

## Foundation

This demo is built upon the [Sample Amazon Bedrock AgentCore Fullstack WebApp](https://github.com/aws-samples/sample-amazon-bedrock-agentcore-fullstack-webapp) repository, extending it with password reset functionality and Cognito integration.

## Core Technologies

### AgentCore Runtime & Memory
- **Runtime Environment**: Managed platform for deploying and executing custom Strands Agents with your GenAI business logic
- **Session Management**: Persistent conversation context using `runtimeSessionId` parameter
- **Memory Integration**: Automatic conversation history and state retention across interactions
- **Security**: Built-in SigV4 authentication and IAM integration

### Strands Agent Framework
- **Agent Implementation**: Conversational AI with tool integration capabilities
- **Nova 2 Lite Integration**: Optimized prompting for consistent, secure responses
- **Tool System**: Custom password reset tools that wrap external APIs
- **Conversation Manager**: Session-aware context handling and response streaming

### System Prompt Design
The agent uses a structured system prompt following [Nova prompting best practices](https://docs.aws.amazon.com/nova/latest/userguide/prompting-precision.html):

```
Task Summary: Password reset assistant for secure, guided user experience
Context Information: Session state, user verification status, conversation history
Model Instructions: Security boundaries, tool usage patterns, response formatting
Response Style: Helpful, secure, step-by-step guidance with clear next actions
```

**Key Prompt Features:**
- **Security Boundaries**: Agent receives user input but delegates all validation and storage to Cognito APIs
- **Tool Delegation**: All security operations routed to Cognito APIs
- **Session Awareness**: Maintains context across conversation turns
- **Error Handling**: Graceful fallbacks and user guidance for edge cases

## Authentication Architecture

This solution uses **dual Cognito integration** for different purposes:

### 1. **User Database** (Password Reset Target)
- **Service**: Cognito User Pool
- **Purpose**: Stores actual user accounts that need password resets
- **Integration Point**: Agent tools call `cognito-idp` APIs
- **Customization**: Replace with your existing user database (RDS, LDAP, etc.)

### 2. **UI Access** (Anonymous Chat Access)  
- **Service**: Cognito Identity Pool (unauthenticated role)
- **Purpose**: Provides temporary AWS credentials for browser API calls
- **Integration Point**: Frontend SigV4 signing for AgentCore requests
- **Customization**: Keep for anonymous access or replace with authenticated flow

## Prerequisites

- AWS CLI v2.31.13+ ([Installation Guide](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html))
- Node.js 22+
- AWS credentials configured with permissions for CloudFormation, Lambda, S3, ECR, CodeBuild, Cognito, and IAM
- AgentCore available in your target region ([Check availability](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agentcore-regions.html))

## Quick Start

### One-Command Deploy

**Windows (PowerShell):**
```powershell
.\run-demo.ps1
```

**macOS/Linux:**
```bash
chmod +x run-demo.sh scripts/build-frontend.sh
./run-demo.sh
```

**Time:** ~10 minutes (CodeBuild container compilation takes 5-10 minutes)

### Test the Demo

1. Create a test user in Cognito with an email address you can access:
```bash
# Get User Pool ID from deployment output, then:
aws cognito-idp admin-create-user \
  --user-pool-id <USER_POOL_ID> \
  --username your.email@example.com \
  --user-attributes Name=email,Value=your.email@example.com \
  --message-action SUPPRESS
```

2. **CRITICAL:** Set a permanent password to change user status from `FORCE_CHANGE_PASSWORD` to `CONFIRMED`:
```bash
aws cognito-idp admin-set-user-password \
  --user-pool-id <USER_POOL_ID> \
  --username your.email@example.com \
  --password TempPass123! \
  --permanent
```

**Why this step is required:** Cognito will NOT send password reset emails to users in `FORCE_CHANGE_PASSWORD` status. Setting a permanent password changes the status to `CONFIRMED`, which enables password reset functionality.

3. Open the CloudFront URL from deployment output
4. Type "I forgot my password" or click a suggested prompt
5. Follow the chatbot's guidance through the reset flow
6. Check your email for the verification code (may take 1-5 minutes)

## Project Structure

```
ai-password-reset-chatbot/
├── agent/                    # Strands agent with password reset tools
│   ├── strands_agent.py      # Agent implementation + Cognito tools
│   ├── requirements.txt      # Python dependencies
│   └── Dockerfile            # ARM64 container definition
├── cdk/                      # Infrastructure as Code
│   ├── bin/app.ts            # CDK app entry point
│   ├── lib/
│   │   ├── infra-stack.ts    # ECR, CodeBuild, IAM (with Cognito permissions)
│   │   ├── auth-stack.ts     # Cognito User Pool + Identity Pool
│   │   ├── runtime-stack.ts  # AgentCore Runtime deployment
│   │   └── frontend-stack.ts # CloudFront + S3 hosting
│   ├── package.json          # CDK dependencies
│   └── cdk.json              # CDK configuration
├── frontend/                 # React app (anonymous access)
│   ├── src/
│   │   ├── App.tsx           # Chat UI with streaming support
│   │   ├── agentcore.ts      # AgentCore client with session management
│   │   └── markdown.css      # Chat styling
│   ├── package.json          # Frontend dependencies
│   └── vite.config.ts        # Build configuration
├── scripts/
│   ├── build-frontend.ps1    # Frontend build (Windows)
│   └── build-frontend.sh     # Frontend build (macOS/Linux)
├── tests/                    # Test scripts and validation
│   ├── simple_test.py        # Basic agent functionality test
│   ├── test_agent_debug.py   # Agent debugging utilities
│   ├── test_deployed_agent.py # End-to-end deployment testing
│   ├── test_session_flow.py  # Session management validation
│   ├── test_session_persistence.py # Session state testing
│   ├── test_streaming_debug.py # Streaming response testing
│   └── test_streaming_simulation.py # Streaming simulation
├── run-demo.ps1              # One-command deploy (Windows)
├── run-demo.sh               # One-command deploy (macOS/Linux)
├── ARCHITECTURE.md           # Technical design documentation
└── README.md                 # This file
```

## CDK Stacks

| Stack | Purpose | Key Resources | Dependencies |
|-------|---------|---------------|--------------|
| PasswordResetInfra-{region} | Build pipeline & IAM | ECR Repository, CodeBuild Project, Agent Runtime IAM Role, S3 Source Bucket | None |
| PasswordResetAuth-{region} | Identity management | Cognito User Pool, Identity Pool, Unauthenticated IAM Role | None |
| PasswordResetRuntime-{region} | Agent deployment | AgentCore Runtime, CodeBuild Trigger, Build Waiter | Infra + Auth |
| PasswordResetFrontend-{region} | Web hosting | S3 Bucket, CloudFront Distribution, Website Deployment | Runtime + Auth |

**Stack Details:**

**PasswordResetInfra-{region}:**
- **ECR Repository**: Stores the containerized Strands Agent
- **CodeBuild Project**: Compiles agent code into container images
- **Agent Runtime IAM Role**: Permissions for AgentCore to access Cognito APIs
- **S3 Source Bucket**: Stores agent source code for builds

**PasswordResetAuth-{region}:**
- **Cognito User Pool**: Target user accounts for password resets
- **Cognito Identity Pool**: Anonymous AWS credentials for frontend
- **Unauthenticated IAM Role**: Minimal permissions for AgentCore API access

**PasswordResetRuntime-{region}:**
- **AgentCore Runtime**: Managed hosting environment for the Strands Agent
- **Custom Resources**: Triggers CodeBuild and waits for container compilation
- **Environment Variables**: User Pool configuration passed to agent

**PasswordResetFrontend:**
- **S3 + CloudFront**: Static website hosting with global CDN
- **Build Integration**: Automatically deploys frontend with runtime configuration

## Agent Tools

The agent has two tools that wrap Cognito APIs:

### `initiate_password_reset(username)`
- Calls `cognito-idp:ForgotPassword`
- Sends verification code to user's email
- Returns generic message (doesn't reveal if user exists)

### `complete_password_reset(username, code, new_password)`
- Calls `cognito-idp:ConfirmForgotPassword`
- Validates code and sets new password
- Returns success or specific error guidance

## Session Management & Streaming

### Session Persistence
The chatbot maintains conversation context across multiple interactions using AgentCore Runtime sessions:

- **Session ID**: Each conversation uses a unique UUID session identifier
- **Context Retention**: Agent remembers email addresses, verification codes, and conversation state
- **Session Duration**: Sessions persist for up to 8 hours or 15 minutes of inactivity
- **Implementation**: Uses `runtimeSessionId` parameter in `InvokeAgentRuntimeCommand` (AWS SDK best practice)

### Real-Time Streaming
Responses stream in real-time for a natural chat experience:

- **Server-Sent Events (SSE)**: AgentCore returns streaming responses as `text/event-stream`
- **Progressive Display**: Text appears word-by-word as the agent generates responses
- **Chunk Processing**: Frontend processes `data: ` lines from SSE stream
- **Fallback Handling**: Gracefully handles both streaming and non-streaming responses

## Nova 2 Lite Optimization

The agent system prompt follows [Amazon Nova prompting best practices](https://docs.aws.amazon.com/nova/latest/userguide/prompting-precision.html) for optimal performance:

- **Clear prompt sections**: Task Summary, Context Information, Model Instructions, Response Style
- **Specific instructions**: Uses strong emphasis words (NEVER, ALWAYS) for critical security rules
- **Structured format**: Organized sections with bullet points for better model comprehension
- **Contextual information**: Detailed session state and tool usage guidance

This structured approach ensures Nova 2 Lite provides consistent, accurate responses while maintaining security boundaries.

## Security Model

### Password Reset Security (User Pool)
| Responsibility | Owner |
|---------------|-------|
| Intent detection, conversation flow | GenAI Agent |
| Password policy enforcement | Cognito User Pool |
| Verification code delivery | Cognito User Pool |
| Code validation | Cognito User Pool |
| Rate limiting | Cognito User Pool |
| User credential storage | Cognito User Pool |

### Chat UI Security (Identity Pool)
| Responsibility | Owner |
|---------------|-------|
| Anonymous access credentials | Cognito Identity Pool |
| AWS API authentication | SigV4 signing |
| AgentCore access permissions | IAM unauthenticated role |
| Session management | AgentCore Runtime |

**Security Boundaries:**
- **Agent receives** user input but delegates all validation and storage to Cognito APIs
- **All security operations** delegated to Cognito User Pool
- **Anonymous UI access** via Cognito Identity Pool with minimal IAM permissions
- **API authentication** via SigV4 signing ensures requests are authenticated at AWS level
- **Complete separation** between UI authentication and user database

### Production Security Enhancements

For production deployments, consider these additional security measures:

**Rate Limiting & Monitoring:**
- CloudWatch alarms on password reset attempt frequency
- WAF rules to limit requests per IP address
- API Gateway throttling for additional protection

**Session Security:**
- Shorter AgentCore session timeouts (reduce from 8hr default)
- CloudTrail logging for all password reset operations
- Regular security audits of IAM permissions

**Network Security:**
- VPC deployment for AgentCore Runtime (private subnets)
- PrivateLink endpoints for Cognito API calls
- Custom domain with AWS Certificate Manager


## IAM Permissions

The agent role includes minimal Cognito permissions:

```json
{
  "Effect": "Allow",
  "Action": [
    "cognito-idp:ForgotPassword",
    "cognito-idp:ConfirmForgotPassword"
  ],
  "Resource": "arn:aws:cognito-idp:REGION:ACCOUNT:userpool/*"
}
```

These are user-level operations (not admin) that respect Cognito's built-in rate limiting.

## Cost Estimate

| Service | Usage | Estimated Cost |
|---------|-------|----------------|
| AgentCore Runtime | ~100 resets/month, 30s each | $2-5/month |
| Bedrock (Nova Pro) | ~500 messages/month | $1-3/month |
| Cognito | First 10K MAU free | $0 |
| CloudFront | Free tier | $0 |
| S3 | Static hosting | <$1/month |
| **Total** | | **$3-10/month** |

## Cleanup

```bash
cd cdk
npx cdk destroy PasswordResetFrontend --no-cli-pager
npx cdk destroy PasswordResetRuntime --no-cli-pager
npx cdk destroy PasswordResetAuth --no-cli-pager
npx cdk destroy PasswordResetInfra --no-cli-pager
```

## Troubleshooting

### "AgentCore is not available in region"
Check [AgentCore regional availability](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agentcore-regions.html) and set your region:
```powershell
$env:AWS_DEFAULT_REGION = "us-east-1"
```

### "Container failed to start"
Check CloudWatch logs:
```bash
aws logs tail /aws/bedrock-agentcore/runtimes/password_reset_agent-* --follow --no-cli-pager
```

### "Session not persisting between messages"
Ensure the frontend is using the correct session parameter:
- **Correct**: `runtimeSessionId` in `InvokeAgentRuntimeCommand`
- **Incorrect**: `sessionId` (deprecated parameter name)
- **Verification**: Check browser console for consistent session ID logs across messages

### "Streaming not working"
Verify streaming configuration:
- **Response Type**: Should be `text/event-stream` in network tab
- **Console Logs**: Look for "📥 RECEIVED CHUNK:" messages
- **Progressive Display**: Text should appear word-by-word, not all at once

### "Rate limit exceeded"
Cognito enforces rate limits on password reset attempts. Wait a few minutes before retrying.

### "Invalid verification code"
Codes expire after 1 hour. Request a new code through the chatbot.

### "Not receiving verification emails"
**Common causes:**
1. **Corporate email filtering**: Corporate email systems (e.g., company domains) may block emails from Cognito's default email service
2. **Email delivery delay**: Cognito's default email service can take 5-15 minutes to deliver emails
3. **Spam folder**: Check your spam/junk folder

**Solutions:**
- Use a personal email address (Gmail, Outlook, Yahoo, etc.) for testing
- Ensure user status is `CONFIRMED` (not `FORCE_CHANGE_PASSWORD`):
  ```bash
  aws cognito-idp admin-set-user-password --user-pool-id <USER_POOL_ID> --username your.email@example.com --password TempPass123! --permanent
  ```
- For production deployments, configure Cognito to use Amazon SES instead of the default email service

## Integration with Existing Systems

### Replacing the User Database

The demo uses Cognito User Pool as the user database, but you can integrate with any user management system:

**1. Database Integration (RDS, DynamoDB)**
```python
# In agent/strands_agent.py, replace Cognito calls:
import psycopg2  # or your database client

@tool
def initiate_password_reset(username: str) -> str:
    # Replace cognito_client.forgot_password() with:
    conn = psycopg2.connect(DATABASE_URL)
    cursor = conn.cursor()
    
    # Generate reset token, send email, store in database
    reset_token = generate_secure_token()
    cursor.execute("INSERT INTO password_resets (email, token, expires) VALUES (%s, %s, %s)", 
                   (username, reset_token, expires_at))
    
    send_reset_email(username, reset_token)
    return f"If an account exists for '{username}', a reset link has been sent."
```

**2. External API Integration**
```python
# Replace Cognito with your user service API:
import requests

@tool
def initiate_password_reset(username: str) -> str:
    response = requests.post(f"{USER_SERVICE_URL}/password-reset/initiate", 
                           json={"email": username})
    return "Reset instructions sent if account exists."
```

**3. LDAP/Active Directory Integration**
```python
# For enterprise directory services:
import ldap3

@tool
def initiate_password_reset(username: str) -> str:
    # Integrate with your LDAP/AD password reset workflow
    # This might involve generating tickets, sending notifications, etc.
```

### Customizing Chat UI Authentication

**Option 1: Keep Anonymous Access (Recommended)**
- No changes needed
- Users can access chatbot without login
- Suitable for public-facing password reset

**Option 2: Require Authentication**
```typescript
// In frontend/src/agentcore.ts, replace anonymous auth with:
import { Auth } from 'aws-amplify';

// Use authenticated Cognito user credentials instead of anonymous
const credentials = await Auth.currentCredentials();
```

## Customization

### Change the Model
Edit `agent/strands_agent.py`:
```python
model_id = "amazon.nova-lite-v1:0"  # For lighter workloads
model_id = "amazon.nova-premier-v1:0"  # For complex reasoning
```

### Modify Password Policy (if using Cognito)
Edit `cdk/lib/auth-stack.ts`:
```typescript
passwordPolicy: {
  minLength: 12,  // Increase minimum length
  requireSymbols: true,  // Require special characters
  requireUppercase: true,
  requireLowercase: true,
  requireNumbers: true,
}
```

### Add Custom Prompts
Edit `frontend/src/App.tsx` in the `getSupportPrompts()` function:
```typescript
const getSupportPrompts = () => {
  return [
    { id: 'forgot', text: 'I forgot my password' },
    { id: 'locked', text: 'My account is locked' },
    { id: 'expired', text: 'My password expired' },
    // Add your custom prompts here
  ];
};
```

### Customize Agent Behavior
Edit `agent/strands_agent.py`:
```python
SYSTEM_PROMPT_TEMPLATE = """
You are a password reset assistant for [YOUR COMPANY].
[Add your specific instructions, branding, policies]
"""
```

## Contributing

We welcome community contributions! Please see [CONTRIBUTING.md](../../CONTRIBUTING.md) for guidelines.

## Security

See [CONTRIBUTING](../../CONTRIBUTING.md#security-issue-notifications) for more information.

## License

This library is licensed under the MIT-0 License. See the [LICENSE](../../LICENSE) file.
