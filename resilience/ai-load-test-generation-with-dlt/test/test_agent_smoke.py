#!/usr/bin/env python3
"""Agent-level smoke checks (no Bedrock call, no AWS).

builder/tests/ owns the deterministic-tool tests (128 checks). This file only
covers the wiring that agent.py adds on top: prompt loading, model config,
and that the builder modules import cleanly from the project root.

Run: python3 test/test_agent_smoke.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "builder"))

_failures: list[str] = []
_passes = 0


def check(condition: bool, label: str) -> None:
    global _passes
    if condition:
        _passes += 1
    else:
        _failures.append(label)
        print(f"  FAIL {label}")


def main() -> int:
    print("agent wiring")

    prompt = (ROOT / "prompt" / "system_prompt.txt").read_text(encoding="utf-8")
    check(len(prompt) > 500, "system prompt is non-trivial")
    for marker in ("saveOnly", "weight", "body check", "prod"):
        check(marker in prompt, f"system prompt covers: {marker}")

    import agent  # noqa: E402
    check(agent.MODEL_PRIMARY.startswith(("us.", "global.", "eu.", "apac.")),
          "primary model is an inference profile ID, not a base model ID")
    check(agent.SYSTEM_PROMPT == prompt, "agent loads the prompt file verbatim")

    # The CRIS prefix must follow the region, not be written down: a hardcoded
    # one fails at invoke time everywhere outside that geography.
    from aws_utils import get_bedrock_model_id, get_region  # noqa: E402
    for region, expected in (
            ("us-west-2", "us."),        # region fixture, not configuration
            ("eu-west-1", "eu."),        # region fixture, not configuration
            ("ap-northeast-2", "apac."),
            ("ca-central-1", "global.")):
        model_id = get_bedrock_model_id("anthropic.claude-opus-4-8", region)
        check(model_id == f"{expected}anthropic.claude-opus-4-8",
              f"{region} resolves to the {expected}* inference profile")

    # No region is invented when none is configured. Returning "" lets boto3
    # raise NoRegionError, which names the problem; a substituted region would
    # instead call the wrong continent and look like a permissions error.
    saved = {k: os.environ.pop(k, None)
             for k in ("AWS_DEFAULT_REGION", "AWS_REGION")}
    try:
        check(get_region() == "", "no region configured resolves to empty, not a guess")
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v

    src = (ROOT / "agent.py").read_text(encoding="utf-8")
    check("us-west-2" not in src and "us-east-1" not in src,  # region: pattern
          "agent.py contains no hardcoded region")
    check('"us.anthropic' not in src,
          "agent.py contains no hardcoded inference-profile prefix")

    from spec_input import parse_spec_input, select_targets  # noqa: F401,E402
    from jmx_builder import build, validate_spec  # noqa: F401,E402
    check(True, "builder modules import from project root")

    import tools  # noqa: E402
    check(isinstance(tools.ALL_TOOLS, list), "tools package exposes ALL_TOOLS")

    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    check("AWS_PROFILE" in env_example and "BEDROCK_MODEL_PRIMARY" in env_example,
          ".env.example documents profile-based credentials")

    print(f"\n{_passes} passed, {len(_failures)} failed")
    for f in _failures:
        print(f"  - {f}")
    return 1 if _failures else 0


if __name__ == "__main__":
    sys.exit(main())
