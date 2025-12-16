# Password Reset Chatbot - Design Document

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────────────────┐
│                                    TRUST ZONES                                       │
├─────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                      │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────┐ │
│  │   PUBLIC ZONE    │  │   CREDS ZONE     │  │   AGENT ZONE     │  │ IDENTITY ZONE│ │
│  │                  │  │                  │  │                  │  │              │ │
│  │  ┌────────────┐  │  │  ┌────────────┐  │  │  ┌────────────┐  │  │ ┌──────────┐ │ │
│  │  │  Browser   │  │─▶│  │  Cognito   │  │─▶│  │ AgentCore  │  │─▶│ │ Cognito  │ │ │
│  │  │  (React)   │  │  │  │  Identity  │  │  │  │  Runtime   │  │  │ │ User Pool│ │ │
│  │  └────────────┘  │  │  │   Pool     │  │  │  └────────────┘  │  │ └──────────┘ │ │
│  │        │         │  │  └────────────┘  │  │        │         │  │      │       │ │
│  │        ▼         │  │        │         │  │        ▼         │  │      ▼       │ │
│  │  ┌────────────┐  │  │        ▼         │  │  ┌────────────┐  │  │ ┌──────────┐ │ │
│  │  │ CloudFront │  │  │  ┌────────────┐  │  │  │  Strands   │  │  │ │Email/SMS │ │ │
│  │  │    + S3    │  │  │  │ Temp AWS   │  │  │  │   Agent    │  │  │ │ Delivery │ │ │
│  │  └────────────┘  │  │  │   Creds    │  │  │  └────────────┘  │  │ └──────────┘ │ │
│  │                  │  │  └────────────┘  │  │        │         │  │              │ │
│  │  NO LOGIN        │  │                  │  │        ▼         │  │ ENFORCES:    │ │
│  │  REQUIRED        │  │  UNAUTHENTICATED │  │  ┌────────────┐  │  │ - Password   │ │
│  │                  │  │  ROLE (minimal)  │  │  │  Bedrock   │  │  │   Policy     │ │
│  │                  │  │                  │  │  │ Nova Pro   │  │  │ - Rate Limit │ │
│  └──────────────────┘  └──────────────────┘  │  └────────────┘  │  │ - Validation │ │
│                                              └──────────────────┘  └──────────────┘ │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

## Component Overview

### Modified Components (from existing sample)

| Component | Change | Reason |
|-----------|--------|--------|
| `runtime-stack.ts` | Remove JWT authorizer | Enable SigV4 authentication |
| `auth-stack.ts` | Add Identity Pool with unauthenticated role | Provide AWS credentials for anonymous users |
| `strands_agent.py` | Replace tools, update system prompt | Password reset functionality |
| `App.tsx` | Remove auth requirement for chat | Anonymous access |
| `agentcore.ts` | Use AWS SDK with SigV4 signing | Authenticate via Identity Pool credentials |
| `infra-stack.ts` | Add Cognito permissions to agent role | Enable Cognito API calls |

### New Components

| Component | Purpose |
|-----------|---------|
| `password_reset_tools.py` | Strands tools for Cognito ForgotPassword/ConfirmForgotPassword |
| Identity Pool | Provides temporary AWS credentials for unauthenticated users |
| Unauthenticated IAM Role | Minimal permissions to invoke AgentCore |

---

## Sequence Diagram: Password Reset Flow


```
┌─────────┐     ┌───────────┐     ┌───────────┐     ┌─────────┐
│  User   │     │ AgentCore │     │  Strands  │     │ Cognito │
│(Browser)│     │  Runtime  │     │   Agent   │     │         │
└────┬────┘     └─────┬─────┘     └─────┬─────┘     └────┬────┘
     │                │                 │                │
     │ "I forgot my   │                 │                │
     │  password"     │                 │                │
     ├───────────────▶│                 │                │
     │                │  invoke agent   │                │
     │                ├────────────────▶│                │
     │                │                 │                │
     │                │   detect intent │                │
     │                │   (password     │                │
     │                │    reset)       │                │
     │                │◀────────────────┤                │
     │                │                 │                │
     │ "What's your   │                 │                │
     │  email?"       │                 │                │
     │◀───────────────┤                 │                │
     │                │                 │                │
     │ "john@acme.com"│                 │                │
     ├───────────────▶│                 │                │
     │                ├────────────────▶│                │
     │                │                 │                │
     │                │                 │ ForgotPassword │
     │                │                 │ (username)     │
     │                │                 ├───────────────▶│
     │                │                 │                │
     │                │                 │   CodeSent     │
     │                │                 │◀───────────────┤
     │                │                 │                │
     │                │◀────────────────┤                │
     │ "Check your    │                 │                │
     │  email for     │                 │      ┌─────────┴─────────┐
     │  a code"       │                 │      │ Cognito sends     │
     │◀───────────────┤                 │      │ verification code │
     │                │                 │      │ via email/SMS     │
     │                │                 │      └───────────────────┘
     │ "123456"       │                 │                │
     ├───────────────▶│                 │                │
     │                ├────────────────▶│                │
     │                │                 │                │
     │ "Enter new     │                 │                │
     │  password"     │                 │                │
     │◀───────────────┤                 │                │
     │                │                 │                │
     │ "MyNewPass1"   │                 │                │
     ├───────────────▶│                 │                │
     │                ├────────────────▶│                │
     │                │                 │                │
     │                │                 │ ConfirmForgot  │
     │                │                 │ Password       │
     │                │                 │ (user,code,pw) │
     │                │                 ├───────────────▶│
     │                │                 │                │
     │                │                 │    Success     │
     │                │                 │◀───────────────┤
     │                │◀────────────────┤                │
     │ "Password      │                 │                │
     │  reset         │                 │                │
     │  successful!"  │                 │                │
     │◀───────────────┤                 │                │
     │                │                 │                │
```

---

## Security Boundaries

### What GenAI Handles (Conversational Layer)
- Natural language intent detection
- Collecting user identifier (email/username)
- Explaining password requirements
- Passing user inputs to Cognito tools
- Formatting success/error messages

### What GenAI NEVER Handles
- Password generation
- Password validation
- Verification code validation
- MFA code processing
- User existence verification
- Rate limit enforcement

### What Cognito Handles (Security Layer)
- Sending verification codes (email/SMS)
- Validating verification codes
- Enforcing password policy
- Rate limiting and throttling
- Storing credentials securely
- Audit logging

---

## Agent Tools Design

### Tool 1: `initiate_password_reset`

```python
@tool
def initiate_password_reset(username: str) -> str:
    """
    Initiate password reset for a user. Sends verification code to their 
    registered email/phone.
    
    Args:
        username: The user's email address or username
        
    Returns:
        Success message or error description
    """
```

**Cognito API**: `ForgotPassword`
**IAM Permission**: `cognito-idp:ForgotPassword`

### Tool 2: `complete_password_reset`

```python
@tool
def complete_password_reset(username: str, code: str, new_password: str) -> str:
    """
    Complete password reset with verification code and new password.
    
    Args:
        username: The user's email address or username
        code: 6-digit verification code from email/SMS
        new_password: New password meeting policy requirements
        
    Returns:
        Success message or error description
    """
```

**Cognito API**: `ConfirmForgotPassword`
**IAM Permission**: `cognito-idp:ConfirmForgotPassword`

---

## IAM Permissions (Agent Role)

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "CognitoPasswordReset",
      "Effect": "Allow",
      "Action": [
        "cognito-idp:ForgotPassword",
        "cognito-idp:ConfirmForgotPassword"
      ],
      "Resource": "arn:aws:cognito-idp:REGION:ACCOUNT:userpool/USER_POOL_ID"
    }
  ]
}
```

**Note**: These are user-level operations, not admin operations. They respect Cognito's built-in rate limiting and security controls.

---

## Error Handling Flows

### Invalid Username
```
User: "reset password for notauser@fake.com"
Agent: [calls initiate_password_reset]
Cognito: UserNotFoundException (but we don't reveal this)
Agent: "If an account exists with that email, you'll receive a verification 
        code shortly. Please check your inbox."
```

### Invalid Verification Code
```
User: "my code is 000000"
Agent: [calls complete_password_reset]
Cognito: CodeMismatchException
Agent: "That code doesn't match. Please double-check and try again. 
        Codes expire after 1 hour."
```

### Expired Code
```
User: "my code is 123456"
Agent: [calls complete_password_reset]
Cognito: ExpiredCodeException
Agent: "Your code has expired. Would you like me to send a new one?"
```

### Password Policy Violation
```
User: "my new password is 'password'"
Agent: [calls complete_password_reset]
Cognito: InvalidPasswordException
Agent: "Your password doesn't meet the requirements. It needs at least 
        8 characters, including uppercase, lowercase, and a number."
```

### Rate Limiting
```
User: "reset my password again"
Agent: [calls initiate_password_reset]
Cognito: LimitExceededException
Agent: "Too many password reset attempts. Please wait a few minutes 
        before trying again."
```

---

## Frontend Changes

### Authentication Flow

**Current Flow** (authenticated with JWT):
```
Browser → Sign In → Get JWT → Call AgentCore with Bearer token
```

**New Flow** (anonymous with SigV4):
```
Browser → Cognito Identity Pool → Temp AWS Credentials → SigV4 Signed Request → AgentCore
```

### Key Changes in `App.tsx`
1. Remove `checkingAuth` state and loading screen
2. Remove sign-in button from TopNavigation
3. Allow `handleSendMessage` without user check
4. Update welcome message for password reset context

### Key Changes in `agentcore.ts`
1. Use AWS SDK `@aws-sdk/client-bedrock-agentcore`
2. Get credentials from Cognito Identity Pool (unauthenticated)
3. AWS SDK automatically signs requests with SigV4
4. Keep session ID for conversation tracking

### Key Changes in `package.json`
Add AWS SDK dependencies:
- `@aws-sdk/client-bedrock-agentcore`
- `@aws-sdk/client-cognito-identity`
- `@aws-sdk/credential-provider-cognito-identity`

---

## Infrastructure Changes

### `runtime-stack.ts` - Remove JWT Authorizer

```typescript
// BEFORE (authenticated with JWT)
const agentRuntime = new bedrockagentcore.CfnRuntime(this, 'AgentRuntime', {
  // ...
  authorizerConfiguration: {
    customJwtAuthorizer: {
      discoveryUrl: discoveryUrl,
      allowedClients: [props.userPoolClient.userPoolClientId],
    },
  },
});

// AFTER (SigV4 authentication via IAM)
const agentRuntime = new bedrockagentcore.CfnRuntime(this, 'AgentRuntime', {
  // ...
  // authorizerConfiguration removed - uses default IAM/SigV4 authentication
});
```

### `auth-stack.ts` - Add Identity Pool

```typescript
// Cognito Identity Pool for anonymous AWS credentials
this.identityPool = new cognito.CfnIdentityPool(this, 'PasswordResetIdentityPool', {
  identityPoolName: 'password-reset-chatbot-identity-pool',
  allowUnauthenticatedIdentities: true, // CRITICAL: Allow anonymous access
});

// IAM Role for unauthenticated users
this.unauthenticatedRole = new iam.Role(this, 'UnauthenticatedRole', {
  assumedBy: new iam.WebIdentityPrincipal('cognito-identity.amazonaws.com', {
    'StringEquals': { 'cognito-identity.amazonaws.com:aud': this.identityPool.ref },
    'ForAnyValue:StringLike': { 'cognito-identity.amazonaws.com:amr': 'unauthenticated' },
  }),
  inlinePolicies: {
    BedrockAgentCoreAccess: new iam.PolicyDocument({
      statements: [
        new iam.PolicyStatement({
          effect: iam.Effect.ALLOW,
          actions: ['bedrock-agentcore:InvokeAgentRuntime'],
          resources: ['*'],
        }),
      ],
    }),
  },
});
```

### `infra-stack.ts` - Add Cognito Permissions

```typescript
// Add to agentRole policy statements
agentRole.addToPolicy(new iam.PolicyStatement({
  sid: 'CognitoPasswordReset',
  effect: iam.Effect.ALLOW,
  actions: [
    'cognito-idp:ForgotPassword',
    'cognito-idp:ConfirmForgotPassword',
  ],
  resources: [`arn:aws:cognito-idp:${this.region}:${this.account}:userpool/*`],
}));
```

---

## System Prompt Design

```
You are a password reset assistant. Your ONLY job is to help users reset 
their password through a secure, guided process.

CAPABILITIES:
- Detect when users want to reset their password
- Collect their email address
- Initiate password reset (sends verification code)
- Guide them through entering the code and new password
- Complete the password reset

SECURITY RULES:
- NEVER generate or suggest passwords
- NEVER validate codes yourself - always use the tool
- NEVER reveal if a user exists or not
- NEVER store or log passwords
- Always use the provided tools for Cognito operations

CONVERSATION FLOW:
1. Greet user and detect intent
2. Ask for email address
3. Call initiate_password_reset tool
4. Ask for verification code (sent to their email)
5. Ask for new password (explain requirements: 8+ chars, upper, lower, digit)
6. Call complete_password_reset tool
7. Confirm success or handle errors

PASSWORD REQUIREMENTS:
- Minimum 8 characters
- At least one uppercase letter
- At least one lowercase letter
- At least one digit

If the user asks about anything other than password reset, politely redirect 
them to the password reset flow or suggest they contact support for other issues.
```

---

## Deployment Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              CDK Stacks                                      │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌─────────────────┐  ┌─────────────────────┐  ┌─────────────────────────┐  │
│  │PasswordResetInfra│  │ PasswordResetAuth   │  │ PasswordResetRuntime    │  │
│  │                 │  │                     │  │                         │  │
│  │ - ECR Repo      │  │ - User Pool         │  │ - AgentCore Runtime     │  │
│  │ - CodeBuild     │  │ - Pool Client       │  │ - SigV4 Authentication  │  │
│  │ - IAM Role      │  │ - Identity Pool     │  │                         │  │
│  │   + Cognito     │  │ - Unauthenticated   │  │                         │  │
│  │     permissions │  │   IAM Role          │  │                         │  │
│  └────────┬────────┘  └──────────┬──────────┘  └────────────┬────────────┘  │
│           │                      │                          │               │
│           └──────────────────────┼──────────────────────────┘               │
│                                  │                                          │
│                      ┌───────────┴───────────┐                              │
│                      │ PasswordResetFrontend │                              │
│                      │                       │                              │
│                      │  - S3 + CloudFront    │                              │
│                      │  - React App          │                              │
│                      │  - AWS SDK (SigV4)    │                              │
│                      └───────────────────────┘                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Cost Estimate

| Service | Usage | Estimated Cost |
|---------|-------|----------------|
| AgentCore Runtime | ~100 resets/month, 30s each | $2-5/month |
| Bedrock (Claude Haiku) | ~500 messages/month | $1-3/month |
| Cognito | First 10K MAU free | $0 |
| CloudFront | Free tier | $0 |
| S3 | Static hosting | <$1/month |
| **Total** | | **$3-10/month** |
