# Password Reset Chatbot - Architecture

## Overview

This demo implements a conversational password reset assistant using Amazon Bedrock AgentCore with Nova Pro. The key architectural decision is the separation of concerns: GenAI handles the conversational UX while Cognito handles all security-critical operations.

## Component Diagram

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

## Data Flow

### Password Reset Sequence

```
User                    Frontend           Identity Pool         AgentCore              Cognito
 │                         │                     │                    │                     │
 │ "I forgot my password"  │                     │                    │                     │
 ├────────────────────────▶│                     │                    │                     │
 │                         │  GetCredentials()  │                    │                     │
 │                         ├────────────────────▶│                    │                     │
 │                         │  Temp AWS Creds    │                    │                     │
 │                         │◀────────────────────┤                    │                     │
 │                         │  SigV4 Signed POST │                    │                     │
 │                         ├─────────────────────────────────────────▶│                     │
 │                         │                     │                    │ (detect intent)     │
 │                         │◀─────────────────────────────────────────┤                     │
 │ "What's your email?"    │                     │                    │                     │
 │◀────────────────────────┤                     │                    │                     │
 │                         │                     │                    │                     │
 │ "user@example.com"      │                     │                    │                     │
 ├────────────────────────▶│                     │                    │                     │
 │                         ├─────────────────────────────────────────▶│                     │
 │                         │                     │                    │ ForgotPassword()    │
 │                         │                     │                    ├────────────────────▶│
 │                         │                     │                    │     CodeSent        │
 │                         │                     │                    │◀────────────────────┤
 │                         │◀─────────────────────────────────────────┤                     │
 │ "Check your email"      │                     │                    │      ┌──────────────┤
 │◀────────────────────────┤                     │                    │      │ Send email   │
 │                         │                     │                    │      │ with code    │
 │ "123456"                │                     │                    │      └──────────────┘
 ├────────────────────────▶│                     │                    │                     │
 │                         ├─────────────────────────────────────────▶│                     │
 │ "Enter new password"    │                     │                    │                     │
 │◀────────────────────────┤                     │                    │                     │
 │                         │                     │                    │                     │
 │ "MyNewPass1"            │                     │                    │                     │
 ├────────────────────────▶│                     │                    │                     │
 │                         ├─────────────────────────────────────────▶│                     │
 │                         │                     │                    │ ConfirmForgotPwd()  │
 │                         │                     │                    ├────────────────────▶│
 │                         │                     │                    │     Success         │
 │                         │                     │                    │◀────────────────────┤
 │                         │◀─────────────────────────────────────────┤                     │
 │ "Password reset!"       │                     │                    │                     │
 │◀────────────────────────┤                     │                    │                     │
```


## Security Boundaries

### What GenAI Handles (Conversational Layer)
- Natural language intent detection
- Collecting user identifier (email/username)
- Explaining password requirements in plain language
- Passing user inputs to Cognito tools
- Formatting success/error messages for users
- Guiding users through multi-step flow

### What GenAI NEVER Handles
- Password generation or suggestion
- Password validation or strength checking
- Verification code validation
- MFA code processing
- User existence verification (prevents enumeration)
- Rate limit enforcement

### What Cognito Handles (Security Layer)
- Sending verification codes via email/SMS
- Validating verification codes
- Enforcing password policy (length, complexity)
- Rate limiting and throttling
- Storing credentials securely (hashed)
- Audit logging via CloudTrail

## Key Architectural Decisions

### 1. Anonymous Access via Cognito Identity Pool

**Decision:** Use Cognito Identity Pool with unauthenticated role for SigV4 signing

**Rationale:**
- Users who forgot their password cannot authenticate to User Pool
- AgentCore requires SigV4 signed requests (no truly anonymous access)
- Cognito Identity Pool provides temporary AWS credentials without login
- Unauthenticated role has minimal permissions (InvokeAgentRuntime only)

**Architecture:**
```
Browser → Cognito Identity Pool → Temporary AWS Credentials → SigV4 Signed Request → AgentCore
```

**Trade-off:** Requires AWS SDK in frontend for credential management and request signing. This is the standard pattern for anonymous AWS API access.

### 2. Direct Cognito API Calls from Agent

**Decision:** Agent calls Cognito APIs directly via boto3 tools

**Alternatives Considered:**
- Lambda function as intermediary (rejected: adds complexity)
- API Gateway with Cognito authorizer (rejected: requires auth)

**Rationale:** Simplest architecture that meets security requirements. Agent role has minimal permissions (ForgotPassword, ConfirmForgotPassword only).

### 3. User Pool Client ID via Environment Variable

**Decision:** Pass Cognito User Pool Client ID to agent container via environment variable

**Rationale:** 
- Client ID is not sensitive (public client)
- Allows agent to call Cognito APIs without hardcoding
- Easy to change for different environments

### 4. Nova Pro Model Selection

**Decision:** Use Amazon Nova Pro for the agent

**Rationale:**
- Good balance of capability and cost
- Handles multi-turn conversations well
- Follows tool-calling instructions reliably

## Infrastructure Components

### CDK Stack Dependencies

```
PasswordResetInfra
       │
       ├──▶ PasswordResetAuth
       │           │
       └───────────┼──▶ PasswordResetRuntime
                   │           │
                   └───────────┴──▶ PasswordResetFrontend
```

### IAM Permissions Model

```
Cognito Identity Pool - Unauthenticated Role
└── bedrock-agentcore:InvokeAgentRuntime (allows anonymous users to call agent)

AgentCore Runtime Role
├── ECR: Pull container images
├── CloudWatch: Write logs and metrics
├── X-Ray: Write traces
├── Bedrock: Invoke Nova Pro model
└── Cognito: ForgotPassword, ConfirmForgotPassword (scoped to User Pool)
```

## Error Handling

| Error | Source | Agent Response |
|-------|--------|----------------|
| UserNotFoundException | Cognito | Generic "code sent" message (no enumeration) |
| CodeMismatchException | Cognito | "Code incorrect, please try again" |
| ExpiredCodeException | Cognito | "Code expired, want a new one?" |
| InvalidPasswordException | Cognito | Explains which requirement failed |
| LimitExceededException | Cognito | "Too many attempts, wait X minutes" |

## Observability

- **CloudWatch Logs:** `/aws/bedrock-agentcore/runtimes/password_reset_agent-*`
- **X-Ray Tracing:** Enabled for distributed tracing
- **CloudTrail:** Captures all Cognito API calls
- **Cognito Logs:** User pool activity logging

## Future Enhancements

1. **Password Input Masking:** Hide password in chat UI
2. **Multi-language Support:** Localized prompts and messages
3. **Custom Email Templates:** Branded verification emails
4. **Admin Dashboard:** Monitor reset activity
5. **Rate Limiting at Edge:** CloudFront WAF rules
