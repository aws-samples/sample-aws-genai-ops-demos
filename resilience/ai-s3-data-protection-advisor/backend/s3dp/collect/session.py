"""The session each scanned account is read with: the hub's own, or the spoke role assumed from it. Credentials stay
in memory: callers assume again in every step and never return, log or store them (step results are checkpoints)."""
import boto3
from botocore.exceptions import BotoCoreError, ClientError


def _code(e):
    return e.response.get("Error", {}).get("Code", "Unknown") if isinstance(e, ClientError) else type(e).__name__


def for_account(hub_session, account_id, hub, role_name):
    """(session, None), or (None, error code) when the spoke role cannot be assumed."""
    if hub:
        return hub_session, None
    region = hub_session.region_name
    try:
        partition = hub_session.get_partition_for_region(region)
        creds = hub_session.client("sts", region_name=region).assume_role(
            RoleArn=f"arn:{partition}:iam::{account_id}:role/{role_name}",
            RoleSessionName="s3-data-protection-scan", DurationSeconds=3600)["Credentials"]
    except (ClientError, BotoCoreError) as e:
        return None, _code(e)
    return boto3.Session(aws_access_key_id=creds["AccessKeyId"], aws_secret_access_key=creds["SecretAccessKey"],
                         aws_session_token=creds["SessionToken"], region_name=region), None
