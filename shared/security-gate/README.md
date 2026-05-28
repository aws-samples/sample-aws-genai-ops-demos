# Threat Model Security Gate

Automated threat model generation and gating for PRs to this repository.

## How It Works

1. On PR to `main`, the CI runs the [Threat Modeling MCP Server](https://github.com/awslabs/threat-modeling-mcp-server) via `kiro-cli`
2. The MCP server generates a STRIDE threat model in `.threatmodel/`
3. `gate_check.py` parses the output and returns a verdict:
   - **PASS** (exit 0) — all threats have linked mitigations
   - **WARN** (exit 0) — 1-2 unmitigated threats, review recommended
   - **BLOCK** (exit 1) — 3+ unmitigated threats, PR blocked

## Usage

### Manual (local)

```bash
# Generate threat model
kiro-cli chat --agent threat-modeler --no-interactive \
  --trust-tools="@threat-modeling-mcp-server/*" \
  "Threat model this project using the threat modeling MCP Server"

# Run gate check
python shared/security-gate/gate_check.py .threatmodel/
```

### CI Integration

```yaml
- name: Threat Model Security Gate
  run: |
    kiro-cli chat --agent threat-modeler --no-interactive \
      --trust-tools="@threat-modeling-mcp-server/*" \
      "Threat model this project using the threat modeling MCP Server"
    python shared/security-gate/gate_check.py .threatmodel/
```

## Thresholds

| Unmitigated Threats | Verdict | Exit Code |
|---------------------|---------|-----------|
| 0                   | PASS    | 0         |
| 1-2                 | WARN    | 0         |
| 3+                  | BLOCK   | 1         |

## Supported Formats

- **Threat Composer JSON** — uses `mitigationLinks` to determine coverage
- **Flat JSON** — uses `severity` + `mitigation_status` fields per threat
