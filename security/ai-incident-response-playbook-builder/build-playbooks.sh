#!/bin/bash
set -e

# Default values
OUTPUT_FORMAT="both"
MODEL_ID="anthropic.claude-3-5-sonnet-20241022-v2:0"
REGION=""
ORG_CONTEXT=""
OUTPUT_DIR="./output"
SKIP_SETUP=false

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --output-format)
            OUTPUT_FORMAT="$2"
            shift 2
            ;;
        --model-id)
            MODEL_ID="$2"
            shift 2
            ;;
        --region)
            REGION="$2"
            shift 2
            ;;
        --org-context)
            ORG_CONTEXT="$2"
            shift 2
            ;;
        --output-dir)
            OUTPUT_DIR="$2"
            shift 2
            ;;
        -s|--skip-setup)
            SKIP_SETUP=true
            shift
            ;;
        -h|--help)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Generate AI-powered incident response playbooks from your AWS architecture"
            echo ""
            echo "Options:"
            echo "  --output-format FORMAT  ssm, markdown, or both (default: both)"
            echo "  --model-id ID           Bedrock model ID (default: anthropic.claude-3-5-sonnet-20241022-v2:0)"
            echo "  --region REGION         AWS region to scan (default: current configured region)"
            echo "  --org-context FILE      Path to JSON with org-specific context"
            echo "  --output-dir DIR        Local output directory (default: ./output)"
            echo "  -s, --skip-setup        Skip CDK deployment (use existing S3 bucket)"
            echo "  -h, --help              Show this help message"
            echo ""
            echo "Examples:"
            echo "  $0"
            echo "  $0 --output-format markdown"
            echo "  $0 --org-context org-context.json --region us-east-1"
            echo "  $0 -s   # Skip infra, reuse existing bucket"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

echo "=== AI Incident Response Playbook Builder ==="
echo "Generates tailored IR playbooks from your AWS architecture"
echo ""
echo "Pipeline:"
echo "  1. Deploy infrastructure (S3 output bucket via CDK)"
echo "  2. Discover architecture (read-only API calls)"
echo "  3. Analyze threats via Amazon Bedrock"
echo "  4. Generate playbooks with MITRE ATT&CK mapping"
echo "  5. Upload to S3 + save locally"
echo ""

# Get script and shared directories
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$SCRIPT_DIR/src"
CDK_DIR="$SCRIPT_DIR/infrastructure/cdk"
SHARED_SCRIPTS_DIR="$SCRIPT_DIR/../../shared/scripts"

# Silence JSII warnings for untested Node.js versions
export JSII_SILENCE_WARNING_UNTESTED_NODE_VERSION=1

# ── Prerequisites ──────────────────────────────────────────────────────────────
if [ "$SKIP_SETUP" = false ]; then
    echo "Running prerequisites check..."
    "$SHARED_SCRIPTS_DIR/check-prerequisites.sh" \
        --required-service "bedrock" \
        --min-python-version "3.10" \
        --require-cdk

    if [[ $? -ne 0 ]]; then
        echo "Prerequisites check failed"
        exit 1
    fi
fi

# Resolve region
if [[ -z "$REGION" ]]; then
    source "$SHARED_SCRIPTS_DIR/../utils/aws-utils.sh"
    REGION=$(get_aws_region)
fi
if [[ -z "$REGION" ]]; then
    REGION="${AWS_DEFAULT_REGION:-}"
fi
if [[ -z "$REGION" ]]; then
    REGION=$(aws configure get region 2>/dev/null || true)
fi
if [[ -z "$REGION" ]]; then
    echo "ERROR: No AWS region configured"
    echo "  aws configure set region <region>"
    exit 1
fi

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text --no-cli-pager)

# ── Deploy Infrastructure ─────────────────────────────────────────────────────
if [ "$SKIP_SETUP" = false ]; then
    echo ""
    echo "Deploying infrastructure via CDK..."
    echo "  Region: $REGION"

    "$SHARED_SCRIPTS_DIR/deploy-cdk.sh" --cdk-directory "$CDK_DIR"

    if [[ $? -ne 0 ]]; then
        echo "CDK deployment failed"
        exit 1
    fi
fi

# Get S3 bucket from stack outputs
STACK_NAME="PlaybookBuilderStack-$REGION"
OUTPUT_BUCKET=$(aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --region "$REGION" \
    --no-cli-pager \
    --query "Stacks[0].Outputs[?OutputKey=='OutputBucketName'].OutputValue" \
    --output text)

if [[ -z "$OUTPUT_BUCKET" ]]; then
    echo "❌ Failed to get S3 bucket from stack outputs"
    if [ "$SKIP_SETUP" = true ]; then
        echo "  Stack may not exist. Run without -s to deploy infrastructure first."
    fi
    exit 1
fi

# Generate unique job ID
JOB_ID="ir-playbooks-$(date +%Y%m%d-%H%M%S)"

echo ""
echo "Configuration:"
echo "  Account:       $ACCOUNT_ID"
echo "  Region:        $REGION"
echo "  Model:         $MODEL_ID"
echo "  Output format: $OUTPUT_FORMAT"
echo "  S3 Bucket:     $OUTPUT_BUCKET"
echo "  Job ID:        $JOB_ID"
if [[ -n "$ORG_CONTEXT" ]]; then
    echo "  Org context:   $ORG_CONTEXT"
fi
echo ""

# ── Install Python dependencies ────────────────────────────────────────────────
echo "Installing Python dependencies..."
pip install -q -r "$SRC_DIR/requirements.txt" 2>&1 > /dev/null
if [[ $? -ne 0 ]]; then
    echo "  ❌ Failed to install Python dependencies"
    exit 1
fi
echo "  ✓ Dependencies installed"

# Ensure output directories exist
mkdir -p "$OUTPUT_DIR/reports"
mkdir -p "$OUTPUT_DIR/playbooks"
mkdir -p "$OUTPUT_DIR/ssm-documents"

# ── Phase 1: Discovery ────────────────────────────────────────────────────────
echo ""
echo "Phase 1: Discovering AWS architecture..."
echo "  (Read-only API calls — nothing is modified)"

DISCOVERY_START=$(date +%s)
PROFILE_PATH="$OUTPUT_DIR/reports/architecture-profile.json"

python "$SRC_DIR/discovery.py" \
    --region "$REGION" \
    --output "$PROFILE_PATH"

if [[ $? -ne 0 ]]; then
    echo "  ❌ Discovery failed"
    exit 1
fi

DISCOVERY_END=$(date +%s)
DISCOVERY_ELAPSED=$((DISCOVERY_END - DISCOVERY_START))
echo "  ✓ Architecture discovered (${DISCOVERY_ELAPSED}s)"

# ── Phase 2: Threat Assessment & Playbook Generation ──────────────────────────
echo ""
echo "Phase 2: Generating playbooks via Amazon Bedrock..."
echo "  This may take 1-5 minutes depending on account complexity"

GENERATE_START=$(date +%s)

GENERATE_ARGS=(
    "$SRC_DIR/generator.py"
    "--profile" "$PROFILE_PATH"
    "--model-id" "$MODEL_ID"
    "--region" "$REGION"
    "--output-dir" "$OUTPUT_DIR"
    "--output-format" "$OUTPUT_FORMAT"
)

if [[ -n "$ORG_CONTEXT" ]]; then
    if [[ ! -f "$ORG_CONTEXT" ]]; then
        echo "  ❌ Org context file not found: $ORG_CONTEXT"
        exit 1
    fi
    GENERATE_ARGS+=("--org-context" "$ORG_CONTEXT")
fi

python "${GENERATE_ARGS[@]}"

if [[ $? -ne 0 ]]; then
    echo "  ❌ Playbook generation failed"
    exit 1
fi

GENERATE_END=$(date +%s)
GENERATE_ELAPSED=$((GENERATE_END - GENERATE_START))
echo "  ✓ Playbooks generated (${GENERATE_ELAPSED}s)"

# ── Phase 3: Output Assembly ──────────────────────────────────────────────────
echo ""
echo "Phase 3: Assembling output..."

python "$SRC_DIR/output.py" \
    --output-dir "$OUTPUT_DIR" \
    --output-format "$OUTPUT_FORMAT"

if [[ $? -ne 0 ]]; then
    echo "  ❌ Output assembly failed"
    exit 1
fi
echo "  ✓ Output assembled"

# ── Phase 4: Upload to S3 ────────────────────────────────────────────────────
echo ""
echo "Phase 4: Uploading to S3..."

aws s3 cp "$OUTPUT_DIR/" "s3://$OUTPUT_BUCKET/$JOB_ID/" \
    --recursive \
    --region "$REGION" \
    --no-cli-pager > /dev/null

if [[ $? -eq 0 ]]; then
    echo "  ✓ Uploaded to s3://$OUTPUT_BUCKET/$JOB_ID/"
else
    echo "  ❌ S3 upload failed"
    exit 1
fi

# ── Summary ───────────────────────────────────────────────────────────────────
TOTAL_END=$(date +%s)
TOTAL_ELAPSED=$((TOTAL_END - DISCOVERY_START))

echo ""
echo "=== Playbook Generation Complete ==="
echo "  Total time: ${TOTAL_ELAPSED}s"
echo ""

# List generated files
echo "Generated files:"
if [[ -d "$OUTPUT_DIR/reports" ]]; then
    echo "  Reports:"
    find "$OUTPUT_DIR/reports" -type f | while read -r file; do
        echo "    $(basename "$file")"
    done
fi
if [[ -d "$OUTPUT_DIR/playbooks" ]]; then
    PLAYBOOK_COUNT=$(find "$OUTPUT_DIR/playbooks" -type f | wc -l | tr -d ' ')
    if [[ "$PLAYBOOK_COUNT" -gt 0 ]]; then
        echo "  Playbooks ($PLAYBOOK_COUNT):"
        find "$OUTPUT_DIR/playbooks" -type f | while read -r file; do
            echo "    $(basename "$file")"
        done
    fi
fi
if [[ -d "$OUTPUT_DIR/ssm-documents" ]]; then
    SSM_COUNT=$(find "$OUTPUT_DIR/ssm-documents" -type f | wc -l | tr -d ' ')
    if [[ "$SSM_COUNT" -gt 0 ]]; then
        echo "  SSM Documents ($SSM_COUNT):"
        find "$OUTPUT_DIR/ssm-documents" -type f | while read -r file; do
            echo "    $(basename "$file")"
        done
    fi
fi

echo ""
echo "=== Output Locations ==="
echo ""
echo "S3 (persistent):"
echo "  s3://$OUTPUT_BUCKET/$JOB_ID/"
echo ""
echo "Browse S3 output:"
echo "  aws s3 ls s3://$OUTPUT_BUCKET/$JOB_ID/ --recursive"
echo ""
echo "Download from S3:"
echo "  aws s3 cp s3://$OUTPUT_BUCKET/$JOB_ID/ ./ir-playbooks --recursive"
echo ""
echo "Local copy:"
echo "  $OUTPUT_DIR/"
echo ""
echo "=== Next Steps ==="
echo "1. Review playbooks in $OUTPUT_DIR/playbooks/"
echo "2. Check MITRE ATT&CK coverage in $OUTPUT_DIR/reports/attack-coverage-matrix.md"
echo "3. Import SSM documents:"
echo "   aws ssm create-document --content file://$OUTPUT_DIR/ssm-documents/<doc>.json --name <name> --document-type Automation"
echo "4. Run tabletop exercises with your team using the generated playbooks"
echo ""
echo "Cleanup:"
echo "  cd infrastructure/cdk && npx cdk destroy --no-cli-pager"
