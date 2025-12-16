# Password Reset Chatbot - Implementation Tasks

## Task Overview

| ID | Task | Effort | Dependencies | Demo Outcome |
|----|------|--------|--------------|--------------|
| T1 | Copy sample project | 15 min | None | Project structure ready |
| T2 | Add Cognito permissions to agent role | 30 min | T1 | IAM configured |
| T3 | Create password reset tools | 1 hour | T1 | Tools callable |
| T4 | Update agent with new tools and prompt | 30 min | T3 | Agent responds to reset intent |
| T5 | Remove JWT authorizer from runtime | 15 min | T1 | Anonymous access enabled |
| T6 | Update frontend for anonymous access | 45 min | T5 | Chat works without login |
| T7 | Update deployment scripts | 30 min | T1 | One-command deploy |
| T8 | End-to-end testing | 1 hour | All | Full flow works |
| T9 | Documentation | 30 min | T8 | README complete |

**Total Estimated Effort**: ~5 hours

---

## Task Details

### T1: Copy Sample Project

**Objective**: Create the password-reset-chatbot demo from the existing sample

**Steps**:
1. Copy `samples/sample-amazon-bedrock-agentcore-fullstack-webapp/` to `operations-automation/ai-password-reset-chatbot/`
2. Rename/update stack names to avoid conflicts (e.g., `PasswordResetInfra`, `PasswordResetAuth`, etc.)
3. Update `cdk/bin/app.ts` with new stack names
4. Verify project structure is correct

**Files to Create/Modify**:
- `operations-automation/ai-password-reset-chatbot/` (entire folder)
- `cdk/bin/app.ts` - update stack names

**Demo Outcome**: Project compiles and can be deployed (with original functionality)

---

### T2: Add Cognito Permissions to Agent Role

**Objective**: Enable agent to call Cognito ForgotPassword/ConfirmForgotPassword APIs

**Steps**:
1. Open `cdk/lib/infra-stack.ts`
2. Add IAM policy statement for Cognito password reset operations
3. Scope permissions to the User Pool ARN (imported from auth stack)

**Code Change**:
```typescript
// In infra-stack.ts, add to agentRole:
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

**Demo Outcome**: Agent role has permissions to initiate password resets

---

### T3: Create Password Reset Tools

**Objective**: Implement Strands tools that wrap Cognito APIs

**Steps**:
1. Create `agent/password_reset_tools.py`
2. Implement `initiate_password_reset` tool
3. Implement `complete_password_reset` tool
4. Add boto3 to `agent/requirements.txt` (if not present)
5. Handle all Cognito exceptions gracefully

**Files to Create**:
- `agent/password_reset_tools.py`

**Files to Modify**:
- `agent/requirements.txt` - add boto3

**Tool Signatures**:
```python
@tool
def initiate_password_reset(username: str) -> str:
    """Initiate password reset - sends verification code to user's email"""
    
@tool  
def complete_password_reset(username: str, code: str, new_password: str) -> str:
    """Complete password reset with verification code and new password"""
```

**Demo Outcome**: Tools can be tested locally with `python -c "from password_reset_tools import ..."`

---

### T4: Update Agent with New Tools and Prompt

**Objective**: Configure agent for password reset conversations

**Steps**:
1. Open `agent/strands_agent.py`
2. Remove calculator and weather tools
3. Import password reset tools
4. Update system prompt for password reset flow
5. Update model to use Nova Pro (per steering guidelines)

**Code Changes**:
```python
from password_reset_tools import initiate_password_reset, complete_password_reset

model_id = "amazon.nova-pro-v1:0"  # Updated per guidelines

agent = Agent(
    model=model,
    tools=[initiate_password_reset, complete_password_reset],
    system_prompt=PASSWORD_RESET_SYSTEM_PROMPT,
    callback_handler=None
)
```

**Demo Outcome**: Agent detects password reset intent and uses correct tools

---

### T5: Remove JWT Authorizer from Runtime

**Objective**: Enable anonymous access to AgentCore Runtime

**Steps**:
1. Open `cdk/lib/runtime-stack.ts`
2. Remove `authorizerConfiguration` block from `CfnRuntime`
3. Remove `userPool` and `userPoolClient` from stack props (no longer needed for runtime)
4. Update `cdk/bin/app.ts` to not pass auth props to runtime stack

**Code Change**:
```typescript
// REMOVE this entire block from CfnRuntime:
// authorizerConfiguration: {
//   customJwtAuthorizer: {
//     discoveryUrl: discoveryUrl,
//     allowedClients: [props.userPoolClient.userPoolClientId],
//   },
// },
```

**Demo Outcome**: AgentCore accepts requests without JWT token

---

### T6: Update Frontend for Anonymous Access

**Objective**: Allow chat without sign-in

**Steps**:
1. Update `frontend/src/App.tsx`:
   - Remove auth checking logic
   - Remove sign-in button from TopNavigation
   - Update welcome message for password reset context
   - Remove auth modal import
2. Update `frontend/src/agentcore.ts`:
   - Remove JWT token retrieval
   - Remove Authorization header
   - Keep session ID for conversation tracking
3. Update support prompts for password reset context

**Files to Modify**:
- `frontend/src/App.tsx`
- `frontend/src/agentcore.ts`

**Demo Outcome**: User can chat immediately without signing in

---

### T7: Update Deployment Scripts

**Objective**: Ensure one-command deployment works

**Steps**:
1. Update `deploy-all.ps1` and `deploy-all.sh`:
   - Update stack names
   - Remove auth config injection to frontend (no longer needed)
   - Keep User Pool ID injection (needed for agent tools)
2. Update `scripts/build-frontend.ps1` and `scripts/build-frontend.sh`:
   - Remove Cognito config parameters
   - Add User Pool ID as environment variable for agent
3. Add User Pool ID to agent environment variables in runtime stack

**Files to Modify**:
- `deploy-all.ps1` / `deploy-all.sh`
- `scripts/build-frontend.ps1` / `scripts/build-frontend.sh`
- `cdk/lib/runtime-stack.ts` (add USER_POOL_ID env var)

**Demo Outcome**: `.\deploy-all.ps1` deploys everything correctly

---

### T8: End-to-End Testing

**Objective**: Verify complete password reset flow

**Test Scenarios**:

1. **Happy Path**:
   - Open CloudFront URL (no login required)
   - Type "I forgot my password"
   - Enter email address
   - Receive verification code via email
   - Enter code in chat
   - Enter new password
   - Verify success message
   - Log in with new password (via original sample or AWS Console)

2. **Invalid Email**:
   - Enter non-existent email
   - Verify generic "code sent" message (no user enumeration)

3. **Invalid Code**:
   - Enter wrong verification code
   - Verify helpful error message

4. **Weak Password**:
   - Enter password that doesn't meet policy
   - Verify policy explanation

5. **Rate Limiting**:
   - Trigger multiple reset attempts
   - Verify rate limit message

**Demo Outcome**: All scenarios work as expected

---

### T9: Documentation

**Objective**: Create README and architecture docs

**Steps**:
1. Create `README.md` with:
   - Architecture overview
   - Prerequisites
   - Deployment instructions
   - Testing instructions
   - Cost estimates
   - Troubleshooting
2. Create `ARCHITECTURE.md` with:
   - Component diagram
   - Security boundaries
   - Data flow

**Files to Create**:
- `operations-automation/ai-password-reset-chatbot/README.md`
- `operations-automation/ai-password-reset-chatbot/ARCHITECTURE.md`

**Demo Outcome**: Engineers can deploy and understand the demo

---

## Implementation Order

```
T1 (Copy project)
    │
    ├──▶ T2 (Cognito permissions)
    │
    ├──▶ T3 (Password reset tools)
    │         │
    │         └──▶ T4 (Update agent)
    │
    ├──▶ T5 (Remove JWT auth)
    │         │
    │         └──▶ T6 (Update frontend)
    │
    └──▶ T7 (Deployment scripts)
              │
              └──▶ T8 (E2E testing)
                        │
                        └──▶ T9 (Documentation)
```

---

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| AgentCore without auth is fully public | Demo only; document security implications |
| Cognito rate limits may be hit during testing | Use different test emails; wait between attempts |
| User Pool ID needed at runtime | Pass via environment variable to agent container |
| Password visible in chat UI | Frontend should mask password input (enhancement) |

---

## Future Enhancements (Out of Scope)

- Password input masking in chat UI
- Multi-language support
- Custom email templates
- Admin dashboard for reset monitoring
- Integration with enterprise IdP (SAML/OIDC)
