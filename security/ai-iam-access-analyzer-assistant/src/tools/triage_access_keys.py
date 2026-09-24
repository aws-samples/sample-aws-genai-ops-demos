"""Tool: Triage IAM access keys by risk, staleness, and identity pattern.

Inventories every IAM access key in the deployment account and returns a
ranked, per-key report — which keys are admin-privileged, which are stale,
which are over-permissioned, which are unrotated, and (most usefully) what
specific remediation applies to each based on the identity's usage pattern.

The reference document at ``src/tools/references/access_key_analysis_criteria.md``
is the source of truth for the risk-flag taxonomy, prioritization ladder,
and identity-pattern remediation mapping — if the code and doc drift, one
is a bug.

The model presents this tool's output; it does NOT re-rank or substitute a
different remediation. See the ``ACCESS KEY TRIAGE`` section of the agent
system prompt for the presentation rules.
"""

import json
import logging
import re
import urllib.parse
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Module-level clients so warm invocations re-use them (Lambda cold-start
# optimization pattern used by every other tool in this package). Tests
# swap this reference in setUp/tearDown.
iam_client = boto3.client("iam")

# --- Constants --------------------------------------------------------------

# Any Allow statement granting Action:*, or an AWS-managed AdministratorAccess
# attachment, flips the ADMIN flag.
ADMIN_POLICIES = frozenset({"AdministratorAccess"})

# AWS-managed broad-scope policies. Presence of any of these in the resolved
# managed-policy set adds a BROAD:<policy> flag. See
# ``src/tools/references/access_key_analysis_criteria.md`` for definitions.
BROAD_MANAGED = frozenset({
    "AmazonBedrockFullAccess",
    "AmazonS3FullAccess",
    "AmazonAthenaFullAccess",
    "PowerUserAccess",
    "IAMFullAccess",
    "AmazonEC2FullAccess",
    "ReadOnlyAccess",
    "AmazonDynamoDBFullAccess",
    "AWSLambda_FullAccess",
})

# Safety-rule advisory the assistant quotes in prose so operators do not act
# on a "looks idle" reading alone. Kept short so the model can drop it into a
# sentence without paraphrasing away the caveat.
USAGE_LAG_CAVEAT = (
    "Last-used data can lag by hours, and a low-frequency workload may back "
    "a rare-but-critical job that appears idle. Verify with the resource "
    "owner before recommending any removal."
)

# Regex patterns for the identity-pattern → remediation mapping (DD-7).
# Order matters: the first match wins. CI/CD is checked BEFORE service
# because names like ``my-lambda-deployer-ci`` should map to OIDC
# federation, not IAM_Role.
_HUMAN_PATTERN = re.compile(
    r"(@)"                                # email
    r"|(\b[a-z]+\.[a-z]+\b)"              # first.last as a segment anywhere
    r"|(?<![a-z])[a-z]+_[a-z]+(?![a-z])", # first_last as a segment anywhere
    re.IGNORECASE,
)
_CICD_PATTERN = re.compile(
    r"(-ci-|-cicd-|-deployer-|-github-actions-|-gitlab-|-jenkins-"
    r"|\bci-|\bcicd-|-ci\b|-cicd\b|-deployer\b|-github-actions\b|-gitlab\b|-jenkins\b)",
    re.IGNORECASE,
)
_SERVICE_PATTERN = re.compile(
    r"(\bsvc-|\bservice-|-lambda-|-eks-|-ecs-|-ec2-"
    r"|-runner\b|-worker\b|-agent\b|-lambda\b|-eks\b|-ecs\b|-ec2\b)",
    re.IGNORECASE,
)


# --- Effective-policy resolution (ported from audit_iam_keys.py) ------------


def _empty() -> dict:
    """Empty aggregate for the effective-policy walk.

    Fields:
      actions      — every Allow action seen (set)
      wild         — actions granted on Resource:* (set)
      any_wild     — any Allow statement is on Resource:*
      any_scoped   — any Allow statement is on a constrained ARN
      cond         — any Allow statement carries a Condition
      blocks       — per-statement ``<actions> [<resources> | if condition]``
                     strings, shown inline in the report so operators see
                     the exact grant that triggered a flag
    """
    return {
        "actions": set(),
        "wild": set(),
        "any_wild": False,
        "any_scoped": False,
        "cond": False,
        "blocks": [],
    }


def _cond_summary(cond) -> str:
    parts = []
    for op, kv in (cond or {}).items():
        if isinstance(kv, dict):
            for k, v in kv.items():
                val = ",".join(map(str, v)) if isinstance(v, list) else str(v)
                parts.append(f"{op} {k}={val}")
        else:
            parts.append(f"{op}={kv}")
    return "; ".join(parts)


def _cap(xs, n: int) -> str:
    """Cap a sorted list to ``n`` entries with a ``…(+N)`` overflow marker."""
    xs = sorted(xs)
    return "+".join(xs[:n]) + (f"…(+{len(xs) - n})" if len(xs) > n else "")


def _fmt_block(actions, resources, cond) -> str:
    s = f"{_cap(actions, 8)} [{_cap(resources or ['*'], 6)}"
    if cond:
        s += " | if " + _cond_summary(cond)
    return s + "]"


def _parse_policy(doc) -> dict:
    """Parse a single policy document (dict or URL-encoded string) into the
    aggregate shape from ``_empty``.
    """
    if isinstance(doc, str):
        doc = json.loads(urllib.parse.unquote(doc))
    out = _empty()
    stmts = doc.get("Statement", [])
    if isinstance(stmts, dict):
        stmts = [stmts]
    for s in stmts:
        if s.get("Effect") != "Allow":
            continue
        actions = s.get("Action", [])
        if isinstance(actions, str):
            actions = [actions]
        out["actions"].update(actions)
        resources = s.get("Resource", [])
        if isinstance(resources, str):
            resources = [resources]
        cond = s.get("Condition")
        if any(r == "*" for r in resources):
            out["any_wild"] = True
            out["wild"].update(actions)
        elif resources:
            out["any_scoped"] = True
        if cond:
            out["cond"] = True
        out["blocks"].append(_fmt_block(actions, resources, cond))
    return out


def _merge(agg: dict, p: dict) -> None:
    """Merge parsed-policy ``p`` into aggregate ``agg`` in place."""
    agg["actions"] |= p["actions"]
    agg["wild"] |= p["wild"]
    agg["any_wild"] = agg["any_wild"] or p["any_wild"]
    agg["any_scoped"] = agg["any_scoped"] or p["any_scoped"]
    agg["cond"] = agg["cond"] or p["cond"]
    for b in p["blocks"]:
        if b not in agg["blocks"]:
            agg["blocks"].append(b)


def _resource_scope(agg: dict) -> str:
    """Compute WILDCARD / SCOPED / MIXED / NONE for the aggregate."""
    if agg["any_wild"] and agg["any_scoped"]:
        return "MIXED"
    if agg["any_wild"]:
        return "WILDCARD"
    if agg["any_scoped"]:
        return "SCOPED"
    return "NONE"


def _resolve_managed(policy_arn: str, cache: dict) -> dict:
    """Resolve an AWS-managed or customer-managed policy to its default version.

    Cached across users in a single tool invocation — a policy attached to
    twenty users is resolved once.
    """
    if policy_arn in cache:
        return cache[policy_arn]
    try:
        policy = iam_client.get_policy(PolicyArn=policy_arn)
        version_id = policy["Policy"]["DefaultVersionId"]
        version = iam_client.get_policy_version(
            PolicyArn=policy_arn, VersionId=version_id
        )
        parsed = _parse_policy(version["PolicyVersion"]["Document"])
    except (ClientError, KeyError):
        parsed = _empty()
    cache[policy_arn] = parsed
    return parsed


def _user_effective(user_name: str, cache: dict) -> tuple:
    """Walk a user's attached-managed + inline + group-attached policies and
    return the aggregate, the set of managed-policy names, and the list of
    source labels (attached / ``inline:<name>`` / ``group:<g>/<name>``).

    Raises the first non-recoverable ClientError to the caller, which flips
    the row into the "policy resolution failed" placeholder shape. Individual
    per-policy fetch failures are absorbed here (a missing / deleted managed
    policy returns an empty parse via ``_resolve_managed``).
    """
    agg = _empty()
    managed: set = set()
    policies: list = []

    # Attached managed policies
    for pol in iam_client.list_attached_user_policies(UserName=user_name).get(
        "AttachedPolicies", []
    ):
        managed.add(pol["PolicyName"])
        policies.append(pol["PolicyName"])
        _merge(agg, _resolve_managed(pol["PolicyArn"], cache))

    # Inline policies
    for name in iam_client.list_user_policies(UserName=user_name).get(
        "PolicyNames", []
    ):
        policies.append("inline:" + name)
        doc = iam_client.get_user_policy(
            UserName=user_name, PolicyName=name
        ).get("PolicyDocument", {})
        _merge(agg, _parse_policy(doc))

    # Group attached + group inline policies
    for group in iam_client.list_groups_for_user(UserName=user_name).get(
        "Groups", []
    ):
        gname = group["GroupName"]
        for pol in iam_client.list_attached_group_policies(GroupName=gname).get(
            "AttachedPolicies", []
        ):
            managed.add(pol["PolicyName"])
            policies.append(f"group:{gname}/{pol['PolicyName']}")
            _merge(agg, _resolve_managed(pol["PolicyArn"], cache))
        for pname in iam_client.list_group_policies(GroupName=gname).get(
            "PolicyNames", []
        ):
            policies.append(f"group:{gname}/inline:{pname}")
            doc = iam_client.get_group_policy(
                GroupName=gname, PolicyName=pname
            ).get("PolicyDocument", {})
            _merge(agg, _parse_policy(doc))

    return agg, managed, policies


# --- Risk flags (ported from audit_iam_keys.py flags_for) -------------------


def _flags_for(
    agg: dict,
    managed: set,
    created: datetime,
    last_used,
    n_active: int,
    last_known: bool,
    now: datetime,
) -> list:
    """Compute the risk_flags list for one access key.

    Mirrors ``audit_iam_keys.py:flags_for`` — same order, same predicates.
    ``LASTUSED_UNKNOWN`` replaces (never joins) ``NEVER_USED`` / ``IDLE_*``
    when the last-used lookup could not run, so a coverage gap is not
    silently reported as "confirmed unused".
    """
    actions = agg["actions"]
    flags: list = []

    if "*" in actions or (managed & ADMIN_POLICIES):
        flags.append("ADMIN")

    broad = managed & BROAD_MANAGED
    if broad:
        flags.append("BROAD:" + "+".join(sorted(broad)))

    services_wild = sorted({a.split(":")[0] for a in actions if a.endswith(":*")})
    if services_wild:
        flags.append("SERVICE_WILDCARD:" + "+".join(services_wild))

    if agg["any_wild"]:
        flags.append("RESOURCE_WILDCARD")

    age_days = (now - created).days
    if age_days >= 365:
        flags.append(f"KEY_AGE_{age_days}d(>1yr)")

    if not last_known:
        flags.append("LASTUSED_UNKNOWN")
    elif last_used is None:
        flags.append("NEVER_USED")
    else:
        idle = (now - last_used).days
        if idle > 90:
            flags.append(f"IDLE_{idle}d")

    if n_active >= 2:
        flags.append("MULTI_ACTIVE_KEYS")

    return flags


# --- Prioritization + remediation mapping (Req 4) ---------------------------


def _priority_class(flags: list, is_root: bool = False) -> str:
    """Assign one of Critical / High / Cleanup / Rotation.

    Root keys are always Critical (Req 4.4). Otherwise the ladder from
    ``analysis-criteria.md`` applies: ADMIN + stale → Critical, ADMIN alone
    → Critical, BROAD/SERVICE_WILDCARD → High, cleanup flags without
    admin/broad → Cleanup, rotation flags alone → Rotation.
    """
    if is_root:
        return "Critical"
    has_admin = "ADMIN" in flags
    has_broad = any(f.startswith("BROAD:") or f.startswith("SERVICE_WILDCARD:") for f in flags)
    has_stale = any(
        f == "NEVER_USED" or f.startswith("IDLE_") or f.startswith("KEY_AGE_")
        for f in flags
    )
    has_rotation_only = any(
        f.startswith("KEY_AGE_") or f == "MULTI_ACTIVE_KEYS" for f in flags
    )
    has_cleanup_only = any(f == "NEVER_USED" or f.startswith("IDLE_") for f in flags)

    if has_admin and has_stale:
        return "Critical"
    if has_admin:
        return "Critical"
    if has_broad:
        return "High"
    if has_cleanup_only:
        return "Cleanup"
    if has_rotation_only:
        return "Rotation"
    return "Rotation"


def _suggested_remediation(user_name: str, is_root: bool = False) -> str:
    """Regex the IAM user name against the identity patterns from DD-7.

    Root keys return ``Remove_Root_Access_Keys`` (Req 4.4) — the special
    case that overrides the four migration paths.

    Order: human → CI/CD → service → default. CI/CD is checked before
    service so ``my-lambda-deployer-ci`` maps to OIDC federation instead
    of IAM_Role.
    """
    if is_root:
        return "Remove_Root_Access_Keys"
    if _HUMAN_PATTERN.search(user_name):
        return "SSO_Federation"
    if _CICD_PATTERN.search(user_name):
        return "OIDC_Federation"
    if _SERVICE_PATTERN.search(user_name):
        return "IAM_Role"
    # Safe default. The Cross_Account_Role_With_External_Id path requires
    # investigation before adoption — the response text calls this out so
    # the operator does not treat the default as a settled recommendation.
    return "Cross_Account_Role_With_External_Id"


# Canonical AWS documentation URL per remediation label. Emitted as
# ``suggested_remediation_url`` on every row so the frontend table and
# the model's prose can both render each recommendation as a clickable
# link into the right docs page — no more "here's what to do but you're
# on your own to find out how."
_REMEDIATION_DOCS = {
    # SSO_Federation is the recommended path for human identities — we
    # link to the AWS "Identity providers and federation" landing page
    # because it covers the three mechanisms a customer might pick
    # (SAML 2.0 with an external IdP like Okta / Entra ID, OIDC, or IAM
    # Identity Center) instead of pointing at just one AWS product.
    "SSO_Federation": "https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_providers.html",
    "IAM_Role": "https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles.html",
    "OIDC_Federation": "https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_providers_oidc.html",
    "IAM_Roles_Anywhere": "https://docs.aws.amazon.com/rolesanywhere/latest/userguide/introduction.html",
    "Cross_Account_Role_With_External_Id": "https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_common-scenarios_third-party.html",
    "Remove_Root_Access_Keys": "https://docs.aws.amazon.com/accounts/latest/reference/root-user-access-key.html",
}


def _remediation_url(label: str) -> str:
    """Return the canonical AWS docs URL for a remediation label, or empty
    string if the label is unrecognized. Never fabricate URLs — an unknown
    label yields an empty string and the frontend renders plain text."""
    return _REMEDIATION_DOCS.get(label, "")


# --- Small helpers ----------------------------------------------------------


def _parse_dt(value):
    """Parse a datetime from ISO string, epoch string, or datetime — or None."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            try:
                return datetime.fromtimestamp(float(value), tz=timezone.utc)
            except ValueError:
                return None
    return None


def _cov(source: str, state: str, detail: str = "", count=None) -> dict:
    entry = {"source": source, "state": state, "detail": detail}
    if count is not None:
        entry["count"] = count
    return entry


def _priority_order(priority: str) -> int:
    return {"Critical": 0, "High": 1, "Cleanup": 2, "Rotation": 3}.get(priority, 4)


def _now() -> datetime:
    """Return the current UTC time.

    Factored out so tests can freeze the clock by monkey-patching
    ``triage._now`` without swapping the ``datetime`` class (which would
    break ``isinstance(value, datetime)`` checks inside ``_parse_dt``).
    """
    return datetime.now(timezone.utc)


# --- Handler ----------------------------------------------------------------


def handler(event, context=None):
    """Triage every IAM access key in the deployment account.

    Args:
        event: {
            user_filter: str            — substring match on user name
            exclude_user_substr: str    — substring skip for bulk naming
            include_inactive: bool      — include Inactive keys (default: True)
        }

    Returns:
        {
            keys: [ per-key rows sorted by priority then descending age ],
            summary: { Critical, High, Cleanup, Rotation, total_keys, users_with_keys },
            coverage: [ {source, state, detail, count?} per #171 ],
            usage_lag_caveat: str,
        }
    """
    event = event or {}
    user_filter = event.get("user_filter") or ""
    exclude_user_substr = event.get("exclude_user_substr") or ""
    include_inactive = event.get("include_inactive", True)

    now = _now()
    keys_out: list = []
    coverage: list = []

    # ---- Root user special case (Req 4.4 / DD-8) ---------------------------
    # The root user does NOT appear in iam:ListUsers, so we check
    # iam:GetAccountSummary["AccountAccessKeysPresent"] separately. If root
    # keys exist we synthesize one row; this row's priority is Critical no
    # matter what other flags apply.
    root_keys_present = False
    try:
        summary_map = iam_client.get_account_summary().get("SummaryMap", {})
        root_keys_present = bool(summary_map.get("AccountAccessKeysPresent"))
    except ClientError as e:
        # Non-fatal — record it as a coverage note against the iam source
        # but keep processing the rest of the inventory.
        coverage.append(_cov(
            "iam",
            "unavailable",
            f"iam:GetAccountSummary failed: {e.response.get('Error', {}).get('Code', 'Error')}: {e}",
        ))

    if root_keys_present:
        keys_out.append({
            "account_id": _account_id(),
            "user": "<root>",
            "is_root": True,
            "key_id": "(root)",
            "status": "Active",
            "created": "",
            "key_age_days": None,
            "last_used": "UNKNOWN",
            "last_used_service": "",
            "actions": "(root user — full account control)",
            "policies": "(root)",
            "resource_scope": "WILDCARD",
            "has_condition": False,
            "risk_flags": ["ADMIN"],
            "priority_class": "Critical",
            "suggested_remediation": "Remove_Root_Access_Keys",
            "suggested_remediation_url": _remediation_url("Remove_Root_Access_Keys"),
        })

    # ---- User + key inventory ---------------------------------------------
    iam_source_detail = ""
    iam_error_details: list = []
    users_with_keys = 0
    policy_walk_failures: list = []

    try:
        users_paginator = iam_client.get_paginator("list_users")
        users: list = []
        for page in users_paginator.paginate():
            users.extend(page.get("Users", []))
    except ClientError as e:
        # Top-level failure — no partial data (Req 5.2).
        code = e.response.get("Error", {}).get("Code", "Error")
        coverage.append(_cov(
            "iam",
            "unavailable",
            f"iam:ListUsers failed: {code}: {e}",
        ))
        return {
            "keys": keys_out,
            "summary": {"total_keys": len(keys_out), "users_with_keys": 0},
            "coverage": coverage,
            "usage_lag_caveat": USAGE_LAG_CAVEAT,
        }

    cache: dict = {}
    for user in users:
        user_name = user.get("UserName")
        if not user_name:
            continue
        if user_filter and user_filter not in user_name:
            continue
        if exclude_user_substr and exclude_user_substr in user_name:
            continue

        # List this user's keys. A per-user ListAccessKeys failure is
        # recorded but does not stop the inventory.
        try:
            key_meta = iam_client.list_access_keys(UserName=user_name).get(
                "AccessKeyMetadata", []
            )
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "Error")
            iam_error_details.append(f"iam:ListAccessKeys({user_name})={code}")
            continue

        if not key_meta:
            continue

        # Optional inactive filter
        if not include_inactive:
            key_meta = [k for k in key_meta if k.get("Status") == "Active"]
            if not key_meta:
                continue

        users_with_keys += 1
        n_active = sum(1 for k in key_meta if k.get("Status") == "Active")

        # Resolve effective policy for this user
        try:
            agg, managed, source_policies = _user_effective(user_name, cache)
            perms_str = " || ".join(agg["blocks"]) if agg["blocks"] else "(none)"
            if len(perms_str) > 3000:
                perms_str = perms_str[:3000] + " …TRUNC"
            pol_str = "; ".join(source_policies) if source_policies else "(none)"
            scope = _resource_scope(agg)
            has_condition = bool(agg["cond"])
            walk_ok = True
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "Error")
            logger.warning(
                "policy resolution failed for %s: %s: %s", user_name, code, e
            )
            policy_walk_failures.append(f"{user_name}({code})")
            agg = _empty()
            managed = set()
            perms_str = "(unavailable — policy resolution failed)"
            pol_str = "(unavailable)"
            scope = "NONE"
            has_condition = False
            walk_ok = False

        for k in key_meta:
            key_id = k.get("AccessKeyId", "")
            status = k.get("Status", "")
            created_dt = _parse_dt(k.get("CreateDate"))
            key_age_days = (now - created_dt).days if created_dt else None

            # Last-used per key. A ClientError here (throttle, permission
            # denied) sets LASTUSED_UNKNOWN on the row instead of aborting
            # (Req 5.3).
            last_used_dt = None
            last_used_service = ""
            last_known = True
            try:
                lu = iam_client.get_access_key_last_used(
                    AccessKeyId=key_id
                ).get("AccessKeyLastUsed", {}) or {}
                last_used_dt = _parse_dt(lu.get("LastUsedDate"))
                last_used_service = lu.get("ServiceName") or ""
            except ClientError as e:
                last_known = False
                code = e.response.get("Error", {}).get("Code", "Error")
                iam_error_details.append(
                    f"iam:GetAccessKeyLastUsed({key_id[:4]}…{key_id[-4:]})={code}"
                )

            flags = _flags_for(
                agg=agg,
                managed=managed,
                created=created_dt or now,
                last_used=last_used_dt,
                n_active=n_active,
                last_known=last_known,
                now=now,
            )
            # If the policy walk failed we cannot claim ADMIN/BROAD/etc.
            # authoritatively; drop those permission-derived flags but keep
            # the age / usage / multi-key flags which come from other data.
            if not walk_ok:
                flags = [
                    f for f in flags
                    if not (
                        f == "ADMIN"
                        or f.startswith("BROAD:")
                        or f.startswith("SERVICE_WILDCARD:")
                        or f == "RESOURCE_WILDCARD"
                    )
                ]

            priority = _priority_class(flags)
            remediation = _suggested_remediation(user_name)

            keys_out.append({
                "account_id": _account_id(),
                "user": user_name,
                "is_root": False,
                "key_id": key_id,
                "status": status,
                "created": created_dt.isoformat() if created_dt else "",
                "key_age_days": key_age_days,
                "last_used": (
                    last_used_dt.isoformat() if last_used_dt
                    else ("UNKNOWN" if not last_known else "NEVER")
                ),
                "last_used_service": last_used_service,
                "actions": perms_str,
                "policies": pol_str,
                "resource_scope": scope,
                "has_condition": has_condition,
                "risk_flags": flags,
                "priority_class": priority,
                "suggested_remediation": remediation,
                "suggested_remediation_url": _remediation_url(remediation),
            })

    # ---- Sort: priority, then age descending ------------------------------
    keys_out.sort(
        key=lambda r: (
            _priority_order(r["priority_class"]),
            -1 * (r["key_age_days"] or 0),
        )
    )

    # ---- Summary counts ---------------------------------------------------
    summary = {
        "Critical": 0, "High": 0, "Cleanup": 0, "Rotation": 0,
        "total_keys": len(keys_out),
        "users_with_keys": users_with_keys + (1 if root_keys_present else 0),
    }
    for row in keys_out:
        summary[row["priority_class"]] = summary.get(row["priority_class"], 0) + 1

    # ---- Coverage --------------------------------------------------------
    # `iam` covers ListUsers/ListAccessKeys/GetAccessKeyLastUsed/GetAccountSummary.
    # If we made it here without a top-level bailout, the state is `checked`;
    # any per-key throttles are named in `detail`.
    iam_source_detail = (
        f"Inventoried {len(users)} IAM user(s); {users_with_keys} had access keys"
    )
    if iam_error_details:
        iam_source_detail += ". Per-item issues: " + "; ".join(iam_error_details[:5])
        if len(iam_error_details) > 5:
            iam_source_detail += f" (+{len(iam_error_details) - 5} more)"
    coverage.append(_cov("iam", "checked", iam_source_detail, count=len(keys_out)))

    if policy_walk_failures:
        coverage.append(_cov(
            "iam-policy-resolution",
            "unavailable",
            "Effective-policy walk failed for: " + ", ".join(policy_walk_failures[:10])
            + (f" (+{len(policy_walk_failures) - 10} more)" if len(policy_walk_failures) > 10 else ""),
            count=len(policy_walk_failures),
        ))
    else:
        coverage.append(_cov(
            "iam-policy-resolution",
            "checked",
            "Resolved attached-managed, inline, and group policies for every user with keys",
            count=users_with_keys,
        ))

    return {
        "keys": keys_out,
        "summary": summary,
        "coverage": coverage,
        "usage_lag_caveat": USAGE_LAG_CAVEAT,
    }


# --- Account-id helper ------------------------------------------------------


def _account_id() -> str:
    """Return the current AWS account id, cached across warm invocations.

    Uses ``iam:ListUsers`` implicitly (the tool's caller already has that
    permission) — we cache the id from the first call. Falls back to
    ``AWS_ACCOUNT_ID`` env or an empty string so tests do not need STS mocks.
    """
    global _CACHED_ACCOUNT_ID
    if _CACHED_ACCOUNT_ID:
        return _CACHED_ACCOUNT_ID
    import os
    _CACHED_ACCOUNT_ID = os.environ.get("AWS_ACCOUNT_ID", "")
    return _CACHED_ACCOUNT_ID


_CACHED_ACCOUNT_ID = ""
