#!/bin/bash
# PreToolUse guard: block agent writes to governance files under
# .kiro/steering/ or .kiro/hooks/.
#
# Receives the tool invocation as JSON on stdin. Extracts every candidate
# target path and exits:
#   - exit 2  -> block the write (stderr is forwarded to the agent)
#   - exit 0  -> allow (silent, near-instant path check)
#
# This replaces the previous agent-type hook, which could only *ask* the
# agent to self-check and fired on every write regardless of path.

input="$(cat)"

# Extract ALL candidate paths from common write-tool fields. smart_relocate
# has both sourcePath and destinationPath, so we must check every one, not
# just the first. Uses python3 for robust JSON parsing; fails open (exit 0)
# if the payload can't be parsed, so legitimate edits are never wedged.
paths="$(printf '%s' "$input" | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)
ti = data.get("tool_input", {}) or {}
for key in ("path", "targetFile", "sourcePath", "destinationPath"):
    val = ti.get(key)
    if val:
        print(val)
' 2>/dev/null)"

# No path found -> nothing to protect, allow.
[ -z "$paths" ] && exit 0

# Check each candidate path; block if any targets a governance directory.
while IFS= read -r target_path; do
  [ -z "$target_path" ] && continue
  case "$target_path" in
    */.kiro/steering/*|.kiro/steering/*|*/.kiro/hooks/*|.kiro/hooks/*)
      echo "BLOCKED: '$target_path' is a governance file (.kiro/steering/ or .kiro/hooks/)." >&2
      echo "These are maintained by repo owners only. Contact the maintainers if changes are needed." >&2
      exit 2
      ;;
  esac
done <<< "$paths"

exit 0
