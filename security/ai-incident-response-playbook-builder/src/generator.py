"""Bedrock-powered playbook generation — threat assessment and playbook creation."""

import argparse
import json
import os

import boto3

THREAT_ASSESSMENT_PROMPT = """You are an AWS security incident response expert. Analyze the following AWS architecture profile and identify the most likely and highest-impact threat scenarios for this specific environment.

For each threat scenario, provide:
- threat_name: Short name (e.g., "Compromised IAM Credentials")
- description: One-sentence description
- likelihood: HIGH, MEDIUM, or LOW based on the architecture
- severity: CRITICAL, HIGH, MEDIUM, or LOW
- mitre_attack_techniques: List of MITRE ATT&CK technique IDs (e.g., ["T1078", "T1528"])
- affected_resources: List of specific resource names/IDs from the architecture that are at risk
- rationale: Why this threat is relevant to THIS architecture

Return ONLY valid JSON — an array of threat scenario objects. Generate 6-12 scenarios based on the architecture complexity.

Architecture Profile:
{profile}"""

PLAYBOOK_PROMPT = """You are an AWS security incident response expert. Generate a detailed incident response playbook for the following threat scenario, tailored to the specific AWS architecture discovered.

Requirements:
- Reference actual resource names, IDs, and ARNs from the architecture profile
- Include specific AWS CLI commands for each step
- Map all procedures to MITRE ATT&CK techniques
- Include detection indicators (CloudTrail events, GuardDuty finding types)
- Structure: Detection → Containment → Eradication → Recovery → Post-Incident

Threat Scenario:
{threat}

Architecture Profile:
{profile}

{org_context_section}

Return the playbook as markdown with the following structure:

# Incident Response Playbook: [Threat Name]

## MITRE ATT&CK Mapping
- [Technique ID] — [Technique Name]

## Severity: [CRITICAL/HIGH/MEDIUM/LOW]
## Affected Resources
- [List specific resources from architecture]

## Detection Indicators
### CloudTrail Events
- [Specific API calls to monitor]
### GuardDuty Finding Types
- [Relevant finding types]
### CloudWatch Metrics/Alarms
- [Relevant metrics]

## Phase 1: Containment
### Step 1.1: [Action]
[Description]
```bash
[AWS CLI command]
```

## Phase 2: Eradication
### Step 2.1: [Action]
...

## Phase 3: Recovery
### Step 3.1: [Action]
...

## Phase 4: Post-Incident Review
### Checklist
- [ ] [Item]

## Communication Template
[Brief notification template for stakeholders]
"""

SSM_PROMPT = """You are an AWS Systems Manager automation expert. Convert the following incident response playbook into an SSM Automation document (JSON format, schemaVersion 0.3).

Requirements:
- Include an approveBeforeExecution step before any destructive action
- Use parameterized resource identifiers (not hardcoded ARNs)
- Include proper error handling with onFailure steps
- Add a description referencing the MITRE ATT&CK techniques

Playbook:
{playbook}

Architecture context:
{profile_summary}

Return ONLY valid JSON for the SSM Automation document."""


def invoke_bedrock(client, model_id, prompt, max_tokens=4096):
    """Invoke Bedrock with the given prompt and return the response text."""
    response = client.invoke_model(
        modelId=model_id,
        contentType="application/json",
        accept="application/json",
        body=json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": max_tokens,
            "temperature": 0.2,
            "messages": [{"role": "user", "content": prompt}],
        }),
    )
    result = json.loads(response["body"].read())
    return result["content"][0]["text"]


def assess_threats(bedrock, model_id, profile):
    """Phase 1: Identify threat scenarios from architecture profile."""
    prompt = THREAT_ASSESSMENT_PROMPT.format(profile=json.dumps(profile, indent=2, default=str))
    response_text = invoke_bedrock(bedrock, model_id, prompt)

    # Extract JSON array from response
    text = response_text.strip()
    if "```" in text:
        start = text.find("```")
        first_newline = text.find("\n", start)
        end = text.rfind("```")
        if first_newline != -1 and end > first_newline:
            text = text[first_newline + 1:end].strip()
    # Find array boundaries
    first_bracket = text.find("[")
    last_bracket = text.rfind("]")
    if first_bracket != -1 and last_bracket > first_bracket:
        text = text[first_bracket:last_bracket + 1]

    return json.loads(text)


def generate_playbook(bedrock, model_id, threat, profile, org_context=None):
    """Phase 2: Generate a single playbook for a threat scenario."""
    org_section = ""
    if org_context:
        org_section = f"Organization Context (embed these details into the playbook):\n{json.dumps(org_context, indent=2)}"

    prompt = PLAYBOOK_PROMPT.format(
        threat=json.dumps(threat, indent=2),
        profile=json.dumps(profile, indent=2, default=str),
        org_context_section=org_section,
    )
    return invoke_bedrock(bedrock, model_id, prompt)


def extract_json(text):
    """Robustly extract JSON from Bedrock response that may contain markdown or extra text."""
    text = text.strip()
    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Strip markdown code fences
    if "```" in text:
        # Find content between first ``` and last ```
        start = text.find("```")
        first_newline = text.find("\n", start)
        end = text.rfind("```")
        if first_newline != -1 and end > first_newline:
            text = text[first_newline + 1:end].strip()
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                pass
    # Find first { and last } to extract JSON object
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace > first_brace:
        try:
            return json.loads(text[first_brace:last_brace + 1])
        except json.JSONDecodeError:
            pass
    raise json.JSONDecodeError("No valid JSON found in response", text, 0)


def generate_ssm_document(bedrock, model_id, playbook_md, profile, max_retries=2):
    """Generate an SSM Automation document from a playbook with retry logic."""
    summary = {
        "account_id": profile.get("account_id"),
        "region": profile.get("region"),
        "risk_indicators": profile.get("risk_indicators"),
    }
    prompt = SSM_PROMPT.format(
        playbook=playbook_md[:3000],
        profile_summary=json.dumps(summary, indent=2, default=str),
    )
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            response_text = invoke_bedrock(bedrock, model_id, prompt)
            return extract_json(response_text)
        except (json.JSONDecodeError, KeyError) as e:
            last_error = e
            if attempt < max_retries:
                print(f"    ⚠ SSM JSON parse failed (attempt {attempt + 1}/{max_retries + 1}), retrying...")
    raise last_error


def slugify(name):
    """Convert a threat name to a filename-safe slug."""
    return name.lower().replace(" ", "-").replace("/", "-").replace(":", "")


def main():
    parser = argparse.ArgumentParser(description="Generate IR playbooks via Amazon Bedrock")
    parser.add_argument("--profile", required=True, help="Path to architecture profile JSON")
    parser.add_argument("--model-id", default="anthropic.claude-3-5-sonnet-20241022-v2:0")
    parser.add_argument("--region", required=True)
    parser.add_argument("--output-dir", default="./output")
    parser.add_argument("--output-format", default="both", choices=["ssm", "markdown", "both"])
    parser.add_argument("--org-context", default=None, help="Path to org context JSON")
    args = parser.parse_args()

    with open(args.profile) as f:
        profile = json.load(f)

    org_context = None
    if args.org_context:
        with open(args.org_context) as f:
            org_context = json.load(f)

    bedrock = boto3.client("bedrock-runtime", region_name=args.region)

    # Phase 1: Threat assessment
    print("  Assessing threats...")
    threats = assess_threats(bedrock, args.model_id, profile)
    print(f"  Identified {len(threats)} threat scenarios")

    # Save threat assessment
    reports_dir = os.path.join(args.output_dir, "reports")
    os.makedirs(reports_dir, exist_ok=True)
    with open(os.path.join(reports_dir, "threat-assessment.json"), "w") as f:
        json.dump(threats, f, indent=2)

    # Phase 2: Generate playbooks
    playbooks_dir = os.path.join(args.output_dir, "playbooks")
    ssm_dir = os.path.join(args.output_dir, "ssm-documents")
    os.makedirs(playbooks_dir, exist_ok=True)
    os.makedirs(ssm_dir, exist_ok=True)

    for i, threat in enumerate(threats, 1):
        name = threat.get("threat_name", f"threat-{i}")
        slug = slugify(name)
        print(f"  [{i}/{len(threats)}] Generating: {name}")

        # Generate markdown playbook
        if args.output_format in ("markdown", "both"):
            playbook_md = generate_playbook(bedrock, args.model_id, threat, profile, org_context)
            with open(os.path.join(playbooks_dir, f"{slug}.md"), "w") as f:
                f.write(playbook_md)

        # Generate SSM document
        if args.output_format in ("ssm", "both"):
            try:
                playbook_text = playbook_md if args.output_format == "both" else generate_playbook(
                    bedrock, args.model_id, threat, profile, org_context
                )
                ssm_doc = generate_ssm_document(bedrock, args.model_id, playbook_text, profile)
                with open(os.path.join(ssm_dir, f"{slug}.json"), "w") as f:
                    json.dump(ssm_doc, f, indent=2)
            except (json.JSONDecodeError, KeyError, Exception) as e:
                print(f"    ⚠ SSM document generation failed for {name}: {e}")

    print(f"  Generated {len(threats)} playbooks")


if __name__ == "__main__":
    main()
