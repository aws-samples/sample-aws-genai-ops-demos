"""Regression tests for Bedrock prompt caching (#PERF-1).

The system prompt (~7,500 tokens) and TOOL_CONFIG's tool list (~10 tool
schemas) are identical on every single conversation turn -- they never vary
by user, mode, or guided-tour step. Added cachePoint checkpoints to both so
Bedrock can skip re-processing them within the 5-minute TTL, targeting the
Step 2 timeout Quick's guided-tour re-test surfaced (less to process before
generation starts reduces latency, not just cost).

These tests assert on the exact request shape sent to bedrock_client.converse
without making a real Bedrock call -- they pin the presence and placement of
the cachePoint blocks, which is easy to silently regress (e.g. a future edit
that rebuilds the `system` list without preserving the cache checkpoint).
"""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import agent  # noqa: E402


def _stop_response(text="ok"):
    return {
        "output": {"message": {"content": [{"text": text}]}},
        "stopReason": "end_turn",
        "usage": {"inputTokens": 10, "outputTokens": 5},
    }


class ToolConfigCachePointTest(unittest.TestCase):
    def test_tool_config_ends_with_a_cache_point(self):
        self.assertIn("tools", agent.TOOL_CONFIG)
        tools = agent.TOOL_CONFIG["tools"]
        self.assertGreater(len(tools), 1, "expected multiple real tool specs before the cache point")
        last = tools[-1]
        self.assertIn("cachePoint", last)
        self.assertEqual(last["cachePoint"], {"type": "default"})

    def test_every_other_entry_is_a_real_tool_spec(self):
        # The cache point must be the ONLY non-toolSpec entry -- if a future
        # edit inserts something else into this list, this catches it.
        for entry in agent.TOOL_CONFIG["tools"][:-1]:
            self.assertIn("toolSpec", entry)


class SystemPromptCachePointTest(unittest.TestCase):
    def test_first_converse_call_includes_cache_point_after_system_text(self):
        mock_response = _stop_response()
        with patch.object(
            agent.bedrock_client, "converse", return_value=mock_response
        ) as mock_converse:
            agent.converse_with_tools(
                messages=[{"role": "user", "content": [{"text": "hello"}]}],
                system_prompt="a static system prompt",
            )

        mock_converse.assert_called_once()
        call_kwargs = mock_converse.call_args.kwargs
        self.assertIn("system", call_kwargs)
        system_blocks = call_kwargs["system"]
        self.assertEqual(len(system_blocks), 2)
        self.assertEqual(system_blocks[0], {"text": "a static system prompt"})
        self.assertEqual(system_blocks[1], {"cachePoint": {"type": "default"}})

    def test_followup_converse_call_in_a_tool_use_loop_reuses_same_system_blocks(self):
        """When the model calls a tool, the SECOND converse() call (after
        the tool result is appended) must send the identical system blocks
        -- including the cache point -- not a re-built list without it.
        Changing the system content between calls in the same turn would
        invalidate the cache checkpoint AWS just wrote."""
        tool_use_response = {
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "toolUse": {
                                "toolUseId": "t1",
                                "name": "list_findings",
                                "input": {},
                            }
                        }
                    ],
                }
            },
            "stopReason": "tool_use",
            "usage": {"inputTokens": 10, "outputTokens": 5},
        }
        final_response = _stop_response("done")

        with patch.object(
            agent.bedrock_client,
            "converse",
            side_effect=[tool_use_response, final_response],
        ) as mock_converse, patch.object(
            agent, "invoke_tool", return_value={"findings": [], "coverage": []}
        ):
            agent.converse_with_tools(
                messages=[{"role": "user", "content": [{"text": "list findings"}]}],
                system_prompt="a static system prompt",
            )

        self.assertEqual(mock_converse.call_count, 2)
        first_system = mock_converse.call_args_list[0].kwargs["system"]
        second_system = mock_converse.call_args_list[1].kwargs["system"]
        self.assertEqual(first_system, second_system)
        self.assertEqual(second_system[-1], {"cachePoint": {"type": "default"}})


if __name__ == "__main__":
    unittest.main()
