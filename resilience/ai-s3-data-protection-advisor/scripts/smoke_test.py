#!/usr/bin/env python3
"""End-to-end smoke test for a deployed S3 data protection advisor.

Standalone: python3, boto3 and urllib only (s3dp is imported from ../backend for --seed-check). Creates a temporary
Cognito user with a random password held in memory only, runs a scan against the account, checks the reports, and
always deletes the user. Exit 0 only when every check passed.
"""
import argparse
import json
import secrets
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import boto3

STACKS = {"data": "S3DataProtectionData-{r}", "auth": "S3DataProtectionAuth-{r}",
          "api": "S3DataProtectionApi-{r}", "frontend": "S3DataProtectionFrontend-{r}"}
results = []


def report(name, ok, detail=""):
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f": {detail}" if detail else ""), flush=True)
    return ok


def http(method, url, token=None, body=None, timeout=30):
    """Returns (status, headers, bytes); HTTP errors return their status instead of raising."""
    headers = {}
    if token:
        headers["Authorization"] = token
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


def jsonbody(raw):
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def outputs(cf, stack):
    out = cf.describe_stacks(StackName=stack)["Stacks"][0].get("Outputs", [])
    return {o["OutputKey"]: o["OutputValue"] for o in out}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--region", required=True)
    ap.add_argument("--profile")
    ap.add_argument("--seed-check", action="store_true", help="compare the scan inventory with the mock estate")
    ap.add_argument("--timeout-minutes", type=int, default=30)
    args = ap.parse_args()

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    cf = session.client("cloudformation")
    names = {k: v.format(r=args.region) for k, v in STACKS.items()}
    out = {k: outputs(cf, v) for k, v in names.items()}
    site = out["frontend"]["WebsiteUrl"].rstrip("/")
    pool, client_id = out["auth"]["UserPoolId"], out["auth"]["UserPoolClientId"]
    bucket = out["data"]["ReportsBucket"]
    idp = session.client("cognito-idp")

    # 1. the authorizer rejects an anonymous caller
    status, _, raw = http("GET", site + "/api/scans")
    body = jsonbody(raw)
    report("anonymous GET /api/scans is rejected by the authorizer",
           status == 401 and body == {"message": "Unauthorized"}, f"status {status}")

    # 2. the site root and its CSP
    status, headers, _ = http("GET", site + "/")
    csp = headers.get("content-security-policy") or ""
    report("GET / returns 200 with a content security policy",
           status == 200 and "default-src 'self'" in csp, f"status {status}")

    # 3. the runtime config
    status, _, raw = http("GET", site + "/config.json")
    cfg = jsonbody(raw) or {}
    report("GET /config.json has region, userPoolId, userPoolClientId",
           status == 200 and all(cfg.get(k) for k in ("region", "userPoolId", "userPoolClientId")), f"status {status}")

    username = f"smoke-{secrets.token_hex(4)}@example.com"
    created = False
    try:
        # 4. temporary user and token
        token = None
        try:
            password = secrets.token_urlsafe(24)[:24] + "aA1!"   # held in memory only
            idp.admin_create_user(UserPoolId=pool, Username=username, MessageAction="SUPPRESS",
                                  UserAttributes=[{"Name": "email", "Value": username},
                                                  {"Name": "email_verified", "Value": "true"}])
            created = True
            idp.admin_set_user_password(UserPoolId=pool, Username=username, Password=password, Permanent=True)
            auth = idp.initiate_auth(ClientId=client_id, AuthFlow="USER_PASSWORD_AUTH",
                                     AuthParameters={"USERNAME": username, "PASSWORD": password})
            token = auth["AuthenticationResult"]["IdToken"]
            del password
            report("temporary user signs in", True, username)
        except Exception as e:
            report("temporary user signs in", False, type(e).__name__)
        if not token:
            return finish()

        # 5. start a scan, or attach to the running one
        status, _, raw = http("POST", site + "/api/scans", token=token, body={})
        body = jsonbody(raw) or {}
        run_id = body.get("run_id")
        report("POST /api/scans returns 202 or 409", status in (202, 409) and bool(run_id), f"status {status}")
        if not run_id:
            return finish()
        print(f"      run id {run_id}", flush=True)

        # 6. poll until the run finishes
        deadline = time.time() + args.timeout_minutes * 60
        seen, run_status = {}, None
        while time.time() < deadline:
            status, _, raw = http("GET", f"{site}/api/scans/{run_id}", token=token)
            body = jsonbody(raw) or {}
            for s in body.get("steps", []):
                if seen.get(s["label"]) != s["status"]:
                    seen[s["label"]] = s["status"]
                    print(f"      {s['label']}: {s['status']}", flush=True)
            run_status = (body.get("run") or {}).get("status")
            if run_status in ("SUCCEEDED", "FAILED"):
                break
            time.sleep(15)
        report("scan finishes", run_status == "SUCCEEDED", run_status or "timed out")
        if run_status != "SUCCEEDED":
            return finish()

        # 7. the three reports, through presigned URLs (no token sent to S3)
        for fmt in ("json", "xlsx", "html"):
            status, _, raw = http("GET", f"{site}/api/scans/{run_id}/report?format={fmt}", token=token)
            url = (jsonbody(raw) or {}).get("url")
            got, size = None, 0
            if status == 200 and url:
                got, _, data = http("GET", url)
                size = len(data)
            report(f"report {fmt}", status == 200 and got == 200 and size > 0, f"api {status}, file {got}, {size} bytes")

        # 8. the bundled sample
        status, _, raw = http("GET", site + "/api/sample", token=token)
        meta = (jsonbody(raw) or {}).get("meta") or {}
        report("GET /api/sample is the bundled sample", status == 200 and meta.get("sample") is True, f"status {status}")

        # 9. the mock estate matches what the scan found
        if args.seed_check:
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
            from s3dp import seed
            obj = session.client("s3").get_object(Bucket=bucket, Key=f"runs/{run_id}/inventory.json")
            rows, ok = seed.check(json.loads(obj["Body"].read()))
            for row in rows:
                if row[4] != "ok":
                    print("      " + " | ".join(str(c) for c in row), flush=True)
            report("seed check", ok, f"{sum(1 for r in rows if r[4] != 'ok')} mismatches")
    except Exception as e:
        report("smoke test ran to the end", False, type(e).__name__)
    finally:
        if created:
            try:
                idp.admin_delete_user(UserPoolId=pool, Username=username)
                print(f"      deleted {username}", flush=True)
            except Exception as e:
                print(f"      could not delete {username}: {type(e).__name__}", flush=True)
    return finish()


def finish():
    ok = bool(results) and all(results)
    print("All checks passed." if ok else "Some checks failed.", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
