#!/bin/bash
# PreToolUse guard for governance files. Same set as CODEOWNERS and
# .github/workflows/governance-guard.yml:
#   .kiro/ (tracked parts), .github/, root .ash/, root bandit.yaml, .secrets.baseline
#
#   BLOCK (exit 2): .kiro/steering/ and .kiro/hooks/ - the rules and the hooks
#                   that police the agent; never written by the agent itself.
#   ASK   (exit 0 + permissionDecision "ask"): every other governance path, so a
#                   maintainer approves each write in the session.
#   ALLOW: everything else, including Kiro's local working folders
#          (.kiro/specs/, .kiro/settings/, .kiro/workflow-runs/, all gitignored),
#          a demo's own .ash/ or bandit.yaml, and paths outside this repo
#          (e.g. ~/.kiro/steering/).
#
# Shell commands are checked best-effort: a command that names a governance path
# AND looks like a write gets "ask". This is a speed bump, not the gate: CI
# (governance-guard) labels every PR touching these paths however the change was
# made, and CODEOWNERS requires a maintainer approval to merge.
#
# Fails open (allow) on input it cannot parse, so legitimate work is never wedged.

input="$(cat)"

printf '%s' "$input" | python3 -c '
import json, re, sys

try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)

REPO = "sample-aws-genai-ops-demos"
LOCAL_ONLY = (".kiro/specs/", ".kiro/settings/", ".kiro/workflow-runs/")
BLOCK = (".kiro/steering/", ".kiro/hooks/")
ASK_PREFIX = (".kiro/", ".github/", ".ash/")
ASK_EXACT = ("bandit.yaml", ".secrets.baseline")

def repo_relative(p):
    """Path relative to the repo root (or a worktree root), or None if outside it."""
    p = p.replace("\\", "/")
    if not p.startswith("/") and not re.match(r"^[A-Za-z]:/", p):
        rel = p
    else:
        m = re.search(r"/" + re.escape(REPO) + r"/(.*)$", p)
        if not m:
            return None                       # outside this repo: not ours to guard
        rel = m.group(1)
    rel = re.sub(r"^\./", "", rel)
    rel = re.sub(r"^\.worktrees/[^/]+/", "", rel)   # a worktree is the same repo
    return rel

def classify(rel):
    if rel is None or rel.startswith(LOCAL_ONLY):
        return "allow"
    if rel.startswith(BLOCK):
        return "block"
    if rel.startswith(ASK_PREFIX) or rel in ASK_EXACT:
        return "ask"
    return "allow"

def ask(reason):
    print(json.dumps({"hookSpecificOutput": {
        "permissionDecision": "ask",
        "permissionDecisionReason": reason}}))
    sys.exit(0)

ti = data.get("tool_input", {}) or {}

# File tools: check every path field (smart_relocate has two).
decision, hit = "allow", None
for key in ("path", "targetFile", "sourcePath", "destinationPath"):
    val = ti.get(key)
    if not val:
        continue
    d = classify(repo_relative(str(val)))
    if d == "block":
        sys.stderr.write(
            "BLOCKED: %r is a governance file (.kiro/steering/ or .kiro/hooks/). "
            "These are maintained by repo owners only.\n" % val)
        sys.exit(2)
    if d == "ask":
        decision, hit = "ask", val
if decision == "ask":
    ask("%s is a governance file (CODEOWNERS: maintainer approval). Approve this write?" % hit)

# Shell: best effort. Governance path named + a write-looking command -> ask.
cmd = ti.get("command")
if isinstance(cmd, str) and cmd:
    gov = re.compile(
        r"(?:^|[\s\x27\x22=:(])(?:\./)?(?:\.worktrees/[^/\s]+/)?"
        r"(\.kiro/(?!specs/|settings/|workflow-runs/)\S*|\.github/\S*|\.ash/\S*|bandit\.yaml|\.secrets\.baseline)")
    write = re.compile(
        r">|\btee\b|\bsed\s+(-[a-zA-Z]*i|--in-place)|\bperl\s+-[a-zA-Z]*i|\b(cp|mv|rm|rmdir|ln|truncate|install|touch|chmod)\b"
        r"|\bgit\s+(checkout|restore|apply|am|rm|mv|reset|stash\s+(pop|apply))\b")
    m = gov.search(cmd)
    if m and write.search(cmd):
        ask("This shell command may modify governance file %s (CODEOWNERS: maintainer approval). Approve?" % m.group(1))

sys.exit(0)
'
exit $?
