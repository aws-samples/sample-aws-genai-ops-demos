# Password Reset Chatbot - Requirements

## Overview

A publicly accessible chatbot that guides users through Cognito's native password reset flow. GenAI handles conversational UX while Cognito enforces all security-critical operations.

## Stakeholders

- **End Users**: Employees who forgot their password and need self-service recovery
- **Security Team**: Ensures no credentials are exposed to GenAI layer
- **Operations Team**: Monitors and audits password reset activities

## Glossary

| Term | Definition |
|------|------------|
| ForgotPassword | Cognito API that initiates password reset and sends verification code |
| ConfirmForgotPassword | Cognito API that completes reset with code + new password |
| Anonymous Access | Chat without authentication (no JWT required) |
| Intent Detection | GenAI identifying user's goal from natural language |

---

## Functional Requirements

### FR-1: Intent Detection

**EARS Notation**: When the user sends a message, the chatbot shall detect password-reset intent from natural language variations (e.g., "I forgot my password", "can't log in", "reset my credentials", "locked out").

**Acceptance Criteria**:
- Recognizes at least 10 common password reset phrasings
- Responds within 3 seconds of message receipt
- Gracefully handles unrelated queries with redirection

### FR-2: User Identification

**EARS Notation**: When password-reset intent is detected, the chatbot shall collect a user identifier (email or username) before initiating the reset flow.

**Acceptance Criteria**:
- Prompts user for email/username if not provided
- Validates email format before proceeding
- Does not reveal whether the user exists in the system (security)

### FR-3: Initiate Password Reset

**EARS Notation**: When a valid user identifier is provided, the chatbot shall call Cognito's ForgotPassword API to send a verification code to the user's registered recovery channel.

**Acceptance Criteria**:
- Calls `cognito-idp:ForgotPassword` with username
- Informs user that a code was sent (without revealing delivery method details)
- Handles rate limiting gracefully with user-friendly message

### FR-4: Guide Verification Code Entry

**EARS Notation**: After initiating password reset, the chatbot shall guide the user to enter the verification code received via email/SMS.

**Acceptance Criteria**:
- Prompts user to check email/SMS for code
- Accepts 6-digit verification code input
- Allows retry if code entry fails (up to 3 attempts)
- Does NOT validate the code itself (Cognito does this)

### FR-5: Guide New Password Entry

**EARS Notation**: After verification code is provided, the chatbot shall guide the user to enter a new password that meets Cognito's password policy.

**Acceptance Criteria**:
- Explains password requirements (min 8 chars, uppercase, lowercase, digit)
- Accepts new password input
- Passes password to Cognito's ConfirmForgotPassword API
- Never logs, stores, or displays the password

### FR-6: Complete Password Reset

**EARS Notation**: When verification code and new password are provided, the chatbot shall call Cognito's ConfirmForgotPassword API to complete the reset.

**Acceptance Criteria**:
- Calls `cognito-idp:ConfirmForgotPassword` with username, code, and new password
- Confirms success to user with clear message
- Handles invalid code/password errors with actionable guidance

### FR-7: Error Handling and Retry

**EARS Notation**: When any step fails, the chatbot shall provide clear error messages and offer retry options.

**Acceptance Criteria**:
- Invalid code: "The code you entered is incorrect. Please try again."
- Expired code: "Your code has expired. Would you like me to send a new one?"
- Password policy violation: Explains which requirement wasn't met
- Rate limiting: "Too many attempts. Please wait X minutes."

### FR-8: Conversation State Management

**EARS Notation**: The chatbot shall maintain conversation state across the multi-step password reset flow within a single session.

**Acceptance Criteria**:
- Tracks current step (intent → identify → code sent → code entered → password set)
- Allows user to restart flow at any point
- Session state does not persist passwords or codes

---

## Non-Functional Requirements

### NFR-1: Anonymous Access

**EARS Notation**: The chat interface shall be accessible without authentication (no Cognito login required to chat).

**Acceptance Criteria**:
- AgentCore Runtime configured without JWT authorizer
- Frontend loads and allows chat without sign-in
- No authentication tokens required for agent invocation

### NFR-2: Security Boundaries

**EARS Notation**: The GenAI agent shall never generate, receive, store, or validate passwords or MFA codes.

**Acceptance Criteria**:
- Passwords passed directly to Cognito API, not processed by agent logic
- Verification codes passed directly to Cognito API, not validated by agent
- No credentials in CloudWatch logs (use Cognito's built-in logging)
- Agent role has minimal Cognito permissions (ForgotPassword, ConfirmForgotPassword only)

### NFR-3: Least Privilege IAM

**EARS Notation**: The agent execution role shall have only the minimum permissions required.

**Acceptance Criteria**:
- `cognito-idp:ForgotPassword` - scoped to specific User Pool
- `cognito-idp:ConfirmForgotPassword` - scoped to specific User Pool
- No `AdminSetUserPassword` or other admin-level permissions
- No access to user attributes beyond what's needed

### NFR-4: Auditability

**EARS Notation**: All password reset attempts shall be auditable via AWS native services.

**Acceptance Criteria**:
- Cognito logs all ForgotPassword/ConfirmForgotPassword calls
- CloudTrail captures API calls
- Agent logs conversation flow (without sensitive data)

### NFR-5: Rate Limiting

**EARS Notation**: The system shall respect Cognito's built-in rate limiting and throttling.

**Acceptance Criteria**:
- Agent gracefully handles `LimitExceededException`
- User receives friendly message about waiting
- No bypass of Cognito's security controls

---

## Responsibility Matrix

| Responsibility | Owner | Notes |
|---------------|-------|-------|
| Intent detection | GenAI Agent | Natural language understanding |
| User identification collection | GenAI Agent | Conversational prompt |
| Password policy explanation | GenAI Agent | User-friendly guidance |
| Verification code delivery | Cognito | Email/SMS via configured channels |
| Code validation | Cognito | Built-in security |
| Password validation | Cognito | Policy enforcement |
| Password storage | Cognito | Secure credential management |
| Rate limiting | Cognito | Built-in throttling |
| Audit logging | Cognito + CloudTrail | Native AWS services |

---

## Out of Scope

- Custom authentication logic
- Direct password handling by GenAI
- Admin-initiated password resets
- MFA code generation or validation by GenAI
- Production hardening beyond demo scope
- Multi-tenant user pool support
