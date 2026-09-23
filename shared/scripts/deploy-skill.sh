#!/bin/bash
# GenAI Ops Demo Library - Shared Agent Tools Skill Packager (Bash)
#
# Fetches ONE skill (or custom agent) from the public Agent Tools repository
# (aws/tools-for-devops-agent) at a given ref and packages it for upload to an
# AWS DevOps Agent Agent Space.
#
# Design rule: one artefact, one home. The skill's source stays in the Agent Tools
# repository; this script fetches it into a temp dir at deploy time. Nothing is
# copied into this repository.
#
# Usage (from a demo directory):
#   ../../shared/scripts/deploy-skill.sh --skill eks-upgrade-readiness --ref main
#   ../../shared/scripts/deploy-skill.sh --custom-agent aws-health-report --ref v1.2.0
#
# Exports (when sourced): AGENT_TOOLS_SKILL_ZIP, AGENT_TOOLS_SKILL_DIR

set -e

SKILL=""
CUSTOM_AGENT=""
REF="main"
REPO="https://github.com/aws/tools-for-devops-agent"
OUTPUT_DIRECTORY="."
KEEP_SOURCE=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --skill) SKILL="$2"; shift 2 ;;
        --custom-agent) CUSTOM_AGENT="$2"; shift 2 ;;
        --ref) REF="$2"; shift 2 ;;
        --repo) REPO="$2"; shift 2 ;;
        --output-directory) OUTPUT_DIRECTORY="$2"; shift 2 ;;
        --keep-source) KEEP_SOURCE=true; shift ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; GRAY='\033[0;90m'; NC='\033[0m'

if [ -z "$SKILL" ] && [ -z "$CUSTOM_AGENT" ]; then
    echo -e "${RED}ERROR: Pass --skill <name> or --custom-agent <name>${NC}"; exit 1
fi
if [ -n "$SKILL" ] && [ -n "$CUSTOM_AGENT" ]; then
    echo -e "${RED}ERROR: Pass either --skill or --custom-agent, not both${NC}"; exit 1
fi

if [ -n "$SKILL" ]; then IS_SKILL=true; NAME="$SKILL"; KIND_DIR="skills"; else IS_SKILL=false; NAME="$CUSTOM_AGENT"; KIND_DIR="custom-agents"; fi
SPARSE_PATH="$KIND_DIR/$NAME"

if ! [[ "$NAME" =~ ^[a-z0-9][a-z0-9-]{0,63}$ ]]; then
    echo -e "${RED}ERROR: Invalid name '$NAME' (lowercase letters, digits and hyphens only)${NC}"; exit 1
fi

echo ""
echo -e "${CYAN}=== Agent Tools Skill Packager (Shared Script) ===${NC}"
echo -e "${GRAY}      Repository: $REPO${NC}"
echo -e "${GRAY}      Path:       $SPARSE_PATH${NC}"
echo -e "${GRAY}      Ref:        $REF${NC}"

if ! command -v git >/dev/null 2>&1; then
    echo -e "${RED}ERROR: git is required. Install from https://git-scm.com${NC}"; exit 1
fi

TEMP_ROOT=$(mktemp -d -t agent-tools-XXXXXXXX)

echo ""
echo -e "${YELLOW}Fetching $SPARSE_PATH @ $REF ...${NC}"
CLONE_RESULT=$(git clone --quiet --depth 1 --filter=blob:none --sparse --branch "$REF" "$REPO" "$TEMP_ROOT" 2>&1 && git -C "$TEMP_ROOT" sparse-checkout set "$SPARSE_PATH" 2>&1 || echo "FAILED")
if [[ "$CLONE_RESULT" == *"FAILED"* ]]; then
    echo -e "${RED}      ERROR: Could not fetch '$SPARSE_PATH' at ref '$REF' from $REPO${NC}"
    echo -e "${YELLOW}      Check the ref exists (tag or branch) and the name is spelled correctly.${NC}"
    rm -rf "$TEMP_ROOT"; exit 1
fi

SOURCE_DIR="$TEMP_ROOT/$SPARSE_PATH"
if [ ! -d "$SOURCE_DIR" ]; then
    echo -e "${RED}      ERROR: '$SPARSE_PATH' does not exist at ref '$REF'${NC}"
    rm -rf "$TEMP_ROOT"; exit 1
fi
COMMIT=$(git -C "$TEMP_ROOT" rev-parse --short HEAD)
echo -e "${GREEN}      OK: fetched $SPARSE_PATH @ $REF ($COMMIT)${NC}"
export AGENT_TOOLS_SKILL_DIR="$SOURCE_DIR"

if [ "$IS_SKILL" = true ]; then
    if [ ! -f "$SOURCE_DIR/SKILL.md" ]; then
        echo -e "${RED}      ERROR: No SKILL.md in $SPARSE_PATH - not a skill directory${NC}"; exit 1
    fi

    echo ""
    echo -e "${YELLOW}Building upload zip...${NC}"
    # Agent Tools upload rules: allowed extensions only; never the repo-only files/dirs.
    STAGE_DIR="$TEMP_ROOT/stage/$NAME"
    mkdir -p "$STAGE_DIR"
    FILE_COUNT=0
    while IFS= read -r -d '' f; do
        rel="${f#"$SOURCE_DIR"/}"
        mkdir -p "$STAGE_DIR/$(dirname "$rel")"
        cp "$f" "$STAGE_DIR/$rel"
        FILE_COUNT=$((FILE_COUNT + 1))
    done < <(find "$SOURCE_DIR" -type f \
        \( -iname '*.md' -o -iname '*.txt' -o -iname '*.json' -o -iname '*.yaml' -o -iname '*.yml' \
           -o -iname '*.xml' -o -iname '*.csv' -o -iname '*.tsv' -o -iname '*.html' -o -iname '*.htm' \
           -o -iname '*.png' -o -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.gif' -o -iname '*.svg' \
           -o -iname '*.webp' -o -iname '*.pdf' \) \
        ! -name 'README.md' ! -name 'CHANGELOG.md' ! -name '.skilleval.yaml' ! -name '.skilleval.yml' \
        ! -path '*/evals/*' ! -path '*/scripts/*' ! -path '*/.claude/*' -print0)

    if [ "$FILE_COUNT" -eq 0 ]; then
        echo -e "${RED}      ERROR: Nothing to package after applying upload rules${NC}"; exit 1
    fi

    OUT_DIR=$(cd "$OUTPUT_DIRECTORY" && pwd)
    ZIP_PATH="$OUT_DIR/$NAME.zip"
    rm -f "$ZIP_PATH"
    (cd "$TEMP_ROOT/stage" && zip -q -r "$ZIP_PATH" "$NAME")
    export AGENT_TOOLS_SKILL_ZIP="$ZIP_PATH"
    echo -e "${GREEN}      OK: $FILE_COUNT files -> $ZIP_PATH${NC}"

    echo ""
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}  Skill packaged: $NAME @ $REF ($COMMIT)${NC}"
    echo -e "${GREEN}========================================${NC}"
    echo -e "${CYAN}  Zip:      $ZIP_PATH${NC}"
    echo -e "${CYAN}  Upload:   DevOps Agent console -> Agent Space -> Skills -> Upload${NC}"
    echo -e "${GRAY}            Pick 'All agents' if a custom agent will use this skill.${NC}"
    echo -e "${CYAN}  Source:   $REPO/tree/$REF/$SPARSE_PATH${NC}"
else
    if [ ! -f "$SOURCE_DIR/SYSTEM_PROMPT.md" ]; then
        echo -e "${RED}      ERROR: No SYSTEM_PROMPT.md in $SPARSE_PATH - not a custom agent directory${NC}"; exit 1
    fi
    echo ""
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}  Custom agent fetched: $NAME @ $REF ($COMMIT)${NC}"
    echo -e "${GREEN}========================================${NC}"
    echo -e "${CYAN}  Prompt:   $SOURCE_DIR/SYSTEM_PROMPT.md${NC}"
    echo -e "${CYAN}  Create:   DevOps Agent console -> Custom agents -> Create (Form),${NC}"
    echo -e "${GRAY}            paste SYSTEM_PROMPT.md, then assign skills and tools.${NC}"
    echo -e "${CYAN}  Details:  $REPO/tree/$REF/$SPARSE_PATH/README.md${NC}"
    KEEP_SOURCE=true
fi

if [ "$KEEP_SOURCE" = false ]; then
    rm -rf "$TEMP_ROOT"
else
    echo -e "${GRAY}  Source dir kept: $SOURCE_DIR${NC}"
fi
