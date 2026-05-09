/**
 * Curated DevOps Agent Skills that make this demo more opinionated.
 *
 * Amazon DevOps Agent does not (yet) expose a public API for creating Skills
 * programmatically, so we can't seed them at deploy time. Instead we ship
 * each skill as a ready-to-paste markdown/JSON blob and offer a Copy button
 * in the dashboard Lab area — same pattern the peer EKS demo uses.
 *
 * Each skill has:
 *   - `title`:   shown as the card header
 *   - `summary`: one-line pitch the TAM can read aloud
 *   - `when`:    the "When" field in the Agent Skills creation form
 *   - `rules`:   the "Rules" multi-line field (markdown-ish)
 *
 * Two seeded skills for this demo:
 *   1. "AWS Security Remediator" — turns agent output into step-by-step
 *      AWS CLI + CDK remediation instructions, aligned with the Nova
 *      remediation playbook format.
 *   2. "Compliance Framework Translator" — when a finding maps to
 *      multiple frameworks, the agent explains what the failing control
 *      means for each framework instead of dumping raw IDs.
 */

export interface AgentSkill {
  id: string;
  title: string;
  summary: string;
  when: string;
  rules: string;
}

export const AGENT_SKILLS: AgentSkill[] = [
  {
    id: 'aws-security-remediator',
    title: 'AWS Security Remediator',
    summary:
      'Forces the Agent to return concrete AWS CLI + CDK v2 remediation steps for every finding it investigates.',
    when:
      'The investigation relates to an AWS security finding (IAM, S3, Secrets Manager, Security Group, KMS, CloudTrail, GuardDuty, Security Hub).',
    rules: `## Output format
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
    when:
      'The investigation payload references a Prowler finding with compliance.framework entries (CIS, PCI, HIPAA, NIST, SOC 2, GDPR, ENS, CSA-CCM, NIST-CSF, MITRE-ATT&CK).',
    rules: `## Output

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
