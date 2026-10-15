"""Regression test: app.py's hubAccountId context override (#173 Level 2).

Catches the exact bug found during live deployment: when the Organizations
MANAGEMENT account deploys the Org stack on behalf of a hub account that
cannot roll out service-managed StackSets itself (deploy-all.sh's printed
fallback command for that case), app.py must trust the real hub account in
the spoke role's trust policy — NOT the management account that happened
to run `cdk deploy`. Without the hubAccountId context value, OrgStack's
hub_account_id defaulted to CDK_DEFAULT_ACCOUNT (the deploying account),
producing a spoke role trust policy that pinned to the WRONG account and
that nothing could ever assume — silently broken, no error at deploy time.

This runs app.py as a real subprocess (`python app.py` is not how CDK
normally invokes it, so this test calls `cdk synth` via the npx CDK CLI
installed in this directory's own virtualenv) because app.py has
module-level side effects (`app.synth()` runs at import time) that make it
impractical to import and inspect in-process the way the other CDK stack
tests in this directory do.

Requires: npm/npx and the venv's own installed CDK CLI (same prerequisite
as running deploy-all.sh itself). Skipped automatically if npx is not on
PATH, so this doesn't block a plain `pytest` run in an environment that
hasn't set up the CDK toolchain.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

_CDK_DIR = Path(__file__).resolve().parents[1]
_HUB_ACCOUNT = "661487690263"  # the real hub (e.g. the Security account)
_OU_ID = "ou-ilhk-kbqds89c"
_STACK = "IamAnalyzerAssistantOrg-us-east-1"
_ENV_RE = re.compile(r"^aws://(\d{12})/")


@unittest.skipUnless(shutil.which("npx"), "npx not on PATH — skipping CDK subprocess test")
class HubAccountIdContextTest(unittest.TestCase):
    def _synth_org_stack(self, *, hub_account_id_context: str | None) -> tuple[dict, str]:
        """Run `cdk synth` for the Org stack, optionally overriding
        hubAccountId via context. Returns (parsed CloudFormation template,
        deploying account id).

        The deploying account is read back from the synthesized
        manifest's stack `environment` (``aws://<account>/<region>``)
        rather than assumed: the CDK CLI resolves the deploying account
        itself from the ambient AWS credentials and sets
        CDK_DEFAULT_ACCOUNT accordingly before spawning app.py — it
        deliberately ignores any CDK_DEFAULT_ACCOUNT we pre-set in the
        subprocess env, so a test cannot pin the deployer to a chosen
        account that way. We therefore read whichever account the CLI
        actually resolved and assert app.py's defaulting relative to
        THAT, which is the real contract app.py must honor."""
        with tempfile.TemporaryDirectory() as out_dir:
            # Override cdk.json's `app` command to use THIS package's venv
            # Python explicitly. cdk.json uses a bare `python3 app.py` (the
            # deploy path activates the venv first, via the shared
            # deploy-cdk script, so bare python3 resolves correctly there).
            # This test subprocess does NOT activate the venv, so without
            # the override `npx cdk synth` would spawn a system python3 that
            # lacks aws_cdk. Point --app at the venv interpreter so the
            # synth is self-contained regardless of the ambient python3.
            venv_python = _CDK_DIR / ".venv" / "bin" / "python3"
            app_cmd = f"{venv_python} app.py" if venv_python.exists() else "python3 app.py"
            cmd = [
                "npx", "cdk", "synth", _STACK,
                "--app", app_cmd,
                "--context", f"orgTargets={_OU_ID}",
                "--output", out_dir,
            ]
            if hub_account_id_context:
                cmd += ["--context", f"hubAccountId={hub_account_id_context}"]

            # The CDK app imports the repo-root `shared` package (upstream
            # #207 moved shared utilities there), and the deploy path sets
            # PYTHONPATH=<repo root> via the shared deploy-cdk script. This
            # synth subprocess must do the same so `import shared...`
            # resolves. Repo root is four levels up from this cdk dir
            # (security/<demo>/infrastructure/cdk -> repo root).
            repo_root = _CDK_DIR.parents[3]
            env = {
                **os.environ,
                "AWS_REGION": "us-east-1",
                "PYTHONPATH": str(repo_root)
                + os.pathsep
                + os.environ.get("PYTHONPATH", ""),
            }
            result = subprocess.run(
                cmd, cwd=str(_CDK_DIR), env=env,
                capture_output=True, text=True, timeout=120,
            )
            self.assertEqual(
                0, result.returncode,
                msg=f"cdk synth failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}",
            )
            template_path = Path(out_dir) / f"{_STACK}.template.json"
            template = json.loads(template_path.read_text())
            manifest = json.loads((Path(out_dir) / "manifest.json").read_text())
            environment = manifest["artifacts"][_STACK]["environment"]
            match = _ENV_RE.match(environment)
            if match is None:
                # The CDK CLI couldn't resolve a concrete 12-digit
                # deploying account (e.g. no/expired AWS credentials, so
                # the env is left account-agnostic). Without a known
                # deployer there's nothing to assert the default against,
                # so skip rather than report a false failure.
                self.skipTest(
                    "CDK CLI did not resolve a concrete deploying account "
                    f"(environment={environment!r}); need AWS credentials"
                )
            deploying_account = match.group(1)
            return template, deploying_account

    def _spoke_role_trust_principal_account(self, template: dict) -> str:
        stack_set = next(
            r for r in template["Resources"].values()
            if r["Type"] == "AWS::CloudFormation::StackSet"
        )
        embedded = json.loads(stack_set["Properties"]["TemplateBody"])
        role = next(
            r for r in embedded["Resources"].values() if r["Type"] == "AWS::IAM::Role"
        )
        join_parts = role["Properties"]["AssumeRolePolicyDocument"]["Statement"][0][
            "Condition"
        ]["ArnEquals"]["aws:PrincipalArn"]["Fn::Join"][1]
        # join_parts looks like ["arn:", {"Ref": "AWS::Partition"}, ":iam::<account>:role/<name>"]
        account_segment = join_parts[-1]
        return account_segment.split(":iam::")[1].split(":role/")[0]

    def test_without_hub_account_id_context_defaults_to_deploying_account(self):
        """Documents the DEFAULT behavior (hub deploys its own Org stack):
        with no override, hub_account_id falls back to the deploying
        account. This is CORRECT when the hub deploys its own StackSet —
        the bug only appears when a DIFFERENT account deploys on its
        behalf, covered by the next test.

        We assert the spoke trust account equals whatever account the
        CDK CLI resolved as the deployer (read from the manifest), not a
        hardcoded value: the CLI picks the deployer from the ambient AWS
        credentials and we can't force it from the test env. Equality
        here proves `hub_account_id = try_get_context("hubAccountId") or
        account` falls through to `account` (the deployer) when no
        override is given."""
        template, deploying_account = self._synth_org_stack(hub_account_id_context=None)
        trusted_account = self._spoke_role_trust_principal_account(template)
        self.assertEqual(
            deploying_account, trusted_account,
            msg=(
                "With no hubAccountId override, the spoke role must trust "
                "the deploying account (CDK_DEFAULT_ACCOUNT). It trusted "
                f"{trusted_account} but the CLI deployed as "
                f"{deploying_account}."
            ),
        )

    def test_with_hub_account_id_context_trusts_the_real_hub_not_the_deployer(self):
        """The actual regression: a management/delegated-admin account runs
        `cdk deploy` on behalf of a hub (661487690263) that cannot roll
        out StackSets itself. The spoke role MUST trust the real hub's
        tool role, not the deployer's — found via live deployment against
        a real AWS Organization before this test was written.

        This is the case that exercises the fix directly: `--context
        hubAccountId=...` is honored by the CDK CLI (unlike
        CDK_DEFAULT_ACCOUNT), so we CAN pin the hub here and prove the
        trust policy follows the override rather than the deployer."""
        template, deploying_account = self._synth_org_stack(
            hub_account_id_context=_HUB_ACCOUNT
        )
        trusted_account = self._spoke_role_trust_principal_account(template)
        self.assertEqual(
            _HUB_ACCOUNT, trusted_account,
            msg=(
                "Spoke role trust policy pinned to the WRONG account. "
                "app.py must read a separate `hubAccountId` CDK context "
                "value, not just default to CDK_DEFAULT_ACCOUNT (the "
                "deploying account), when the two differ."
            ),
        )
        # Only meaningful when the deployer differs from the hub (the real
        # scenario). If someone runs the suite while logged into the hub
        # account itself, the two legitimately coincide; skip the
        # inequality rather than fail on a false positive.
        if deploying_account != _HUB_ACCOUNT:
            self.assertNotEqual(deploying_account, trusted_account)


if __name__ == "__main__":
    unittest.main()
