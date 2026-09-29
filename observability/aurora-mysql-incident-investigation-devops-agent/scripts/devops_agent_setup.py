#!/usr/bin/env python3
"""
devops_agent_setup.py — Hands-free Amazon DevOps Agent setup for the Aurora demo.

Creates (or reuses, idempotently) everything the DevOps Agent side of the demo
needs, using the boto3 `devops-agent` API directly:

  1. An Agent Space (default name: aurora-demo)
  2. Two IAM roles:
       - <space>-AgentSpaceRole  (monitoring role, AIDevOpsAgentAccessPolicy)
       - <space>-OperatorRole    (web-console role, AIDevOpsOperatorAppAccessPolicy)
  3. The Operator App (IAM auth) for the web console
  4. The AWS account associated as a "monitor" cloud source
  5. A generic (HMAC) webhook — the URL + secret alarms are delivered to

The webhook URL + secret are written to `.devops-agent.env` in the demo root so
`deploy-all.sh` / `deploy-all.ps1` can pick them up automatically.

If this build of boto3 does not include the `devops-agent` service (older SDKs),
the script prints the equivalent manual console steps and exits 0 — the demo still
works, the operator just clicks through the console once.

Usage:
    python3 scripts/devops_agent_setup.py [--region us-west-2] [--space-name aurora-demo]
"""
import argparse
import json
import os
import sys
import time

try:
    import boto3
    from botocore.exceptions import ClientError, UnknownServiceError
except ImportError:
    print("ERROR: boto3 is required. Install it with:  pip install boto3", file=sys.stderr)
    sys.exit(1)

AGENT_POLICY = "arn:aws:iam::aws:policy/AIDevOpsAgentAccessPolicy"
OPERATOR_POLICY = "arn:aws:iam::aws:policy/AIDevOpsOperatorAppAccessPolicy"
TRUST_PRINCIPAL = "aidevops.amazonaws.com"


def _err(e):
    if isinstance(e, ClientError):
        return f'{e.response["Error"]["Code"]}: {e.response["Error"]["Message"]}'
    return str(e)


def _print_manual_steps(region, space_name):
    """Fallback when the SDK lacks the devops-agent service."""
    print(
        "\n"
        "-------------------------------------------------------------------\n"
        " This boto3 build does not include the DevOps Agent API, so the\n"
        " Agent Space and webhook must be created once in the console:\n"
        "-------------------------------------------------------------------\n"
        f"  1. Open: https://{region}.console.aws.amazon.com/aidevops/home?region={region}\n"
        f"  2. Create an Agent Space named '{space_name}' and connect this AWS account.\n"
        "  3. Capabilities > Webhook > Add > Next > 'Generate URL and secret key'.\n"
        "  4. Copy the webhook URL + secret, then deploy the demo with them:\n"
        "       ./deploy-all.sh --key-file <path> \\\n"
        "           --webhook-url '<URL>' --webhook-secret '<SECRET>'\n"
        "-------------------------------------------------------------------\n"
    )


def ensure_role(iam, name, policy_arn, trust_doc):
    try:
        arn = iam.get_role(RoleName=name)["Role"]["Arn"]
        print(f"  IAM role exists: {name}")
    except ClientError:
        arn = iam.create_role(
            RoleName=name,
            AssumeRolePolicyDocument=trust_doc,
            Description="Amazon DevOps Agent role for the Aurora MySQL demo",
        )["Role"]["Arn"]
        print(f"  IAM role created: {name}")
    try:
        iam.attach_role_policy(RoleName=name, PolicyArn=policy_arn)
    except ClientError as e:
        print(f"    (attach policy: {_err(e)})")
    return arn


def main():
    ap = argparse.ArgumentParser(description="Set up the DevOps Agent side of the Aurora demo.")
    ap.add_argument("--region", default=os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION"))
    ap.add_argument("--space-name", default="aurora-demo")
    ap.add_argument("--env-file", default=None, help="Where to write webhook env (default: <demo>/.devops-agent.env)")
    ap.add_argument("--teardown", action="store_true", help="Delete the Agent Space + IAM roles instead of creating them")
    args = ap.parse_args()

    region = args.region
    if not region:
        try:
            region = boto3.session.Session().region_name
        except Exception:
            region = None
    if not region:
        print("ERROR: region required (pass --region or set AWS_REGION).", file=sys.stderr)
        sys.exit(1)

    space_name = args.space_name
    # Default env file lives in the demo root (parent of this scripts/ dir).
    env_file = args.env_file or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".devops-agent.env"
    )

    print(f"=== Aurora DevOps Agent setup ({region}, space '{space_name}') ===\n")

    try:
        da = boto3.client("devops-agent", region_name=region)
    except UnknownServiceError:
        _print_manual_steps(region, space_name)
        sys.exit(0)

    iam = boto3.client("iam")
    try:
        account_id = boto3.client("sts").get_caller_identity()["Account"]
    except ClientError as e:
        print(f"ERROR: cannot resolve account (credentials expired?): {_err(e)}", file=sys.stderr)
        sys.exit(1)

    # ---- Teardown mode: delete the Agent Space + IAM roles, then exit ----
    if args.teardown:
        target = None
        for s in da.list_agent_spaces().get("agentSpaces", []):
            if s.get("name") == space_name:
                target = s["agentSpaceId"]
        if target:
            for a in da.list_associations(agentSpaceId=target).get("associations", []):
                try:
                    da.disassociate_service(agentSpaceId=target, associationId=a["associationId"])
                except ClientError:
                    pass
            try:
                da.delete_agent_space(agentSpaceId=target)
                print(f"Deleted Agent Space '{space_name}' ({target}).")
            except ClientError as e:
                print(f"delete_agent_space: {_err(e)}")
        else:
            print(f"No Agent Space named '{space_name}' in {region}.")
        for rn in (f"{space_name}-AgentSpaceRole", f"{space_name}-OperatorRole"):
            try:
                for p in iam.list_attached_role_policies(RoleName=rn).get("AttachedPolicies", []):
                    iam.detach_role_policy(RoleName=rn, PolicyArn=p["PolicyArn"])
                iam.delete_role(RoleName=rn)
                print(f"Deleted IAM role {rn}.")
            except ClientError as e:
                print(f"  ({rn}: {_err(e)})")
        if os.path.exists(env_file):
            os.remove(env_file)
            print("Removed .devops-agent.env")
        return

    # If we already saved a webhook for an existing space, reuse it as-is.
    saved = {}
    if os.path.exists(env_file):
        for line in open(env_file):
            line = line.strip()
            if line.startswith("export "):
                line = line[len("export "):]
            if "=" in line:
                k, v = line.split("=", 1)
                saved[k.strip()] = v.strip().strip("'\"")

    # ---- 1. Agent Space (create or reuse by name) ----
    space_id = None
    try:
        for s in da.list_agent_spaces().get("agentSpaces", []):
            if s.get("name") == space_name:
                space_id = s["agentSpaceId"]
    except ClientError as e:
        print(f"ERROR listing agent spaces: {_err(e)}", file=sys.stderr)
        sys.exit(1)

    if space_id:
        print(f"[1/5] Agent Space exists: {space_id}")
    else:
        space_id = da.create_agent_space(
            name=space_name,
            description="Aurora MySQL incident investigation demo",
        )["agentSpace"]["agentSpaceId"]
        print(f"[1/5] Agent Space created: {space_id}")
        time.sleep(8)

    # ---- 2. IAM roles ----
    print("[2/5] IAM roles...")
    trust = json.dumps({
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": TRUST_PRINCIPAL},
            "Action": ["sts:AssumeRole", "sts:TagSession"],
            "Condition": {"StringEquals": {"aws:SourceAccount": account_id}},
        }],
    })
    agent_role = ensure_role(iam, f"{space_name}-AgentSpaceRole", AGENT_POLICY, trust)
    operator_role = ensure_role(iam, f"{space_name}-OperatorRole", OPERATOR_POLICY, trust)
    print("      waiting for IAM propagation...")
    time.sleep(12)

    # ---- 3. Operator App (web console, IAM auth) ----
    try:
        da.enable_operator_app(agentSpaceId=space_id, authFlow="iam", operatorAppRoleArn=operator_role)
        print("[3/5] Operator App enabled (IAM auth).")
    except ClientError as e:
        print(f"[3/5] Operator App: {_err(e)} (continuing)")

    # ---- 4. Associate AWS account (monitor) ----
    assocs = da.list_associations(agentSpaceId=space_id).get("associations", [])
    if any(a.get("serviceId") == "aws" for a in assocs):
        print("[4/5] AWS account already connected (monitor).")
    else:
        try:
            da.associate_service(
                agentSpaceId=space_id,
                serviceId="aws",
                configuration={"aws": {
                    "accountId": account_id,
                    "accountType": "monitor",
                    "assumableRoleArn": agent_role,
                }},
            )
            print(f"[4/5] AWS account {account_id} connected (monitor).")
        except ClientError as e:
            print(f"[4/5] associate AWS account: {_err(e)} (you may need to add it in the console)")

    # ---- 5. Generic (HMAC) webhook ----
    webhook_url = webhook_secret = None
    # Reuse a previously captured webhook if the space matches (the secret is only
    # returned once at creation and cannot be re-fetched).
    if saved.get("WEBHOOK_URL") and saved.get("WEBHOOK_SECRET") and saved.get("DEVOPS_AGENT_SPACE_ID") == space_id:
        webhook_url = saved["WEBHOOK_URL"]
        webhook_secret = saved["WEBHOOK_SECRET"]
        print("[5/5] Reusing existing webhook from .devops-agent.env.")
    else:
        # Remove any stale event-channel associations so we get a fresh, known secret.
        for a in da.list_associations(agentSpaceId=space_id).get("associations", []):
            if a.get("serviceId") != "aws":
                try:
                    da.disassociate_service(agentSpaceId=space_id, associationId=a["associationId"])
                except ClientError:
                    pass
        try:
            reg = da.register_service(
                service="eventChannel",
                serviceDetails={"eventChannel": {"type": "webhook"}},
                name=f"{space_name}-webhook",
            )
            out = da.associate_service(
                agentSpaceId=space_id,
                serviceId=reg["serviceId"],
                configuration={"eventChannel": {}},
            )
            wh = out.get("webhook") or {}
            webhook_url = wh.get("webhookUrl")
            webhook_secret = wh.get("webhookSecret")
            print(f"[5/5] Webhook created (type={wh.get('webhookType')}).")
        except ClientError as e:
            print(f"[5/5] webhook: {_err(e)}", file=sys.stderr)

    # ---- Persist for deploy-all ----
    if webhook_url and webhook_secret:
        with open(env_file, "w") as f:
            f.write(f"export DEVOPS_AGENT_REGION={region}\n")
            f.write(f"export DEVOPS_AGENT_SPACE_ID={space_id}\n")
            f.write(f"export WEBHOOK_URL='{webhook_url}'\n")
            f.write(f"export WEBHOOK_SECRET='{webhook_secret}'\n")
        try:
            os.chmod(env_file, 0o600)
        except OSError:
            pass

    console = f"https://{region}.console.aws.amazon.com/aidevops/home?region={region}#/agent-spaces/{space_id}"
    print("\n=================== DevOps Agent ready ===================")
    print(f"  Agent Space : {space_name} ({space_id})")
    print(f"  Console     : {console}")
    print(f"  Account     : {account_id} (monitored)")
    if webhook_url:
        print(f"  Webhook URL : {webhook_url}")
        print(f"  Secret      : (saved to {os.path.basename(env_file)})")
        print("\n  Next: deploy the demo — it auto-loads the webhook from .devops-agent.env:")
        print("    ./deploy-all.sh --key-file <path-to-your-key.pem>")
    else:
        print("  Webhook     : NOT created — see errors above.")
    print("==========================================================")


if __name__ == "__main__":
    main()
