# AI Password Reset Chatbot

A conversational chatbot that guides users through Cognito's native password reset flow using Amazon Bedrock AgentCore and Nova 2 Lite.

## Architecture

```
┌─────────────┐     ┌─────────────────┐     ┌─────────────────┐     ┌─────────────┐
│   Browser   │────▶│  Cognito        │────▶│  AgentCore      │────▶│   Cognito   │
│   (React)   │     │  Identity Pool  │     │  Runtime        │     │  User Pool  │
│             │     │  (Unauth Role)  │     │ (Nova 2 Lite)   │     │             │
│  Anonymous  │     │                 │     │  SigV4 Auth     │     │  Password   │
│   Access    │     │  AWS Creds      │     │                 │     │  Reset APIs │
└─────────────┘     └─────────────────┘     └─────────────────┘     └─────────────┘
       │                    │                      │                      │
       ▼                    ▼                      ▼                      ▼
┌─────────────┐     ┌─────────────────┐     ┌─────────────────┐     ┌─────────────┐
│ CloudFront  │     │  Temporary AWS  │     │  Strands Agent  │     │ Email/SMS   │
│    + S3     │     │  Credentials    │     │  + Tools        │     │ Delivery    │
└─────────────┘     └─────────────────┘     └─────────────────┘     └─────────────┘
```

## Two Distinct Cognito Use Cases

**⚠️ Important:** This solution uses Cognito for two completely separate purposes. Understanding this distinction is crucial for customization and integration.

### 1. User Database (Target of Password Resets)

**Purpose:** The actual user accounts that need password resets
**Cognito Service:** User Pool
**Location:** `PasswordResetAuth` CDK stack
**Configuration:** `cdk/lib/auth-stack.ts`

This is where your application's users are stored. The chatbot performs password reset operations on accounts in this User Pool.

**For Production Integration:**
- Replace this User Pool with your existing user database
- Modify the agent tools (`agent/strands_agent.py`) to call your user management APIs instead of Cognito
- Update IAM permissions to access your user database (RDS, DynamoDB, etc.)
- Keep the same tool interface (`initiate_password_reset`, `complete_password_reset`)

**Example Integration Points:**
```python
# Instead of Cognito APIs, call your user service:
# cognito_client.forgot_password() → your_user_service.initiate_reset()
# cognito_client.confirm_forgot_password() → your_user_service.complete_reset()
```

### 2. Chat UI Authentication (Anonymous Access)

**Purpose:** Allows anonymous users to access the chatbot interface
**Cognito Service:** Identity Pool (unauthenticated role)
**Location:** `PasswordResetAuth` CDK stack  
**Configuration:** `frontend/src/agentcore.ts`

This provides temporary AWS credentials so the browser can make authenticated API calls to AgentCore without requiring user login.

**For Production Integration:**
- Keep this Identity Pool for anonymous access, OR
- Replace with authenticated access (require login to use chatbot)
- Modify frontend authentication in `frontend/src/agentcore.ts`
- Update IAM roles for appropriate permissions

**Key Design Decisions:**
- Anonymous access via Cognito Identity Pool (unauthenticated role)
- SigV4 signed requests to AgentCore (required by AWS APIs)
- GenAI handles conversation flow, Cognito handles all security
- Agent never sees, generates, or validates passwords/codes

## Prerequisites

- AWS CLI v2.31.13+ ([Installation Guide](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html))
- Node.js 22+
- AWS credentials configured with permissions for CloudFormation, Lambda, S3, ECR, CodeBuild, Cognito, and IAM
- AgentCore available in your target region ([Check availability](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agentcore-regions.html))

## Quick Start

### One-Command Deploy

**Windows (PowerShell):**
```powershell
.\deploy-all.ps1
```

**macOS/Linux:**
```bash
chmod +x deploy-all.sh scripts/build-frontend.sh
./deploy-all.sh
```

**Time:** ~10 minutes (CodeBuild container compilation takes 5-10 minutes)

### Test the Demo

1. Create a test user in Cognito:
```bash
# Get User Pool ID from deployment output, then:
aws cognito-idp admin-create-user \
  --user-pool-id <USER_POOL_ID> \
  --username test@youremail.com \
  --temporary-password TempPass1! \
  --message-action SUPPRESS
```

2. Open the CloudFront URL from deployment output
3. Type "I forgot my password" or click a suggested prompt
4. Follow the chatbot's guidance through the reset flow

## Project Structure

```
ai-password-reset-chatbot/
├── agent/                    # Strands agent with password reset tools
│   ├── strands_agent.py      # Agent implementation + Cognito tools
│   ├── requirements.txt      # Python dependencies
│   └── Dockerfile            # ARM64 container definition
├── cdk/                      # Infrastructure as Code
│   ├── bin/app.ts            # CDK app entry point
│   └── lib/
│       ├── infra-stack.ts    # ECR, CodeBuild, IAM (with Cognito permissions)
│       ├── auth-stack.ts     # Cognito User Pool
│       ├── runtime-stack.ts  # AgentCore Runtime (NO JWT auth)
│       └── frontend-stack.ts # CloudFront + S3
├── frontend/                 # React app (anonymous access)
│   └── src/
│       ├── App.tsx           # Chat UI (no auth required)
│       └── agentcore.ts      # AgentCore client (no JWT)
├── scripts/
│   ├── build-frontend.ps1    # Frontend build (Windows)
│   └── build-frontend.sh     # Frontend build (macOS/Linux)
├── deploy-all.ps1            # One-command deploy (Windows)
└── deploy-all.sh             # One-command deploy (macOS/Linux)
```

## CDK Stacks

| Stack | Purpose | Key Resources | Cognito Usage |
|-------|---------|---------------|---------------|
| PasswordResetInfra | Build pipeline | ECR, CodeBuild, IAM Role | None |
| PasswordResetAuth | Identity & user management | **User Pool** (target users), **Identity Pool** (UI auth), IAM Roles | Both use cases |
| PasswordResetRuntime | Agent runtime | AgentCore Runtime (SigV4 authentication) | None |
| PasswordResetFrontend | Web UI | S3, CloudFront | None |

**PasswordResetAuth Stack Details:**
- **Cognito User Pool**: Contains the actual user accounts that need password resets (Use Case #1)
- **Cognito Identity Pool**: Provides anonymous AWS credentials for the chat UI (Use Case #2)
- **IAM Roles**: Permissions for both anonymous UI access and agent operations

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
- **Agent NEVER** generates, validates, or stores passwords
- **Agent NEVER** validates verification codes  
- **All security operations** delegated to Cognito User Pool
- **Anonymous UI access** via Cognito Identity Pool with minimal IAM permissions
- **API authentication** via SigV4 signing ensures requests are authenticated at AWS level
- **Complete separation** between UI authentication and user database


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

### "Rate limit exceeded"
Cognito enforces rate limits on password reset attempts. Wait a few minutes before retrying.

### "Invalid verification code"
Codes expire after 1 hour. Request a new code through the chatbot.

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
PASSWORD_RESET_SYSTEM_PROMPT = """
You are a password reset assistant for [YOUR COMPANY].
[Add your specific instructions, branding, policies]
"""
```

## License

MIT-0
