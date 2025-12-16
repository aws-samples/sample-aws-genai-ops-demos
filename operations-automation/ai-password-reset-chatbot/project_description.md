### **Prompt to give to Kiro**

You are a senior AWS solutions engineer tasked with extending the GitHub repository
`aws-samples/sample-amazon-bedrock-agentcore-fullstack-webapp` to build a **password-reset chatbot demo**.

#### **Objective**

Transform the existing sample into a demonstration where:

* The **chat interface is publicly accessible (anonymous users)**
* **Amazon Cognito is used only as the Identity Provider (user database)**
* The chatbot can safely initiate and guide a **Cognito-managed password reset**
* No passwords or credentials are ever handled by the GenAI layer

This is a **demo architecture**, not a production IAM replacement.

---

#### **Functional Requirements**

* The chatbot must detect natural-language password-reset intent (e.g., “I forgot my password”).
* The chatbot must collect a user identifier (username or email).
* The system must initiate **Cognito’s native Forgot Password flow**.
* Cognito must perform identity verification using configured recovery channels (email/SMS/MFA).
* The chatbot must guide the user through verification and completion.
* The final password change must be performed **exclusively by Cognito**.
* The new password must be valid for subsequent logins.
* The chatbot must support retry, explanation, and graceful failure handling.
* High-risk or blocked scenarios must be escalated (demo-level).

---

#### **Non-Functional / Security Requirements**

* Chat access must be **anonymous** (no Cognito authentication for chat).
* The GenAI agent must never:

  * generate passwords
  * receive or store passwords
  * validate MFA codes
* All password policies, throttling, and verification must remain in Cognito.
* Admin-level password reset must be excluded or explicitly gated.
* All flows must be auditable (CloudWatch / CloudTrail awareness).
* Follow least-privilege IAM principles.

---

#### **Architecture Constraints**

* Reuse the existing **samples\sample-amazon-bedrock-agentcore-fullstack-webapp** project to start with from the sample.
* Modify as less as you can the sample to achieve our goal.
* If any extra layer would be needed verify this with me.
* Move any stacks you think could be reused "as is" in other AgentCore Runtime based scenario.
* Use the shared folder existing stacks instead of the stacks you have already present in the sample
* Modify the frontend to allow **unauthenticated chat access**.
* Cognito User Pool remains the single source of truth for credentials.
* GenAI acts as:

  * intent classifier
  * dialog manager
  * policy-aware orchestrator
  * user-facing explainer

---

#### **Deliverables**

Produce the following artifacts:

1. `requirements.md`

   * Use EARS notation
   * Clearly distinguish chatbot responsibilities vs Cognito responsibilities

2. `design.md`

   * High-level architecture diagram (textual)
   * Sequence diagram for:

     * anonymous user → chatbot → Cognito Forgot Password
   * Security boundaries and trust zones
   * Failure and escalation paths

3. `tasks.md`

   * Discrete implementation tasks
   * Dependencies between tasks
   * Clear demo outcomes for each task

4. Minimal changes needed to the existing repository structure

   * Identify which components are reused
   * Identify which components are modified or added

---

#### **Explicit Out-of-Scope**

* No custom authentication logic
* No direct password handling
* No replacement of Cognito security controls
* No production hardening beyond demo scope

---

#### **Guiding Principle**

GenAI must **absorb human ambiguity**, while **Cognito enforces deterministic identity security**.

---

If you want, next I can:

* tailor this prompt specifically to **Kiro’s Spec syntax expectations**, or
* produce a **shorter “quick-start” version** of the prompt for rapid iteration.
