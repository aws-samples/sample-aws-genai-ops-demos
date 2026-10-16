"""Tests for the deterministic Change Request short-circuit (P1 / R6).

The free-form Bedrock synthesis of the six-section CR document intermittently
breached the API Gateway 29s ceiling (HTTP 504, then a successful retry). The
short-circuit assembles the document locally from check_dependencies with zero
Bedrock round trips. These tests pin that contract: converse_with_tools is
never called, check_dependencies runs exactly once, the six sections render,
and an unnamed CR request falls through to the model.
"""

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import agent  # noqa: E402


def _sample_dependencies(level="HIGH", dependents=2):
    deps = [
        {
            "type": "role",
            "name": f"dependent-role-{i}",
            "relationship": "can_assume_target",
            "impact": "assumes the target",
        }
        for i in range(dependents)
    ]
    return {
        "entity_arn": "arn:aws:iam::000000000000:role/CloudSecAuditRole",
        "entity_type": "role",
        "entity_name": "CloudSecAuditRole",
        "direct_dependents": deps,
        "trust_relationships": [],
        "policy_attachments": [],
        "dependency_graph": {},
        "warnings": [],
        "risk_score": {
            "score": 55,
            "level": level,
            "factors": [f"{dependents} dependent(s)", "Broad trust: *"],
            "recommendation": "Test changes in a non-production environment first",
        },
        "coverage": [{"source": "iam", "state": "checked", "detail": "IAM, global"}],
    }


class ChangeRequestIntentTest(unittest.TestCase):
    def test_matches_common_phrasings(self):
        self.assertIsNotNone(
            agent._CHANGE_REQUEST_INTENT.search("generate a change request for CloudSecAuditRole")
        )
        self.assertIsNotNone(
            agent._CHANGE_REQUEST_INTENT.search("draft a formal change-request for deleting MyRole")
        )
        self.assertIsNotNone(
            agent._CHANGE_REQUEST_INTENT.search("create the change request for the role AdminRole")
        )

    def test_rejects_unrelated_prompts(self):
        self.assertIsNone(agent._CHANGE_REQUEST_INTENT.search("show my active findings"))
        self.assertIsNone(agent._CHANGE_REQUEST_INTENT.search("audit my access keys"))
        self.assertIsNone(agent._CHANGE_REQUEST_INTENT.search("what is a change window"))

    def test_role_name_extraction(self):
        m = agent._CR_ROLE_NAME.search("generate a change request for deleting ConsoleAdminAccess")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "ConsoleAdminAccess")
        m2 = agent._CR_ROLE_NAME.search("change request for the role `CloudSecAuditRole`")
        self.assertIsNotNone(m2)
        self.assertEqual(m2.group(1), "CloudSecAuditRole")


class HandlerChangeRequestShortCircuitTest(unittest.TestCase):
    def _event(self, message, history=None):
        return {
            "httpMethod": "POST",
            "body": json.dumps(
                {"message": message, "history": history or [], "mode": "guided"}
            ),
        }

    def test_named_role_renders_cr_without_bedrock(self):
        with patch.object(agent, "invoke_tool", return_value=_sample_dependencies()) as invoke, \
                patch.object(agent, "converse_with_tools") as converse:
            response = agent.handler(
                self._event("generate a change request for deleting CloudSecAuditRole"), None
            )

        # The whole point: no model round trip.
        converse.assert_not_called()
        invoke.assert_called_once()
        tool_name, tool_input = invoke.call_args.args
        self.assertEqual(tool_name, "check_dependencies")
        self.assertIn(":role/CloudSecAuditRole", tool_input["entity_arn"])

        body = json.loads(response["body"])
        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(body["usage"], {"inputTokens": 0, "outputTokens": 0})
        self.assertEqual(len(body["tools_used"]), 1)
        self.assertIsNone(body["pagination"])

        doc = body["response"]
        # All six sections present + CR identity (so export classifies it as a CR).
        self.assertIn("Change Request", doc)
        self.assertIn("CloudSecAuditRole", doc)
        self.assertIn("## 1. Summary", doc)
        self.assertIn("## 2. Blast radius analysis", doc)
        self.assertIn("Rollback plan", doc)
        self.assertIn("## 4. Testing plan", doc)
        self.assertIn("## 5. Approval requirements", doc)
        self.assertIn("## 6. Implementation window", doc)
        # Blast-radius summary names a dependent (summarized, not raw dump).
        self.assertIn("dependent-role-0", doc)

    def test_unnamed_cr_request_falls_through_to_model(self):
        """A CR request with no role named must NOT short-circuit — the model
        asks which role (and the two-turn check_dependencies split applies)."""
        self.assertIsNone(agent._shortcircuit_change_request("draft a change request"))
        self.assertIsNone(
            agent._shortcircuit_change_request("I'd like to create a formal change request please")
        )

    def test_tool_error_surfaces_cleanly(self):
        with patch.object(agent, "invoke_tool", return_value={"error": "NoSuchEntity"}), \
                patch.object(agent, "converse_with_tools") as converse:
            response = agent.handler(
                self._event("generate a change request for MissingRole"), None
            )
        converse.assert_not_called()
        body = json.loads(response["body"])
        self.assertEqual(response["statusCode"], 200)
        self.assertIn("NoSuchEntity", body["response"])
        self.assertEqual(body["usage"], {"inputTokens": 0, "outputTokens": 0})

    def test_cr_export_classifies_as_change_request(self):
        """The rendered document must classify as change_request so a later
        'export that' lands in change-request-*.md (the recent filename fix),
        not blast-radius-*.md — the CR quotes blast-radius context."""
        doc = agent._render_change_request("CloudSecAuditRole", _sample_dependencies())
        content_type, fmt = agent._infer_export_content_type(doc)
        self.assertEqual(content_type, "change_request")
        self.assertEqual(fmt, "md")

    def test_zero_dependents_low_blast_radius_wording(self):
        doc = agent._render_change_request(
            "LonelyRole", _sample_dependencies(level="LOW", dependents=0)
        )
        self.assertIn("No direct dependents", doc)
        self.assertIn("LonelyRole", doc)


if __name__ == "__main__":
    unittest.main()
