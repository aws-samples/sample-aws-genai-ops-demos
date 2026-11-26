"""Local runner. `python -m s3dp.cli scan --out DIR [--region R] [--profile P] [--no-ai]` or
`python -m s3dp.cli sample [--out FILE]`.

Sandbox only, writes to AWS: `seed` creates the mock estate (see s3dp/seed.py), `seed-check --out DIR` compares a
scan of it with what each mock bucket should show, `seed-cleanup` deletes it.

The scan is read-only and uses the caller's credentials. The region (--region, else the session's region from
AWS_REGION / AWS_DEFAULT_REGION or the profile) is the Amazon Bedrock region and the region used to resolve the
partition (Cost Explorer endpoint)."""
import argparse
import json
import os
from pathlib import Path

import boto3

from s3dp import run, seed as mock
from s3dp.ai import bedrock
from s3dp.collect.scan import collect_account
from s3dp.engine import scoring

ROOT = Path(__file__).resolve().parents[2]
SAMPLE_OUT = ROOT / "sample-data" / "anycompany-data.json"
NOVA_LITE = "amazon.nova-lite-v1:0"
# Amazon Nova Lite geographic inference profiles (there is no global profile). First matching prefix wins.
NOVA_LITE_GEO = (("us-gov-", None), ("us-", "us"), ("ca-", "ca"), ("eu-", "eu"), ("il-", "eu"), ("ap-", "apac"))


def model_for(region):
    """MODEL_ID when set (plan 2: computed by CDK), else the Amazon Nova Lite inference profile of the region's
    geography, or None when no profile is known for the region."""
    if os.environ.get("MODEL_ID"):
        return os.environ["MODEL_ID"]
    geo = next((g for prefix, g in NOVA_LITE_GEO if region.startswith(prefix)), None)
    return f"{geo}.{NOVA_LITE}" if geo else None


def write(data, out):
    out.mkdir(parents=True, exist_ok=True)
    for name, content in zip(("data.json", "report.xlsx", "report.html"), run.render(data)):
        (out / name).write_bytes(content)


def scan(args):
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    region = session.region_name
    if not region:
        raise SystemExit("Set a region with --region, AWS_REGION / AWS_DEFAULT_REGION or the profile's region")
    model = None
    if not args.no_ai:
        model = model_for(region)
        if not model:
            raise SystemExit(f"No Amazon Nova Lite inference profile known for {region}: set MODEL_ID or use --no-ai")
    account_id = session.client("sts").get_caller_identity()["Account"]
    raw = collect_account(session, account_id)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "raw.json").write_text(json.dumps(raw, indent=1, default=str))
    client = None
    if model:
        client = bedrock.client(session)
    ai_roles, ai_state = run.classify_roles(raw, client, model)
    if ai_state["narrative"] == "unavailable":
        print(f"Amazon Bedrock is not available ({ai_state['reason']}): continuing with a rules-only report")
    inv, data, stats = run.score(raw, ai_roles, ai_state)
    (out / "inventory.json").write_text(json.dumps(inv, indent=1, default=str))
    run.narrate(inv, data, client, model, ai_state)
    write(data, out)
    print(json.dumps(stats, default=str))
    print(out / "report.html")


def sample(args):
    inv = json.loads((ROOT / "sample-data" / "anycompany-inventory.json").read_text())
    data, _ = scoring.build(inv)
    data["meta"]["sample"] = True
    path = Path(args.out)
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    print(path)


def _session(args):
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    if not session.region_name:
        raise SystemExit("Set a region with --region, AWS_REGION / AWS_DEFAULT_REGION or the profile's region")
    return session, session.client("sts").get_caller_identity()["Account"]


def _confirm(args, question):
    if not args.yes and input(f"{question} [y/N] ").strip().lower() != "y":
        raise SystemExit("Cancelled")


def seed(args):
    session, account_id = _session(args)
    mock.check_account(next(iter(session.client("iam").list_account_aliases().get("AccountAliases", [])), None))
    primary = session.region_name
    secondary = args.second_region or mock.default_secondary(primary)
    for r in (primary, secondary):
        mock.check_partition(session, r)
    planned = mock.plan(account_id, primary, secondary)
    print("\n".join(f"  {n} ({r})" for n, r, _ in planned))
    _confirm(args, f"Create or update these {len(planned)} private buckets and IAM role {mock.ROLE_NAME} "
                   f"in account {account_id}?")
    mock.seed(session, account_id, primary, secondary)
    print("Mock estate ready. Storage sizes appear in scans one to two days after seeding (daily CloudWatch "
          "metrics). Next: scan, then seed-check --out <scan folder>; seed-cleanup removes everything.")


def seed_check(args):
    inv = json.loads((Path(args.out) / "inventory.json").read_text())
    rows, ok = mock.check(inv)
    for r in rows:
        if r[4] != "ok" or args.verbose:
            print(f"{r[4]:8} {r[0]:17} {r[1]:32} expected {r[2]!r}, got {r[3]!r}")
    print(f"{sum(r[4] == 'ok' for r in rows)}/{len(rows)} checks match the mock estate")
    if not ok:
        raise SystemExit(1)


def seed_cleanup(args):
    session, account_id = _session(args)
    buckets = mock.mock_buckets(session, account_id)
    print("\n".join(f"  {n} ({r})" for n, r in buckets) or "  no mock buckets")
    _confirm(args, f"Delete these {len(buckets)} buckets, all their objects, and IAM role {mock.ROLE_NAME} "
                   f"(if tagged {mock.TAG_KEY}={mock.TAG_VALUE})?")
    mock.cleanup(session, account_id, buckets)


def main(argv=None):
    p = argparse.ArgumentParser(prog="s3dp")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan", help="scan the current account (read-only)")
    s.add_argument("--out", required=True)
    s.add_argument("--profile")
    s.add_argument("--region", help="Amazon Bedrock region; defaults to the session region (AWS_REGION or profile)")
    s.add_argument("--no-ai", action="store_true", help="skip Amazon Bedrock (rules only)")
    s.set_defaults(fn=scan)
    sm = sub.add_parser("sample", help="rebuild the bundled sample report")
    sm.add_argument("--out", default=str(SAMPLE_OUT), help="output file (default: the bundled sample-data copy)")
    sm.set_defaults(fn=sample)
    sd = sub.add_parser("seed", help="sandbox only: create the mock estate (writes to AWS)")
    sd.add_argument("--profile")
    sd.add_argument("--region", help="primary region; defaults to the session region")
    sd.add_argument("--second-region", help="region of the replica and twin buckets (default us-west-2, "
                                            "or us-east-1 when the primary is us-west-2)")
    sd.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    sd.set_defaults(fn=seed)
    sc = sub.add_parser("seed-check", help="compare a scan of the mock estate with the expected results")
    sc.add_argument("--out", required=True, help="scan output folder (contains inventory.json)")
    sc.add_argument("--verbose", action="store_true", help="also list matching checks")
    sc.set_defaults(fn=seed_check)
    cl = sub.add_parser("seed-cleanup", help="delete the mock estate (tagged buckets and role only)")
    cl.add_argument("--profile")
    cl.add_argument("--region")
    cl.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    cl.set_defaults(fn=seed_cleanup)
    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
