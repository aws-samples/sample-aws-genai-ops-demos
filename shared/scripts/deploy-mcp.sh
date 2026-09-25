#!/bin/bash
# GenAI Ops Demo Library - Shared Agent Tools MCP Server Runner (Bash)
#
# Deploys ONE MCP server from the public Agent Tools repository
# (aws/tools-for-devops-agent) into the current AWS account, driven entirely by the
# server's `mcp-server.yaml` manifest (MCP Server Integration Contract v1).
#
# Design rule: one artefact, one home. The server's source stays in the Agent Tools
# repository; this script fetches it into a temp dir at deploy time, runs the deploy
# command the manifest declares, captures the endpoint output the manifest names, and
# reports how to register it with AWS DevOps Agent. Nothing is copied into this repo.
#
# Requires: git, aws, python3 (used only as the YAML reader - PyYAML if present,
# otherwise a built-in reader for the manifest subset). Mirrors deploy-mcp.ps1.
#
# Usage (from a demo directory):
#   ../../shared/scripts/deploy-mcp.sh --server aws-vpc-dns-diagnostics-mcp --ref main \
#       --param AllowedAccounts=111111111111
#   ../../shared/scripts/deploy-mcp.sh --server aws-vpc-dns-diagnostics-mcp --destroy --manifest ...
#
# Exports (when sourced): AGENT_TOOLS_MCP_ENDPOINT, AGENT_TOOLS_MCP_AUTH_METHOD,
#                         AGENT_TOOLS_MCP_SIGNING_SERVICE, AGENT_TOOLS_MCP_STACK

set -e

SERVER=""
REF="main"
REPO="https://github.com/aws/tools-for-devops-agent"
MANIFEST=""
DESTROY=false
KEEP_SOURCE=false
declare -A PARAMS

while [[ $# -gt 0 ]]; do
    case $1 in
        --server) SERVER="$2"; shift 2 ;;
        --ref) REF="$2"; shift 2 ;;
        --repo) REPO="$2"; shift 2 ;;
        --manifest) MANIFEST="$2"; shift 2 ;;
        --param) PARAMS["${2%%=*}"]="${2#*=}"; shift 2 ;;
        --destroy) DESTROY=true; shift ;;
        --keep-source) KEEP_SOURCE=true; shift ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; GRAY='\033[0;90m'; NC='\033[0m'

if [ -z "$SERVER" ]; then echo -e "${RED}ERROR: --server <name> is required${NC}"; exit 1; fi
if ! [[ "$SERVER" =~ ^[a-z0-9][a-z0-9-]{0,63}$ ]]; then
    echo -e "${RED}ERROR: Invalid server name '$SERVER' (lowercase letters, digits and hyphens only)${NC}"; exit 1
fi
for tool in git aws python3; do
    if ! command -v $tool >/dev/null 2>&1; then echo -e "${RED}ERROR: $tool is required${NC}"; exit 1; fi
done

# ---------------------------------------------------------------------------
# Manifest reader: python3 turns the YAML subset into flat "path<TAB>value" lines.
# Lists come out as path.0, path.1, ... ; argv lists as path.N.M
# ---------------------------------------------------------------------------
read_manifest() {
python3 - "$1" <<'PY'
import sys, json, re
path = sys.argv[1]
text = open(path, encoding="utf-8").read()
try:
    import yaml
    data = yaml.safe_load(text)
except ImportError:
    data = None

def builtin_reader(text):
    # Built-in reader for the contract's YAML subset (mappings, scalars, flow lists,
    # block lists of scalars or mappings). Keeps consumers dependency-free.
    lines = []
    for raw in text.splitlines():
        line = re.sub(r"\s+#.*$", "", raw)
        if line.strip() == "" or line.lstrip().startswith("#"):
            continue
        lines.append(line.rstrip())
    def scalar(s):
        s = s.strip()
        if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'": return s[1:-1]
        if s == "true": return True
        if s == "false": return False
        if s in ("null", "~", ""): return None
        if re.fullmatch(r"-?\d+", s): return int(s)
        return s
    def flow(s):
        inner = s.strip()[1:-1].strip()
        return [] if inner == "" else [scalar(p) for p in inner.split(",")]
    i = 0
    def indent(l): return len(l) - len(l.lstrip())
    def block(ind):
        nonlocal i
        if i >= len(lines) or indent(lines[i]) != ind: return None
        return lst(ind) if lines[i].lstrip().startswith("- ") else mapping(ind)
    def mapping(ind):
        nonlocal i
        m = {}
        while i < len(lines):
            l = lines[i]; li = indent(l)
            if li < ind or l.lstrip().startswith("- "): break
            if li > ind: raise SystemExit(f"manifest parse error near: {l!r}")
            k, _, rest = l.strip().partition(":")
            i += 1
            rest = rest.strip()
            if rest:
                m[k] = flow(rest) if rest.startswith("[") else scalar(rest)
            elif i < len(lines) and indent(lines[i]) > ind:
                m[k] = block(indent(lines[i]))
            else:
                m[k] = None
        return m
    def lst(ind):
        nonlocal i
        out = []
        while i < len(lines):
            l = lines[i]
            if indent(l) != ind or not l.lstrip().startswith("- "): break
            content = l.lstrip()[2:]
            i += 1
            if re.match(r"^[A-Za-z0-9_.-]+\s*:", content):
                lines[i-1] = " " * (ind + 2) + content
                i -= 1
                out.append(mapping(ind + 2))
            elif content.lstrip().startswith("["):
                out.append(flow(content))
            else:
                out.append(scalar(content))
        return out
    return block(indent(lines[0]))

if data is None:
    data = builtin_reader(text)
def walk(prefix, v):
    if isinstance(v, dict):
        for k, x in v.items(): walk(f"{prefix}.{k}" if prefix else k, x)
    elif isinstance(v, list):
        print(f"{prefix}.__len__\t{len(v)}")
        for n, x in enumerate(v): walk(f"{prefix}.{n}", x)
    else:
        print(f"{prefix}\t{json.dumps(v) if not isinstance(v, str) else v}")
walk("", data)
PY
}

declare -A M
load_manifest() {
    while IFS=$'\t' read -r k v; do M["$k"]="$v"; done < <(read_manifest "$1")
}
mget() { echo "${M[$1]}"; }
mreq() {
    if [ -z "${M[$1]+x}" ]; then echo -e "${RED}ERROR: manifest is missing required field '$1'${NC}"; exit 1; fi
    echo "${M[$1]}"
}
# Build an argv array from list path "$1" into the named array "$2", substituting ${Param}.
argv_from() {
    local path="$1" name="$2" n i s k
    n="${M[$path.__len__]}"
    eval "$name=()"
    for ((i=0; i<n; i++)); do
        s="${M[$path.$i]}"
        while [[ "$s" =~ \$\{([A-Za-z0-9_]+)\} ]]; do
            k="${BASH_REMATCH[1]}"
            if [ -z "${PARAMS[$k]+x}" ]; then
                echo -e "${RED}ERROR: deploy command needs parameter '$k' - pass --param $k=...${NC}"; exit 1
            fi
            s="${s//\$\{$k\}/${PARAMS[$k]}}"
        done
        eval "$name+=(\"\$s\")"
    done
}
run_argv() {   # run_argv <label> <workdir> <args...>
    local label="$1" wd="$2"; shift 2
    echo -e "${GRAY}      > $*${NC}"
    (cd "$wd" && "$@") || { echo -e "${RED}      ERROR: $label failed${NC}"; exit 1; }
}

# ---------------------------------------------------------------------------
# Region / account (same priority as the rest of the repo)
# ---------------------------------------------------------------------------
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text --no-cli-pager 2>/dev/null || echo "FAILED")
if [ "$ACCOUNT_ID" = "FAILED" ]; then echo -e "${RED}ERROR: AWS credentials are not configured or have expired${NC}"; exit 1; fi
CURRENT_REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-$(aws configure get region 2>/dev/null)}}"
if [ -z "$CURRENT_REGION" ]; then echo -e "${RED}ERROR: No AWS region configured${NC}"; exit 1; fi

echo ""
echo -e "${CYAN}=== Agent Tools MCP Server Runner (Shared Script) ===${NC}"
echo -e "${GRAY}      Server:  $SERVER @ $REF${NC}"
echo -e "${GRAY}      Region:  $CURRENT_REGION${NC}"
echo -e "${GRAY}      Account: $ACCOUNT_ID${NC}"
echo -e "${GRAY}      Mode:    $([ "$DESTROY" = true ] && echo destroy || echo deploy)${NC}"

# ---------------------------------------------------------------------------
# Fetch mcp/<server> at the ref
# ---------------------------------------------------------------------------
SPARSE_PATH="mcp/$SERVER"
TEMP_ROOT=$(mktemp -d -t agent-tools-XXXXXXXX)
echo ""
echo -e "${YELLOW}Fetching $SPARSE_PATH @ $REF ...${NC}"
CLONE_RESULT=$(git clone --quiet --depth 1 --filter=blob:none --sparse --branch "$REF" "$REPO" "$TEMP_ROOT" 2>&1 && git -C "$TEMP_ROOT" sparse-checkout set "$SPARSE_PATH" 2>&1 || echo "FAILED")
SOURCE_DIR="$TEMP_ROOT/$SPARSE_PATH"
if [[ "$CLONE_RESULT" == *"FAILED"* ]] || [ ! -d "$SOURCE_DIR" ]; then
    echo -e "${RED}      ERROR: Could not fetch '$SPARSE_PATH' at ref '$REF' from $REPO${NC}"
    rm -rf "$TEMP_ROOT"; exit 1
fi
COMMIT=$(git -C "$TEMP_ROOT" rev-parse --short HEAD)
echo -e "${GREEN}      OK: fetched $SPARSE_PATH @ $REF ($COMMIT)${NC}"

# ---------------------------------------------------------------------------
# Manifest: upstream first, local override second
# ---------------------------------------------------------------------------
MANIFEST_PATH="$SOURCE_DIR/mcp-server.yaml"; MANIFEST_ORIGIN="upstream"
if [ -n "$MANIFEST" ]; then MANIFEST_PATH=$(cd "$(dirname "$MANIFEST")" && pwd)/$(basename "$MANIFEST"); MANIFEST_ORIGIN="local override"; fi
if [ ! -f "$MANIFEST_PATH" ]; then
    echo -e "${RED}      ERROR: No mcp-server.yaml for '$SERVER' at ref '$REF'.${NC}"
    echo -e "${YELLOW}      This server does not ship a manifest yet. Either get one added upstream,${NC}"
    echo -e "${YELLOW}      or pass --manifest <path> to a temporary one in your demo's own folder.${NC}"
    echo -e "${YELLOW}      See shared/agent-tools/README.md for the schema.${NC}"
    rm -rf "$TEMP_ROOT"; exit 1
fi
load_manifest "$MANIFEST_PATH"
echo -e "${GREEN}      OK: manifest ($MANIFEST_ORIGIN) $MANIFEST_PATH${NC}"

[ "$(mreq schemaVersion)" = "1" ] || { echo -e "${RED}      ERROR: manifest schemaVersion $(mget schemaVersion) is not supported (this runner speaks v1)${NC}"; exit 1; }
[ "$(mreq name)" = "$SERVER" ] || { echo -e "${RED}      ERROR: manifest name '$(mget name)' does not match server '$SERVER'${NC}"; exit 1; }
KIND=$(mreq kind)
IAC=$(mget iac); IAC="${IAC:-unknown}"
WORKDIR="$SOURCE_DIR/$(mget deploy.workdir)"; [ -z "$(mget deploy.workdir)" ] && WORKDIR="$SOURCE_DIR"

case "$IAC" in
    sam) TOOL=sam ;; cdk) TOOL=npx ;; cfn) TOOL=aws ;; terraform) TOOL=terraform ;; *) TOOL="" ;;
esac
if [ -n "$TOOL" ] && ! command -v "$TOOL" >/dev/null 2>&1; then
    echo -e "${RED}      ERROR: manifest declares iac '$IAC' but '$TOOL' is not installed${NC}"; exit 1
fi

# Required parameters declared by the manifest must be supplied.
NPARAMS="${M[deploy.parameters.__len__]:-0}"
for ((i=0; i<NPARAMS; i++)); do
    if [ "$(mget deploy.parameters.$i.required)" = "true" ] && [ -z "${PARAMS[$(mget deploy.parameters.$i.name)]+x}" ]; then
        echo -e "${RED}      ERROR: required parameter '$(mget deploy.parameters.$i.name)' not supplied - $(mget deploy.parameters.$i.description)${NC}"
        echo -e "${YELLOW}      Pass: --param $(mget deploy.parameters.$i.name)=...${NC}"; exit 1
    fi
done
MAIN_STACK=$(mget deploy.stacks.0)

# ---------------------------------------------------------------------------
# Destroy
# ---------------------------------------------------------------------------
if [ "$DESTROY" = true ]; then
    [ -n "${M[teardown.command.__len__]+x}" ] || { echo -e "${RED}ERROR: manifest is missing required field 'teardown.command'${NC}"; exit 1; }
    echo ""; echo -e "${YELLOW}Tearing down ($IAC)...${NC}"
    argv_from teardown.command TEARDOWN
    run_argv "teardown" "$WORKDIR" "${TEARDOWN[@]}"
    echo -e "${GREEN}      OK: teardown command completed${NC}"
    NRES="${M[teardown.residuals.__len__]:-0}"
    if [ "$NRES" -gt 0 ]; then
        echo ""; echo -e "${YELLOW}  Not removed by the teardown command - clean up manually:${NC}"
        for ((i=0; i<NRES; i++)); do echo -e "${YELLOW}    - $(mget teardown.residuals.$i)${NC}"; done
    fi
    [ "$KEEP_SOURCE" = true ] || rm -rf "$TEMP_ROOT"
    exit 0
fi

# ---------------------------------------------------------------------------
# Deploy
# ---------------------------------------------------------------------------
echo ""; echo -e "${YELLOW}Deploying ($IAC)...${NC}"
NSETUP="${M[deploy.setup.__len__]:-0}"
for ((i=0; i<NSETUP; i++)); do
    argv_from "deploy.setup.$i" STEP
    run_argv "setup step" "$WORKDIR" "${STEP[@]}"
done
[ -n "${M[deploy.command.__len__]+x}" ] || { echo -e "${RED}ERROR: manifest is missing required field 'deploy.command'${NC}"; exit 1; }
argv_from deploy.command DEPLOY
run_argv "deploy" "$WORKDIR" "${DEPLOY[@]}"
echo -e "${GREEN}      OK: deploy command completed${NC}"

if [ "$KIND" = "aws-hosted-reference" ]; then
    echo ""
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}  Connection deployed: $SERVER @ $REF ($COMMIT)${NC}"
    echo -e "${GREEN}========================================${NC}"
    echo -e "${CYAN}  This server is AWS-hosted; the template registered it with DevOps Agent.${NC}"
    echo -e "${CYAN}  Associate the registered service with your Agent Space to finish.${NC}"
    [ "$KEEP_SOURCE" = true ] || rm -rf "$TEMP_ROOT"
    exit 0
fi

OUTPUT_NAME=$(mreq endpoint.output)
OUTPUT_STACK=$(mget endpoint.stack); OUTPUT_STACK="${OUTPUT_STACK:-$MAIN_STACK}"
[ -n "$OUTPUT_STACK" ] || { echo -e "${RED}      ERROR: manifest names no stack to read '$OUTPUT_NAME' from${NC}"; exit 1; }
echo ""; echo -e "${YELLOW}Reading endpoint output '$OUTPUT_NAME' from stack '$OUTPUT_STACK'...${NC}"
ENDPOINT=$(aws cloudformation describe-stacks --stack-name "$OUTPUT_STACK" --region "$CURRENT_REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='$OUTPUT_NAME'].OutputValue | [0]" --output text --no-cli-pager 2>/dev/null || echo "None")
if [ -z "$ENDPOINT" ] || [ "$ENDPOINT" = "None" ]; then
    echo -e "${RED}      ERROR: output '$OUTPUT_NAME' not found on stack '$OUTPUT_STACK'${NC}"; exit 1
fi
if [ "$(mget endpoint.includesMcpPath)" = "false" ]; then ENDPOINT="${ENDPOINT%/}/mcp"; fi   # exactly /mcp
echo -e "${GREEN}      OK: $ENDPOINT${NC}"

AUTH_METHOD=$(mreq auth.method)
SIGNING_SERVICE=$(mget auth.signingService)
export AGENT_TOOLS_MCP_ENDPOINT="$ENDPOINT" AGENT_TOOLS_MCP_AUTH_METHOD="$AUTH_METHOD" \
       AGENT_TOOLS_MCP_SIGNING_SERVICE="$SIGNING_SERVICE" AGENT_TOOLS_MCP_STACK="$OUTPUT_STACK"

if [ -n "${M[smokeTest.__len__]+x}" ]; then
    echo ""; echo -e "${YELLOW}Running smoke test...${NC}"
    argv_from smokeTest SMOKE
    run_argv "smoke test" "$WORKDIR" "${SMOKE[@]}"
    echo -e "${GREEN}      OK: smoke test passed${NC}"
fi

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  MCP server deployed: $SERVER @ $REF ($COMMIT)${NC}"
echo -e "${GREEN}========================================${NC}"
echo -e "${CYAN}  Endpoint: $ENDPOINT${NC}"
echo -e "${CYAN}  Auth:     $AUTH_METHOD${SIGNING_SERVICE:+ (signing service: $SIGNING_SERVICE)}${NC}"
echo -e "${CYAN}  Stack:    $OUTPUT_STACK ($CURRENT_REGION)${NC}"
NAUX="${M[deploy.auxiliaryStacks.__len__]:-0}"
if [ "$NAUX" -gt 0 ]; then
    echo ""; echo -e "${YELLOW}  Also required (not deployed by this script):${NC}"
    for ((i=0; i<NAUX; i++)); do echo -e "${YELLOW}    - $(mget deploy.auxiliaryStacks.$i.template) [$(mget deploy.auxiliaryStacks.$i.scope)]${NC}"; done
fi
echo ""
echo -e "${CYAN}  Register with AWS DevOps Agent:${NC}"
case "$AUTH_METHOD" in
    sigv4)
        echo -e "${GRAY}    1. Create/choose an IAM role trusted by aidevops.amazonaws.com with these actions:${NC}"
        NACT="${M[auth.callerActions.__len__]:-0}"
        for ((i=0; i<NACT; i++)); do echo -e "${GRAY}         - $(mget auth.callerActions.$i)${NC}"; done
        echo -e "${GRAY}    2. aws devops-agent register-service --region <agent-space-region> --service mcpserversigv4 \\${NC}"
        echo -e "${GRAY}         --service-details '{\"mcpserversigv4\":{\"name\":\"$SERVER\",\"endpoint\":\"$ENDPOINT\",${NC}"
        echo -e "${GRAY}         \"authorizationConfig\":{\"region\":\"$CURRENT_REGION\",\"service\":\"$SIGNING_SERVICE\",\"mcpRoleArn\":\"<role-arn>\"}}}'${NC}"
        echo -e "${GRAY}    3. Associate the returned serviceId with your Agent Space and allowlist tools.${NC}" ;;
    oauth-client-credentials)
        echo -e "${GRAY}    Console -> Capability Providers -> MCP Server -> OAuth Client Credentials.${NC}"
        echo -e "${GRAY}    Client ID / token URL / scope come from the stack outputs named in the manifest;${NC}"
        echo -e "${GRAY}    retrieve the client secret with the manifest's clientSecret command.${NC}" ;;
    *)  echo -e "${GRAY}    Console -> Capability Providers -> MCP Server -> $AUTH_METHOD.${NC}" ;;
esac
echo -e "${CYAN}  Details:  $REPO/tree/$REF/$SPARSE_PATH/README.md${NC}"

[ "$KEEP_SOURCE" = true ] || rm -rf "$TEMP_ROOT"
