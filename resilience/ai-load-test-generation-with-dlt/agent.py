#!/usr/bin/env python3
"""Load Test Generator Agent entrypoint.

Local:      python3 agent.py "your message"  (or no args for a hello check)
AgentCore:  the BedrockAgentCoreApp handler is the container entrypoint.

Credentials: locally from .env (AWS_PROFILE preferred over raw keys); in
AgentCore from the runtime execution role — never baked into the image.
"""

from __future__ import annotations

import os
import sys
from collections import OrderedDict
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

sys.path.insert(0, str(ROOT))
from aws_utils import get_bedrock_model_id, get_region  # noqa: E402

SYSTEM_PROMPT = (ROOT / "prompt" / "system_prompt.txt").read_text(encoding="utf-8")

# Inference profile IDs — base model IDs are not invokable on demand. The
# region geography decides the prefix (us./eu./apac./global.), so it is derived
# rather than written down: a hardcoded prefix fails at invoke time everywhere
# outside that one geography.
BEDROCK_REGION = os.environ.get("BEDROCK_REGION") or get_region()
MODEL_PRIMARY = (os.environ.get("BEDROCK_MODEL_PRIMARY")
                 or get_bedrock_model_id("anthropic.claude-opus-4-8",
                                         BEDROCK_REGION))
MODEL_FALLBACK = (os.environ.get("BEDROCK_MODEL_FALLBACK")
                  or get_bedrock_model_id("anthropic.claude-sonnet-5",
                                          BEDROCK_REGION))


def build_agent():
    """Construct the Strands agent. Tools are registered in tools/ (prompt 3);
    until then the agent runs conversation-only."""
    from strands import Agent
    from strands.models import BedrockModel

    if not BEDROCK_REGION:
        raise RuntimeError(
            "no region configured — set AWS_REGION (or BEDROCK_REGION to call "
            "Bedrock in a different region than the agent runs in). No region "
            "is assumed: a guess routes model calls to the wrong continent."
        )

    model = BedrockModel(
        model_id=MODEL_PRIMARY,
        region_name=BEDROCK_REGION,
    )

    tools = []
    try:
        from tools import ALL_TOOLS  # populated in prompt 3
        tools = ALL_TOOLS
    except ImportError:
        pass

    return Agent(model=model, system_prompt=SYSTEM_PROMPT, tools=tools)


# --------------------------------------------------------------------------
# Inline spec staging (invoke payload -> /tmp file)
# --------------------------------------------------------------------------


def _stage_inline_spec(payload: dict) -> str | None:
    """If the invoke payload carries the test spec inline, write it to a temp
    file and return its local path — so the agent can call
    ``parse_spec_input(file_path=...)`` without a separate S3 staging bucket.

    The AgentCore invoke contract has a single request body (``payload``, up to
    100 MB); there is no file-upload channel, so an inline spec travels as a
    field of that JSON. Recognized fields (first match wins):

      * ``spec_b64``  — base64 of the raw spec bytes (any format; escaping-safe)
      * ``spec``      — the spec as a JSON object/array, or a raw string
      * ``swagger``   — alias for ``spec``

    Optional ``spec_filename`` sets the parser suffix (``.json`` / ``.har`` …).
    Returns ``None`` when no inline spec is present (caller falls back to the
    ``s3://`` URI path). Writes only under the OS temp dir because the AgentCore
    ``/app`` layer is read-only — only ``/tmp`` is writable.
    """
    import base64
    import json
    import tempfile

    raw: bytes | None = None
    b64 = payload.get("spec_b64")
    if b64:
        try:
            raw = base64.b64decode(b64, validate=True)
        except Exception as exc:  # malformed base64
            raise ValueError(f"spec_b64 is not valid base64: {exc}") from exc
    else:
        spec = payload.get("spec", payload.get("swagger"))
        if spec is None:
            return None
        if isinstance(spec, (dict, list)):
            raw = json.dumps(spec).encode("utf-8")
        elif isinstance(spec, str):
            raw = spec.encode("utf-8")
        else:
            raise ValueError("spec must be a JSON object/array or a string")

    suffix = Path(payload.get("spec_filename") or "").suffix or ".json"
    fd, tmp_name = tempfile.mkstemp(prefix="spec-inline-", suffix=suffix)
    with os.fdopen(fd, "wb") as fh:
        fh.write(raw)
    return tmp_name


# --------------------------------------------------------------------------
# AgentCore Runtime handler
# --------------------------------------------------------------------------

try:
    from bedrock_agentcore.runtime import BedrockAgentCoreApp

    app = BedrockAgentCoreApp()

    # Session-scoped agents, so follow-up turns see the previous ones. AgentCore
    # gives each runtimeSessionId a dedicated microVM and keeps it warm between
    # invocations, so an in-process cache is enough — rebuilding the Agent every
    # call (as this did) threw the conversation history away while the process
    # itself was still alive. Bounded because the dict outlives a session: the
    # microVM is torn down after 15 min idle / 8 h max, and the entries for
    # ended sessions would otherwise leak. NOT durable by design — for history
    # that survives session termination, use AgentCore Memory.
    _AGENTS: "OrderedDict[str, object]" = OrderedDict()
    _AGENT_CACHE_MAX = 8

    def _agent_for(context) -> object:
        sid = getattr(context, "session_id", None) or "default"
        agent = _AGENTS.get(sid)
        if agent is None:
            agent = build_agent()
            _AGENTS[sid] = agent
            while len(_AGENTS) > _AGENT_CACHE_MAX:
                _AGENTS.popitem(last=False)  # evict least-recently-created
        else:
            _AGENTS.move_to_end(sid)
        return agent

    @app.entrypoint
    def invoke(payload: dict, context=None) -> dict:
        payload = payload or {}
        prompt = payload.get("prompt", "")
        if not prompt:
            return {"error": "payload must contain 'prompt'"}
        # Optional inline spec: stage it to /tmp and point the agent at the
        # local path so it can parse_spec_input(file_path=...) — no S3 bucket
        # needed for the common case. Large specs can still use an s3:// URI.
        try:
            spec_path = _stage_inline_spec(payload)
        except ValueError as exc:
            return {"error": str(exc)}
        if spec_path:
            prompt = (
                f"{prompt}\n\n"
                f"[system] The spec for the target under test was supplied as a "
                f"local file: {spec_path}\n"
                f"That is a local path, not S3. Call "
                f'parse_spec_input(file_path="{spec_path}") first to build the '
                f"endpoint inventory, then go on to generate the script."
            )
        agent = _agent_for(context)
        result = agent(prompt)
        return {"result": str(result)}

except ImportError:
    app = None  # local dev without the agentcore package installed


# --------------------------------------------------------------------------
# local CLI
# --------------------------------------------------------------------------


def chat() -> int:
    """Interactive multi-turn session. The Agent object keeps conversation
    history for the life of the process, so follow-up questions work — asking
    'why are those 4 blocked?' after a decision table refers to the same
    context."""
    # Non-ASCII input dies with UnicodeDecodeError when the terminal locale is
    # not UTF-8 (common with pyenv builds); force it rather than crash.
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    agent = build_agent()
    print(f"[model {MODEL_PRIMARY} @ {BEDROCK_REGION}]")
    print("interactive mode — type exit or press Ctrl-D to quit\n")
    while True:
        try:
            user = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if user.lower() in ("exit", "quit"):
            return 0
        if not user:
            continue
        try:
            agent(user)  # streams its own output
        except Exception as exc:  # keep the session alive on a failed turn
            print(f"[error: {exc}]")
        print()


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0] in ("--chat", "-i"):
        return chat()
    agent = build_agent()
    print(f"[model {MODEL_PRIMARY} @ {BEDROCK_REGION}]")
    agent(" ".join(args))  # streams its own output
    return 0


if __name__ == "__main__":
    if app is not None and os.environ.get("AGENTCORE_RUNTIME"):
        # Bind the container's external interface, not loopback: AgentCore
        # Runtime reaches the container on 0.0.0.0:8080. Recent bedrock-agentcore
        # SDK defaults app.run() to 127.0.0.1 (local-dev safe), which makes the
        # server unreachable in-container and every InvokeAgentRuntime 502.
        app.run(host="0.0.0.0", port=8080)
    else:
        sys.exit(main())
