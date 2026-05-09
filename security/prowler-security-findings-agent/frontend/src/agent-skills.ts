/**
 * Curated DevOps Agent Skills that make this demo more opinionated.
 *
 * Amazon DevOps Agent does not (yet) expose a public API for creating Skills
 * programmatically, so we can't seed them at deploy time. Each skill is a
 * ready-to-paste payload that matches the fields of the Agent's "Create skill"
 * form: Name, Description, Agent Type, and Instructions. The dashboard ships
 * Copy buttons for every field so the TAM can walk through the form once.
 *
 * Two seeded skills for this demo:
 *   1. "AWS Security Remediator" — turns agent output into step-by-step AWS
 *      CLI + CDK remediation instructions aligned with the Nova playbook.
 *   2. "Compliance Framework Translator" — when a finding maps to multiple
 *      frameworks, the agent explains the auditor impact per framework
 *      instead of dumping raw control IDs.
 */

/** Mirrors the "Agent Type" dropdown in the Create skill form. */
export type DevOpsAgentType =
  | 'Generic'
  | 'On-demand'
  | 'Incident triage'
  | 'Incident RCA'
  | 'Incident mitigation'
  | 'Evaluation';

export interface AgentSkill {
  /** Stable id used by React; not submitted to the form. */
  id: string;
  /** Card header in the dashboard. */
  title: string;
  /** One-line pitch shown under the header. */
  summary: string;
  /** Form field: Name — lowercase letters, numbers, hyphens (max 64 chars). */
  name: string;
  /** Form field: Description / "when to use" — min 100 characters recommended. */
  description: string;
  /** Form field: Agent Type — which agent sub-type can use this skill. */
  agentType: DevOpsAgentType;
  /** Form field: Instructions (markdown body). */
  instructions: string;
}

export const AGENT_SKILLS: AgentSkill[] = [
  {
    id: 'aws-security-remediator',
    title: 'AWS Security Remediator',
    summary:
      'Forces the Agent to return concrete AWS CLI + CDK v2 remediation steps for every finding it investigates.',
    name: 'aws-security-remediator',
    agentType: 'Incident mitigation',
    description:
      'Apply this skill when the Agent is investigating an AWS security finding from Prowler (IAM, S3, Secrets Manager, Security Group, KMS, CloudTrail, GuardDuty, Security Hub). It forces the output into a three-section Impact / Root cause / Remediation block with executable AWS CLI and AWS CDK v2 snippets, so the engineer can copy remediations directly into a terminal or IaC repo without rewriting anything.',
    instructions: `## Output format
Every investigation MUST end with a three-section block:

1. **Impact** — the concrete exposure (what an attacker can do today).
2. **Root cause** — the exact resource attribute that made it possible.
3. **Remediation** — two fenced code blocks:
   - \`\`\`bash\` — AWS CLI command(s) to fix it immediately.
   - \`\`\`typescript\` — AWS CDK v2 snippet to encode the fix as IaC.

Use placeholders like \`<account-id>\`, \`<resource-name>\` instead of inventing ARNs.

## Tone
Direct. No "consider" / "might want to". Prefix every step with a verb
("Disable", "Rotate", "Add", "Remove"). Keep the whole response under
600 words.

## Defaults
- Reject any suggestion that widens IAM scope. Narrow or keep the same.
- Prefer AWS-managed keys when the finding is about encryption.
- Never recommend disabling CloudTrail / GuardDuty / Security Hub.`,
  },
  {
    id: 'compliance-framework-translator',
    title: 'Compliance Framework Translator',
    summary:
      'When a finding maps to multiple frameworks, the Agent explains what fails in business-language for each of them instead of dumping IDs.',
    name: 'compliance-framework-translator',
    agentType: 'Incident RCA',
    description:
      'Apply this skill when the investigation payload includes a Prowler finding with compliance.framework entries (for example CIS, PCI DSS, HIPAA, NIST 800-53, SOC 2, GDPR, ENS, CSA CCM, NIST CSF, MITRE ATT&CK). The agent should translate every framework that is actually present in the payload into a short business-language paragraph that names the failing control and the auditor impact, instead of just listing control IDs.',
    instructions: `## Output

For every framework in the finding's compliance map, write ONE paragraph
(3–5 sentences) with:

- Which specific control is failing (cite the control ID verbatim).
- What the control is trying to achieve in the user's domain
  (not a literal re-statement of the control text).
- What the auditor would flag in a review today.

Skip frameworks that are not actually present in the finding payload.
If the finding has zero frameworks, say so in one sentence and stop.

## Hard rules

- Never invent control IDs. If unsure, omit the framework.
- Never claim PCI / HIPAA / GDPR coverage that isn't in the payload.
- Priority order when writing: PCI > HIPAA > NIST-800-53 > CIS > others.
- Keep the whole response under 500 words.`,
  },
];
