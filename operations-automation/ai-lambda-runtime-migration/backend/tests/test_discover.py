"""Unit tests for discover.py pure helpers (design §10)."""
from unittest.mock import MagicMock

import discover
from discover import (
    _priority_label, item_key, canonical_arn, support_home_region, base_priority_score,
    fallback_priorities, prioritize_with_bedrock, _extract_region, discover_scope,
    build_rollup, put_rollup,
)


def test_priority_label_thresholds():
    assert _priority_label(90) == "CRITICAL"
    assert _priority_label(80) == "CRITICAL"
    assert _priority_label(70) == "HIGH"
    assert _priority_label(50) == "MEDIUM"
    assert _priority_label(25) == "LOW"
    assert _priority_label(0) == "INACTIVE"


def test_item_key_format():
    assert item_key("111111111111", "us-east-1", "arn:aws:lambda:us-east-1:111111111111:function:f") == \
        "fn#111111111111#us-east-1#arn:aws:lambda:us-east-1:111111111111:function:f"


def test_item_key_canonicalizes_latest_qualifier():
    """The ':$LATEST' and unqualified ARN forms must produce ONE identical key,
    so a rescan where TA switches ARN form does not falsely retire the function."""
    base = "arn:aws:lambda:us-east-1:111:function:f"
    assert item_key("111", "us-east-1", base) == item_key("111", "us-east-1", base + ":$LATEST")


def test_canonical_arn_strips_only_latest():
    base = "arn:aws:lambda:us-east-1:111:function:f"
    assert canonical_arn(base + ":$LATEST") == base
    assert canonical_arn(base) == base
    # A numeric version qualifier is a DIFFERENT resource — leave it untouched.
    assert canonical_arn(base + ":42") == base + ":42"


def test_extract_region_from_arn():
    assert _extract_region("arn:aws:lambda:eu-west-1:111:function:f", "us-east-1") == "eu-west-1"
    # No region segment -> fall back to the default.
    assert _extract_region("bogus-arn", "us-east-1") == "us-east-1"


def test_support_home_region_commercial():
    # Commercial partition is served from us-east-1.
    assert support_home_region("eu-west-1") == "us-east-1"
    assert support_home_region("us-east-1") == "us-east-1"


def test_support_home_region_govcloud():
    assert support_home_region("us-gov-west-1") == "us-gov-west-1"


def _fn(alert, days, daily, invokes_14d=100):
    return {"arn": "a", "alert_status": alert, "days_to_deprecation": days,
            "avg_daily_invokes": daily, "invocation_count_14d": invokes_14d}


def test_base_priority_score_bands():
    """The D3 bands, pinned: the ONE place the priority bands live."""
    assert base_priority_score(_fn("Red", -79, 42)) == 85       # 75 + 5 (past deadline) + 5 (>= 10/day)
    assert base_priority_score(_fn("Red", -79, 5)) == 80        # 75 + 5 + 0
    assert base_priority_score(_fn("Yellow", 0, 1)) == 60       # 50 + 10 (<= 30 days) + 0
    assert base_priority_score(_fn("Yellow", 150, 1)) == 50     # 50 + 0 + 0
    assert base_priority_score(_fn("Yellow", 150, 0, 0)) == 15  # inactive, nothing else applied
    assert base_priority_score(_fn("Red", -200, 500)) == 95     # 75 + 10 (<= -180) + 10 (>= 100/day)
    assert base_priority_score(_fn("Red", -10, 0.5)) == 75      # 75 + 5 - 5 (< 1/day but not inactive)
    assert base_priority_score(_fn("Yellow", 60, 20)) == 60     # 50 + 5 (<= 90 days) + 5


def test_fallback_priorities_shape():
    summaries = [
        {"arn": "a", "alert_status": "Red", "invocation_count_14d": 100, "avg_daily_invokes": 5,
         "days_to_deprecation": -79},
        {"arn": "b", "alert_status": "Yellow", "invocation_count_14d": 0, "avg_daily_invokes": 0},
        {"arn": "c", "base_score": 61},                          # pre-computed base is reused
    ]
    out = fallback_priorities(summaries)
    assert set(out) == {"a", "b", "c"}
    assert out["a"] == {"score": 80, "base": 80, "adjustment": 0, "reasoning": "rule-based score"}
    assert out["b"]["score"] == out["b"]["base"] == 15
    assert out["c"]["score"] == out["c"]["base"] == 61
    for p in out.values():
        assert p["score"] == p["base"] and p["adjustment"] == 0 and p["reasoning"] == "rule-based score"


# --- prioritize_with_bedrock: base score + bounded Nova adjustment, batched ----

def _active_fn(arn, base_daily=42.0, status="DISCOVERED", tags=None):
    """A row as discover_scope hands it to prioritize: Red / -79 days / 42 per day -> base 85."""
    return {"function_arn": arn, "runtime": "python3.8", "alert_status": "Red",
            "days_to_deprecation": -79, "avg_daily_invokes": base_daily, "invocation_count_14d": 100,
            "migration_status": status, "tags": tags or {}}


def _tool_response(adjustments):
    """A Converse response whose toolUse carries {arn: (adjustment, reasoning)} as adj_i."""
    tool_input = {f"adj_{i}": {"arn": arn, "adjustment": adj, "reasoning": why}
                  for i, (arn, (adj, why)) in enumerate(adjustments.items())}
    return {"output": {"message": {"content": [{"toolUse": {"input": tool_input}}]}}}


def _fake_bedrock(monkeypatch, responses):
    """Route discover's bedrock-runtime client to a MagicMock whose converse
    returns `responses` in order (a callable entry is called, so it may raise)."""
    fake = MagicMock()
    queue = list(responses)

    def converse(**kwargs):
        item = queue.pop(0)
        return item() if callable(item) else item
    fake.converse.side_effect = converse
    monkeypatch.setattr(discover.boto3, "client", lambda *a, **k: fake)
    monkeypatch.setattr(discover, "_own_region", lambda: "us-east-1")
    monkeypatch.setattr(discover, "_model_id", lambda: "model")
    return fake


def test_prioritize_applies_and_clamps_adjustment(monkeypatch):
    a, b, c = "arn:a", "arn:b", "arn:c"
    # Red / -79 days with 5, 500 and 0.5 invokes per day -> bases 80, 90, 75 (pinned below).
    fns = [_active_fn(a, base_daily=5.0), _active_fn(b, base_daily=500.0), _active_fn(c, base_daily=0.5)]
    bases = {arn: base_priority_score(fn) for arn, fn in zip((a, b, c), fns)}
    assert bases == {a: 80, b: 90, c: 75}
    fake = _fake_bedrock(monkeypatch, [_tool_response({a: (8, "prod tag"), b: (40, "huge"), c: (-40, "dev")})])

    out = prioritize_with_bedrock(MagicMock(), "us-east-1", fns)

    assert out[a] == {"score": 88, "base": 80, "adjustment": 8, "reasoning": "prod tag"}
    assert out[b]["adjustment"] == 15 and out[b]["score"] == 100          # +40 clamped to +15, final <= 100
    assert out[c]["adjustment"] == -15 and out[c]["score"] == 60          # -40 clamped to -15
    assert fake.converse.call_count == 1
    assert fake.converse.call_args.kwargs["modelId"] == "model"
    assert fake.converse.call_args.kwargs["inferenceConfig"] == {"temperature": 0}


def test_prioritize_adjustment_on_base_72(monkeypatch):
    """The FEAT-003 drawer example: +8 on a base-72 function -> 80 / 72 / +8."""
    arn = "arn:x"
    fn = _active_fn(arn)
    monkeypatch.setattr(discover, "base_priority_score", lambda f: 72)
    _fake_bedrock(monkeypatch, [_tool_response({arn: (8, "prod tag")})])

    out = prioritize_with_bedrock(MagicMock(), "us-east-1", [fn])

    assert out[arn] == {"score": 80, "base": 72, "adjustment": 8, "reasoning": "prod tag"}


def test_prioritize_batches_by_50(monkeypatch):
    arns = [f"arn:f{i}" for i in range(120)]
    fns = [_active_fn(a) for a in arns]
    responses = [_tool_response({a: (0, "no hint") for a in arns[s:s + 50]}) for s in (0, 50, 100)]
    fake = _fake_bedrock(monkeypatch, responses)

    out = prioritize_with_bedrock(MagicMock(), "us-east-1", fns)

    assert fake.converse.call_count == 3
    assert set(out) == set(arns)
    # Each batch's tool schema is bounded to its slice.
    sizes = [len(c.kwargs["toolConfig"]["tools"][0]["toolSpec"]["inputSchema"]["json"]["properties"])
             for c in fake.converse.call_args_list]
    assert sizes == [50, 50, 20]


def test_prioritize_failed_batch_falls_back_alone(monkeypatch, caplog):
    arns = [f"arn:f{i}" for i in range(60)]
    fns = [_active_fn(a) for a in arns]

    def boom():
        raise RuntimeError("throttled")
    _fake_bedrock(monkeypatch, [_tool_response({a: (3, "nova says") for a in arns[:50]}), boom])

    with caplog.at_level("WARNING"):
        out = prioritize_with_bedrock(MagicMock(), "us-east-1", fns)

    assert all(out[a]["reasoning"] == "nova says" and out[a]["adjustment"] == 3 for a in arns[:50])
    for a in arns[50:]:
        assert out[a] == {"score": 85, "base": 85, "adjustment": 0, "reasoning": "rule-based score"}
    assert "prioritize batch 2/2 fell back to rule-based scores" in caplog.text


def test_prioritize_without_tool_use_falls_back(monkeypatch):
    arn = "arn:a"
    _fake_bedrock(monkeypatch, [{"output": {"message": {"content": [{"text": "I cannot."}]}}}])

    out = prioritize_with_bedrock(MagicMock(), "us-east-1", [_active_fn(arn)])

    assert out == {arn: {"score": 85, "base": 85, "adjustment": 0, "reasoning": "rule-based score"}}


def test_prioritize_excludes_skipped_and_fills_missing(monkeypatch, caplog):
    a, b, skipped = "arn:a", "arn:b", "arn:img"
    fns = [_active_fn(a), _active_fn(b), _active_fn(skipped, status="SKIPPED")]
    # Nova answers for a only, plus an ARN we never asked about.
    _fake_bedrock(monkeypatch, [_tool_response({a: (-5, "staging"), "arn:unknown": (9, "?")})])

    with caplog.at_level("WARNING"):
        out = prioritize_with_bedrock(MagicMock(), "us-east-1", fns)

    assert set(out) == {a, b}                                 # SKIPPED never scored
    assert out[a] == {"score": 80, "base": 85, "adjustment": -5, "reasoning": "staging"}
    assert out[b] == {"score": 85, "base": 85, "adjustment": 0, "reasoning": "rule-based score"}
    assert "unknown ARN arn:unknown" in caplog.text


def test_prioritize_empty_scope_makes_no_call(monkeypatch):
    fake = _fake_bedrock(monkeypatch, [])
    assert prioritize_with_bedrock(MagicMock(), "us-east-1", [_active_fn("arn:img", status="SKIPPED")]) == {}
    assert fake.converse.call_count == 0


def test_prioritize_prompt_wording(monkeypatch):
    arn = "arn:a"
    fake = _fake_bedrock(monkeypatch, [_tool_response({arn: (0, "ok")})])

    prioritize_with_bedrock(MagicMock(), "us-east-1", [_active_fn(arn, tags={"environment": "prod"})])

    kwargs = fake.converse.call_args.kwargs
    text = kwargs["messages"][0]["content"][0]["text"]
    # The retired "runtime age" example (nodejs 10 / python 3.6 vs nodejs 20) is
    # gone; the literals are assembled so a repo grep for them finds nothing.
    for retired in (f"nodejs{10}.x", f"python{3}.6", f"nodejs{20}.x"):
        assert retired not in text
    assert "L4dfs2Q4C5" in text                                # Trusted Advisor context kept
    assert "-15" in text and "+15" in text
    assert "older major versions of a runtime first; days_to_deprecation already carries the deadline" in text
    assert '"base_score": 85' in text                          # Nova sees the base it adjusts
    assert "Score 80-95" not in text                           # bands live in code only
    assert "migration backlog for operators" in kwargs["system"][0]["text"]
    tool = kwargs["toolConfig"]["tools"][0]["toolSpec"]
    assert tool["name"] == "submit_priority_adjustments"
    adj = tool["inputSchema"]["json"]["properties"]["adj_0"]["properties"]["adjustment"]
    assert adj == {"type": "integer", "minimum": -15, "maximum": 15, "description": adj["description"]}


# --- discover_scope: Trusted Advisor is the SINGLE source of truth -----------

def _patch_scope(monkeypatch):
    """Stub discover_scope's collaborators: DynamoDB table, the account-wide
    inventory read, enrich, prioritize. The Support-tier gate now lives in its
    own upstream step, so discover_scope no longer calls support_tier — the tier
    is threaded on the cell. query_trusted_advisor is left to the individual test."""
    table = MagicMock()
    table.scan.return_value = {"Items": []}
    table.get_item.return_value = {}          # no mig# sibling unless a test says so
    monkeypatch.setattr(discover, "_table", lambda session: table)
    monkeypatch.setattr(discover, "read_account_inventory",
                        lambda session, account_id: [])
    # No STS in tests: the scanned account is NOT the hub unless a test says so.
    monkeypatch.setattr(discover, "_caller_account", lambda region: "")
    monkeypatch.setattr(discover, "enrich_function",
                        lambda session, arn, region: {"package_type": "Zip",
                                                      "migration_status": "DISCOVERED"})
    monkeypatch.setattr(discover, "prioritize_with_bedrock",
                        lambda session, region, functions: {})
    return table


def _ta_fn(arn, region="us-east-1"):
    return {
        "function_arn": arn, "runtime": "python3.8", "region": region,
        "deprecation_date": "2024-10-14", "days_to_deprecation": -79,
        "avg_daily_invokes": 42.0, "alert_status": "Red", "ta_last_updated": "2025-01-01",
    }


def test_discover_scope_does_not_query_support_api(monkeypatch):
    """The Support-tier gate moved to its own upstream step: discover_scope must
    NOT call support_tier anymore (it only runs for TA-eligible accounts) and it
    takes the tier from the cell."""
    arn = "arn:aws:lambda:us-east-1:111:function:old"
    _patch_scope(monkeypatch)
    monkeypatch.setattr(discover, "support_tier",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("support_tier called in discover")))
    monkeypatch.setattr(discover, "query_trusted_advisor",
                        lambda session, region: {"functions": [_ta_fn(arn)], "message": "Found 1 function(s)."})

    result = discover_scope(
        MagicMock(),
        {"account_id": "111", "region": "us-east-1", "support_tier": "business"},
        "run1", "2025-01-01T00:00:00Z")

    assert result["ta_checked"] is True
    assert result["support_tier"] == "business"  # threaded from the cell
    assert result["discovered"] == 1


def test_discover_scope_business_account_discovers_via_ta(monkeypatch):
    arn = "arn:aws:lambda:us-east-1:111:function:old"
    table = _patch_scope(monkeypatch)
    monkeypatch.setattr(discover, "query_trusted_advisor",
                        lambda session, region: {"functions": [_ta_fn(arn)], "message": "Found 1 function(s)."})

    result = discover_scope(
        MagicMock(),
        {"account_id": "111", "region": "us-east-1", "support_tier": "business"},
        "run1", "2025-01-01T00:00:00Z")

    assert result["support_tier"] == "business"
    assert result["ta_checked"] is True
    assert result["discovered"] == 1
    assert result["new"] == 1
    assert result["regions_seen"] == ["us-east-1"]
    assert table.put_item.called
    # TA's avg_daily_invokes landed on the written row.
    written = table.put_item.call_args.kwargs["Item"]
    assert float(written["avg_daily_invokes"]) == 42.0


def test_discover_scope_writes_priority_base_and_adjustment(monkeypatch):
    """The priority write is ONE field-scoped SET carrying the five priority_*
    attributes (score, label, reasoning, base, adjustment) from the prioritizer."""
    arn = "arn:aws:lambda:us-east-1:111:function:old"
    table = _patch_scope(monkeypatch)
    monkeypatch.setattr(discover, "query_trusted_advisor",
                        lambda session, region: {"functions": [_ta_fn(arn)], "message": "Found 1 function(s)."})
    monkeypatch.setattr(discover, "prioritize_with_bedrock",
                        lambda session, region, functions: {
                            arn: {"score": 80, "base": 72, "adjustment": 8, "reasoning": "prod tag"}})

    discover_scope(MagicMock(), {"account_id": "111", "region": "us-east-1", "support_tier": "business"},
                   "run1", "2025-01-01T00:00:00Z")

    prio = [c for c in table.update_item.call_args_list
            if c.kwargs["UpdateExpression"].startswith("SET priority_score")]
    assert len(prio) == 1
    assert prio[0].kwargs["Key"] == {"item_key": item_key("111", "us-east-1", arn)}
    expr = prio[0].kwargs["UpdateExpression"]
    assert "priority_base = :b" in expr and "priority_adjustment = :a" in expr
    assert prio[0].kwargs["ExpressionAttributeValues"] == {
        ":s": 80, ":l": "CRITICAL", ":r": "prod tag", ":b": 72, ":a": 8}


def test_discover_scope_keys_functions_under_their_own_region(monkeypatch):
    """TA is account-wide: one call returns functions across several regions.
    Each function must be keyed under its OWN region, not the cell region."""
    arn_use1 = "arn:aws:lambda:us-east-1:111:function:a"
    arn_euw1 = "arn:aws:lambda:eu-west-1:111:function:b"
    table = _patch_scope(monkeypatch)
    monkeypatch.setattr(discover, "query_trusted_advisor",
                        lambda session, region: {"functions": [
                            _ta_fn(arn_use1, "us-east-1"),
                            _ta_fn(arn_euw1, "eu-west-1"),
                        ], "message": "Found 2 function(s)."})

    # Cell region is us-east-1, but TA reports a function in eu-west-1 too.
    result = discover_scope(
        MagicMock(),
        {"account_id": "111", "region": "us-east-1", "support_tier": "enterprise"},
        "run1", "2025-01-01T00:00:00Z")

    assert result["discovered"] == 2
    assert result["regions_seen"] == ["eu-west-1", "us-east-1"]
    written_keys = {call.kwargs["Item"]["item_key"] for call in table.put_item.call_args_list}
    assert item_key("111", "us-east-1", arn_use1) in written_keys
    assert item_key("111", "eu-west-1", arn_euw1) in written_keys


def test_discover_scope_region_inferred_from_arn_when_metadata_blank(monkeypatch):
    """When TA metadata has no region, discovery falls back to the ARN region."""
    arn = "arn:aws:lambda:ap-southeast-2:111:function:c"
    fn = _ta_fn(arn, region="")  # blank region in metadata
    table = _patch_scope(monkeypatch)
    monkeypatch.setattr(discover, "query_trusted_advisor",
                        lambda session, region: {"functions": [fn], "message": "Found 1 function(s)."})

    result = discover_scope(
        MagicMock(),
        {"account_id": "111", "region": "us-east-1", "support_tier": "enterprise"},
        "run1", "2025-01-01T00:00:00Z")

    assert result["regions_seen"] == ["ap-southeast-2"]
    written_keys = {call.kwargs["Item"]["item_key"] for call in table.put_item.call_args_list}
    assert item_key("111", "ap-southeast-2", arn) in written_keys


def test_discover_scope_resolves_functions_gone_from_ta(monkeypatch):
    """A function in our inventory but no longer flagged by TA is RESOLVED."""
    gone_arn = "arn:aws:lambda:us-east-1:111:function:gone"
    gone_key = item_key("111", "us-east-1", gone_arn)
    _patch_scope(monkeypatch)
    monkeypatch.setattr(discover, "read_account_inventory",
                        lambda session, account_id: [{"item_key": gone_key, "function_arn": gone_arn,
                                                      "resolved_at": None}])
    # TA now returns nothing.
    monkeypatch.setattr(discover, "query_trusted_advisor",
                        lambda session, region: {"functions": [], "message": "No functions."})
    table = discover._table(MagicMock())

    result = discover_scope(
        MagicMock(),
        {"account_id": "111", "region": "us-east-1", "support_tier": "enterprise"},
        "run1", "2025-01-01T00:00:00Z")

    assert result["resolved"] == 1
    # The resolved row gets the inferred-green transition: migration_status
    # MIGRATED (the only automatic migration write) and alert_status Green.
    resolve_calls = [c for c in table.update_item.call_args_list
                     if c.kwargs["ExpressionAttributeValues"].get(":s") == "MIGRATED"]
    assert resolve_calls
    assert resolve_calls[0].kwargs["ExpressionAttributeValues"][":a"] == "Green"
    # No row was written with the old 'RESOLVED' migration_status.
    assert not [c for c in table.update_item.call_args_list
                if c.kwargs["ExpressionAttributeValues"].get(":s") == "RESOLVED"]


def test_discover_scope_does_not_retire_function_under_other_arn_form(monkeypatch):
    """Key-normalization false-retirement fix: the inventory row is stored under
    the UNQUALIFIED ARN and TA now returns the ':$LATEST' form (or vice-versa).
    Because item_key canonicalizes, the two forms collapse to the same key, so
    the function is recognized as the SAME row (not retired): resolved == 0 and
    NO update writes migration_status='MIGRATED'/resolved_at for it."""
    base = "arn:aws:lambda:us-east-1:111:function:f"
    stored_key = item_key("111", "us-east-1", base)             # unqualified in inventory
    table = _patch_scope(monkeypatch)
    monkeypatch.setattr(discover, "read_account_inventory",
                        lambda session, account_id: [{"item_key": stored_key,
                                                      "function_arn": base, "resolved_at": None}])
    # TA reports the SAME function, but in its ':$LATEST' form this run.
    monkeypatch.setattr(discover, "query_trusted_advisor",
                        lambda session, region: {"functions": [_ta_fn(base + ":$LATEST")],
                                                 "message": "Found 1 function(s)."})

    result = discover_scope(
        MagicMock(),
        {"account_id": "111", "region": "us-east-1", "support_tier": "enterprise"},
        "run1", "2025-01-01T00:00:00Z")

    assert result["resolved"] == 0            # recognized as the same row, not retired
    assert result["new"] == 0                 # the stored key already exists
    # No MIGRATED/resolved_at write for this function.
    assert not [c for c in table.update_item.call_args_list
                if c.kwargs["ExpressionAttributeValues"].get(":s") == "MIGRATED"]


# --- discover_scope: stable-identity matching + row migration -----------------
#
# Twice a change to the item_key's SHAPE (ARN qualifier, hub account id) made a
# raw-key set difference retire the whole inventory as MIGRATED while inserting
# twin rows. Rows must be matched on (region, canonical ARN) and MOVED to the
# key the current scan composes, never retired for a key-shape reason.

def _migrated_writes(table):
    return [c for c in table.update_item.call_args_list
            if c.kwargs["ExpressionAttributeValues"].get(":s") == "MIGRATED"]


def _scan(monkeypatch, account_id, existing_rows, ta_functions, caller=""):
    table = _patch_scope(monkeypatch)
    monkeypatch.setattr(discover, "_caller_account", lambda region: caller)
    monkeypatch.setattr(discover, "read_account_inventory",
                        lambda session, acct: [r for r in existing_rows
                                               if str(r["item_key"]).startswith(f"fn#{acct}#")])
    monkeypatch.setattr(discover, "query_trusted_advisor",
                        lambda session, region: {"functions": ta_functions,
                                                 "message": f"Found {len(ta_functions)} function(s)."})
    result = discover_scope(
        MagicMock(),
        {"account_id": account_id, "region": "us-east-1", "support_tier": "enterprise"},
        "run1", "2025-06-01T00:00:00Z")
    return table, result


def test_discover_scope_rekeys_row_stored_under_latest_qualified_key(monkeypatch):
    """A legacy row keyed with ':$LATEST' in the key and a discovered function of
    the same identity: the row is MOVED to the canonical key (old key deleted,
    first_seen_at preserved), NOT retired, and counted as rekeyed."""
    base = "arn:aws:lambda:us-east-1:111:function:f"
    legacy_key = f"fn#111#us-east-1#{base}:$LATEST"      # pre-canonicalization key shape
    legacy_row = {"item_key": legacy_key, "function_arn": base + ":$LATEST", "account_id": "111",
                  "region": "us-east-1", "resolved_at": None, "first_seen_at": "2024-01-01T00:00:00Z",
                  "priority_score": 75, "assessment_summary": "keep me"}

    table, result = _scan(monkeypatch, "111", [legacy_row], [_ta_fn(base)])

    assert result["rekeyed"] == 1
    assert result["resolved"] == 0
    assert result["new"] == 0
    assert not _migrated_writes(table)
    moved = [c.kwargs["Item"] for c in table.put_item.call_args_list]
    assert len(moved) == 1
    assert moved[0]["item_key"] == item_key("111", "us-east-1", base)
    assert moved[0]["first_seen_at"] == "2024-01-01T00:00:00Z"      # attributes preserved
    assert moved[0]["priority_score"] == 75
    assert moved[0]["assessment_summary"] == "keep me"
    table.delete_item.assert_called_once_with(Key={"item_key": legacy_key})
    # The TA-field refresh landed on the NEW key.
    refreshed = [c for c in table.update_item.call_args_list
                 if ":d" in c.kwargs["ExpressionAttributeValues"]]
    assert refreshed and refreshed[0].kwargs["Key"] == {"item_key": item_key("111", "us-east-1", base)}


def test_discover_scope_rekeys_legacy_empty_id_hub_rows_onto_real_hub_id(monkeypatch):
    """The hub used to be keyed fn##<region>#...; now it scans with its real id.
    Its legacy rows must be read (fn## prefix) and moved to fn#<hub>#..., never
    retired as MIGRATED with fresh twins inserted."""
    hub = "999999999999"
    arn = f"arn:aws:lambda:us-east-1:{hub}:function:old"
    legacy_key = item_key("", "us-east-1", arn)
    legacy_row = {"item_key": legacy_key, "function_arn": arn + ":$LATEST", "account_id": "",
                  "region": "us-east-1", "resolved_at": None, "first_seen_at": "2024-01-01T00:00:00Z"}

    table, result = _scan(monkeypatch, hub, [legacy_row], [_ta_fn(arn + ":$LATEST")], caller=hub)

    assert legacy_key.startswith("fn##")
    assert result["rekeyed"] == 1
    assert result["resolved"] == 0 and result["new"] == 0
    assert not _migrated_writes(table)
    moved = table.put_item.call_args.kwargs["Item"]
    assert moved["item_key"] == item_key(hub, "us-east-1", arn)
    assert moved["account_id"] == hub
    table.delete_item.assert_called_once_with(Key={"item_key": legacy_key})


def test_discover_scope_vanished_function_still_resolved_under_identity_match(monkeypatch):
    """A function genuinely gone from TA is still retired: resolved_at set,
    migration_status MIGRATED, alert_status Green."""
    gone = "arn:aws:lambda:us-east-1:111:function:gone"
    kept = "arn:aws:lambda:us-east-1:111:function:kept"
    rows = [
        {"item_key": item_key("111", "us-east-1", gone), "function_arn": gone + ":$LATEST",
         "account_id": "111", "region": "us-east-1", "resolved_at": None},
        {"item_key": item_key("111", "us-east-1", kept), "function_arn": kept + ":$LATEST",
         "account_id": "111", "region": "us-east-1", "resolved_at": None},
    ]

    table, result = _scan(monkeypatch, "111", rows, [_ta_fn(kept + ":$LATEST")])

    assert result["resolved"] == 1
    assert result["rekeyed"] == 0
    writes = _migrated_writes(table)
    assert len(writes) == 1
    assert writes[0].kwargs["Key"] == {"item_key": item_key("111", "us-east-1", gone)}
    assert writes[0].kwargs["ExpressionAttributeValues"][":a"] == "Green"
    assert writes[0].kwargs["ExpressionAttributeValues"][":t"] == "2025-06-01T00:00:00Z"
    assert not table.delete_item.called


def test_discover_scope_deletes_stale_duplicate_keeps_active_row(monkeypatch):
    """Two legacy rows of ONE identity (a resolved twin + the active row): the
    active one survives, the twin is deleted (not retired, not counted)."""
    base = "arn:aws:lambda:us-east-1:111:function:f"
    active_key = item_key("111", "us-east-1", base)
    twin_key = f"fn#111#us-east-1#{base}:$LATEST"
    rows = [
        {"item_key": twin_key, "function_arn": base + ":$LATEST", "account_id": "111",
         "region": "us-east-1", "resolved_at": "2025-05-01T00:00:00Z", "migration_status": "MIGRATED"},
        {"item_key": active_key, "function_arn": base, "account_id": "111",
         "region": "us-east-1", "resolved_at": None, "first_seen_at": "2025-05-01T00:00:00Z"},
    ]

    table, result = _scan(monkeypatch, "111", rows, [_ta_fn(base + ":$LATEST")])

    table.delete_item.assert_called_once_with(Key={"item_key": twin_key})
    assert result["resolved"] == 0 and result["rekeyed"] == 0 and result["new"] == 0
    assert not table.put_item.called                 # nothing re-keyed or inserted
    assert not _migrated_writes(table)
    refreshed = [c for c in table.update_item.call_args_list
                 if ":d" in c.kwargs["ExpressionAttributeValues"]]
    assert refreshed[0].kwargs["Key"] == {"item_key": active_key}


def test_discover_scope_duplicate_collapse_merges_history_into_kept_row(monkeypatch):
    """The twin pattern the key-shape bug produced: the OLD row was retired
    (resolved, MIGRATED) but carries the assessment / priority / first_seen_at,
    the fresh twin is active and bare. The active twin is kept, and the history
    only the dropped row has is merged onto it (kept values win; the resolution
    and migration axes are never copied) before the duplicate is deleted."""
    base = "arn:aws:lambda:us-east-1:111:function:f"
    active_key = item_key("111", "us-east-1", base)
    twin_key = f"fn#111#us-east-1#{base}:$LATEST"
    rows = [
        {"item_key": twin_key, "function_arn": base + ":$LATEST", "account_id": "111",
         "region": "us-east-1", "resolved_at": "2025-05-01T00:00:00Z", "migration_status": "MIGRATED",
         "alert_status": "Green", "first_seen_at": "2024-01-01T00:00:00Z",
         "assessment_summary": "rich", "priority_score": 80, "priority_label": "CRITICAL"},
        {"item_key": active_key, "function_arn": base, "account_id": "111",
         "region": "us-east-1", "resolved_at": None, "migration_status": "DISCOVERED",
         "alert_status": "Red", "priority_label": "LOW"},
    ]

    table, result = _scan(monkeypatch, "111", rows, [_ta_fn(base + ":$LATEST")])

    merges = [c for c in table.update_item.call_args_list
              if c.kwargs["UpdateExpression"].startswith("SET #m0")]
    assert len(merges) == 1
    merge = merges[0].kwargs
    assert merge["Key"] == {"item_key": active_key}
    merged_attrs = {merge["ExpressionAttributeNames"][n]: merge["ExpressionAttributeValues"][v]
                    for n, v in (p.split(" = ") for p in merge["UpdateExpression"][4:].split(", "))}
    assert merged_attrs == {"first_seen_at": "2024-01-01T00:00:00Z",
                            "assessment_summary": "rich", "priority_score": 80}
    # Kept value wins (priority_label), axes never copied (resolved_at, migration_status, alert_status).
    assert "priority_label" not in merged_attrs
    table.delete_item.assert_called_once_with(Key={"item_key": twin_key})
    assert result["resolved"] == 0 and result["rekeyed"] == 0 and result["new"] == 0
    assert not _migrated_writes(table)


def test_discover_scope_refuses_to_scan_under_empty_account_id(monkeypatch):
    """An empty account id only means STS could not name the hub at run start.
    Scanning under it would key rows fn##... again (twins). The scope fails,
    nothing is written, the previous inventory stays intact."""
    import pytest
    table = _patch_scope(monkeypatch)
    monkeypatch.setattr(discover, "query_trusted_advisor",
                        lambda session, region: {"functions": [_ta_fn("arn:aws:lambda:us-east-1:111:function:f")],
                                                 "message": "Found 1 function(s)."})
    with pytest.raises(RuntimeError, match="empty account id"):
        discover_scope(MagicMock(),
                       {"account_id": "", "region": "us-east-1", "support_tier": "enterprise"},
                       "run1", "2025-06-01T00:00:00Z")
    assert not table.put_item.called and not table.update_item.called and not table.delete_item.called


def test_caller_account_does_not_cache_sts_failure(monkeypatch):
    """A transient STS error must not pin the hub id to '' for the container's
    lifetime (the next call retries and the success is cached)."""
    monkeypatch.setattr(discover, "_CALLER_CACHE", {})
    calls = {"n": 0}

    def fake_client(service, region_name=None):
        calls["n"] += 1
        client = MagicMock()
        if calls["n"] == 1:
            client.get_caller_identity.side_effect = RuntimeError("sts down")
        else:
            client.get_caller_identity.return_value = {"Account": "999999999999"}
        return client

    monkeypatch.setattr(discover.boto3, "client", fake_client)
    assert discover._caller_account("us-east-1") == ""
    assert discover._caller_account("us-east-1") == "999999999999"
    assert discover._caller_account("us-east-1") == "999999999999"
    assert calls["n"] == 2                         # failure retried once, success cached


# --- _rekey_row: the mig# sibling moves with its fn# row ----------------------
#
# migrate.py keys a function's migration-run item on the fn# row's account_id and
# region. Re-keying the fn# row without its mig# sibling would make the next
# re-migration create a SECOND mig# item for the same function.

def _rekey_hub_row(monkeypatch, mig_items, put_side_effect=None):
    hub = "999999999999"
    arn = f"arn:aws:lambda:us-east-1:{hub}:function:old"
    legacy_row = {"item_key": item_key("", "us-east-1", arn), "function_arn": arn + ":$LATEST",
                  "account_id": "", "region": "us-east-1", "resolved_at": None}
    table = _patch_scope(monkeypatch)
    table.get_item.side_effect = lambda Key: ({"Item": mig_items[Key["item_key"]]}
                                              if Key["item_key"] in mig_items else {})
    if put_side_effect is not None:
        table.put_item.side_effect = put_side_effect
    new_row = discover._rekey_row(table, legacy_row, item_key(hub, "us-east-1", arn),
                                  hub, "Hub", "us-east-1", arn + ":$LATEST")
    return table, hub, arn, new_row


def test_rekey_row_moves_mig_sibling_and_repoints_build_index(monkeypatch):
    hub = "999999999999"
    arn = f"arn:aws:lambda:us-east-1:{hub}:function:old"
    old_mig_key = discover.mig_item_key("", "us-east-1", arn)
    new_mig_key = discover.mig_item_key(hub, "us-east-1", arn)
    mig_item = {"item_key": old_mig_key, "account_id": "", "region": "us-east-1",
                "function_arn": arn, "migration_status": "IN_PROGRESS",
                "migration_build_id": "build-1", "migration_batch_id": "b1",
                "migration_updated_at": "2025-06-01T00:00:00Z"}

    table, _, _, new_row = _rekey_hub_row(monkeypatch, {old_mig_key: mig_item})

    assert old_mig_key.startswith("mig##")
    assert new_row["item_key"] == item_key(hub, "us-east-1", arn)
    puts = [c.kwargs for c in table.put_item.call_args_list]
    assert [p["Item"]["item_key"] for p in puts] == [item_key(hub, "us-east-1", arn), new_mig_key]
    moved = puts[1]
    assert moved["ConditionExpression"] == "attribute_not_exists(item_key)"
    assert moved["Item"]["account_id"] == hub
    assert moved["Item"]["migration_status"] == "IN_PROGRESS"          # every attribute preserved
    assert moved["Item"]["migration_build_id"] == "build-1"
    # build_complete resolves the mig# key through buildidx#<build_id>: re-pointed.
    repoint = [c.kwargs for c in table.update_item.call_args_list
               if c.kwargs["Key"] == {"item_key": "buildidx#build-1"}]
    assert repoint and repoint[0]["ExpressionAttributeValues"] == {":k": new_mig_key}
    deleted = [c.kwargs["Key"]["item_key"] for c in table.delete_item.call_args_list]
    assert deleted == [item_key("", "us-east-1", arn), old_mig_key]


def test_rekey_row_without_mig_sibling_moves_only_the_fn_row(monkeypatch):
    table, hub, arn, _ = _rekey_hub_row(monkeypatch, {})
    assert table.put_item.call_count == 1
    assert table.delete_item.call_count == 1
    assert not table.update_item.called


def test_rekey_row_never_clobbers_a_live_item_under_the_new_mig_key(monkeypatch):
    """A different item already lives under the new mig# key: the conditional
    put fails, the new item is left untouched AND the legacy item is kept (not
    deleted), with a warning."""
    from botocore.exceptions import ClientError
    hub = "999999999999"
    arn = f"arn:aws:lambda:us-east-1:{hub}:function:old"
    old_mig_key = discover.mig_item_key("", "us-east-1", arn)
    new_mig_key = discover.mig_item_key(hub, "us-east-1", arn)
    legacy = {"item_key": old_mig_key, "account_id": "", "migration_status": "PR_OPENED",
              "migration_batch_id": "b-old", "migration_updated_at": "2025-05-01T00:00:00Z"}
    live = {"item_key": new_mig_key, "account_id": hub, "migration_status": "QUEUED",
            "migration_batch_id": "b-new", "migration_updated_at": "2025-06-02T00:00:00Z"}

    def put(Item, ConditionExpression=None):
        if ConditionExpression and Item["item_key"] == new_mig_key:
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem")

    table, _, _, _ = _rekey_hub_row(monkeypatch, {old_mig_key: legacy, new_mig_key: live},
                                    put_side_effect=put)

    deleted = [c.kwargs["Key"]["item_key"] for c in table.delete_item.call_args_list]
    assert deleted == [item_key("", "us-east-1", arn)]          # fn# row moved, mig# legacy kept
    assert not table.update_item.called                          # nothing written over the live item


def test_rekey_row_drops_legacy_mig_item_when_new_key_holds_its_own_copy(monkeypatch):
    """Retry after a crash between the mig# put and the old delete: the item
    under the new key IS the copy, so the legacy key is cleaned up."""
    from botocore.exceptions import ClientError
    hub = "999999999999"
    arn = f"arn:aws:lambda:us-east-1:{hub}:function:old"
    old_mig_key = discover.mig_item_key("", "us-east-1", arn)
    new_mig_key = discover.mig_item_key(hub, "us-east-1", arn)
    legacy = {"item_key": old_mig_key, "account_id": "", "migration_status": "REJECTED",
              "migration_batch_id": "b1", "migration_updated_at": "2025-05-01T00:00:00Z"}
    copy = {**legacy, "item_key": new_mig_key, "account_id": hub}

    def put(Item, ConditionExpression=None):
        if ConditionExpression and Item["item_key"] == new_mig_key:
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem")

    table, _, _, _ = _rekey_hub_row(monkeypatch, {old_mig_key: legacy, new_mig_key: copy},
                                    put_side_effect=put)

    deleted = [c.kwargs["Key"]["item_key"] for c in table.delete_item.call_args_list]
    assert deleted == [item_key("", "us-east-1", arn), old_mig_key]


def test_discover_scope_hub_rekey_carries_mig_sibling(monkeypatch):
    """End to end through discover_scope: a legacy fn## hub row with a mig##
    sibling is scanned with the real hub id; both move to the real-id keys."""
    hub = "999999999999"
    arn = f"arn:aws:lambda:us-east-1:{hub}:function:old"
    legacy_row = {"item_key": item_key("", "us-east-1", arn), "function_arn": arn + ":$LATEST",
                  "account_id": "", "region": "us-east-1", "resolved_at": None}
    old_mig_key = discover.mig_item_key("", "us-east-1", arn)
    mig_item = {"item_key": old_mig_key, "account_id": "", "migration_status": "REJECTED",
                "migration_failure_code": "TAG_NOT_FOUND"}
    table = _patch_scope(monkeypatch)
    table.get_item.side_effect = lambda Key: ({"Item": mig_item} if Key["item_key"] == old_mig_key else {})
    monkeypatch.setattr(discover, "_caller_account", lambda region: hub)
    monkeypatch.setattr(discover, "read_account_inventory",
                        lambda session, acct: [legacy_row] if acct == "" else [])
    monkeypatch.setattr(discover, "query_trusted_advisor",
                        lambda session, region: {"functions": [_ta_fn(arn + ":$LATEST")],
                                                 "message": "Found 1 function(s)."})

    result = discover_scope(MagicMock(), {"account_id": hub, "region": "us-east-1",
                                          "support_tier": "enterprise"},
                            "run1", "2025-06-01T00:00:00Z")

    assert result["rekeyed"] == 1 and result["resolved"] == 0
    put_keys = [c.kwargs["Item"]["item_key"] for c in table.put_item.call_args_list]
    assert put_keys == [item_key(hub, "us-east-1", arn), discover.mig_item_key(hub, "us-east-1", arn)]
    deleted = [c.kwargs["Key"]["item_key"] for c in table.delete_item.call_args_list]
    assert deleted == [item_key("", "us-east-1", arn), old_mig_key]


# --- build_rollup / put_rollup (measurement series) --------------------------

def _fn_row(account="111", region="us-east-1", arn="arn:x", runtime="python3.8",
            alert="Red", resolved_at=None, migration_status="DISCOVERED"):
    return {"item_key": item_key(account, region, arn), "function_arn": arn, "account_id": account,
            "region": region, "runtime": runtime, "alert_status": alert,
            "resolved_at": resolved_at, "migration_status": migration_status}


def test_build_rollup_counts_active_and_migrated():
    rows = [
        _fn_row(arn="a", alert="Red", runtime="python3.8"),
        _fn_row(arn="b", alert="Yellow", runtime="python3.8"),
        _fn_row(arn="c", alert="Yellow", runtime="nodejs14.x"),
        # Migrated (inferred-green): resolved_at set, Green — not active backlog.
        _fn_row(arn="d", alert="Green", resolved_at="2025-01-01T00:00:00Z",
                migration_status="MIGRATED"),
        # A rollup row that slipped into the snapshot must be ignored.
        {"item_key": "rollup#2025-01-01T00:00:00Z", "alert_status": "Red"},
    ]
    run = {"run_id": "run-1"}
    disc_summary = {"accounts_scanned": ["111"]}
    support_summary = {"tiers": [
        {"account_id": "111", "ta_eligible": True},
        {"account_id": "222", "ta_eligible": False},
    ]}
    rollup = build_rollup(run, disc_summary, {}, support_summary, rows,
                          "2025-01-02T00:00:00Z")

    assert rollup["item_key"] == "rollup#2025-01-02T00:00:00Z"
    assert rollup["rollup_at"] == "2025-01-02T00:00:00Z"
    assert rollup["run_id"] == "run-1"
    assert rollup["total_active"] == 3
    assert rollup["red"] == 1
    assert rollup["yellow"] == 2
    assert rollup["migrated_total"] == 1
    assert rollup["accounts_scanned"] == 1
    assert rollup["accounts_not_checked"] == 1
    assert rollup["by_runtime"] == {"python3.8": 2, "nodejs14.x": 1}
    assert rollup["by_account"] == {"111": 3}


def test_build_rollup_never_emits_empty_account_key():
    """Legacy hub rows carry account_id == "". DynamoDB rejects an empty map key
    (ValidationException: Empty attribute name), which silently killed every
    rollup write; group those rows under "unknown" instead."""
    rows = [_fn_row(account="", arn="a"), _fn_row(account="", arn="b"), _fn_row(account="222", arn="c")]
    rollup = build_rollup({"run_id": "r"}, {}, {}, {}, rows, "2025-01-02T00:00:00Z")
    assert "" not in rollup["by_account"]
    assert rollup["by_account"] == {"unknown": 2, "222": 1}


def test_put_rollup_writes_rollup_prefixed_item(monkeypatch):
    table = MagicMock()
    monkeypatch.setattr(discover, "_table", lambda session=None: table)
    put_rollup({"item_key": "rollup#2025-01-02T00:00:00Z", "total_active": 3,
                "by_account": {"111": 3}}, MagicMock())
    assert table.put_item.called
    written = table.put_item.call_args.kwargs["Item"]
    assert written["item_key"].startswith("rollup#")


# --- migration-run matching (the ONE rule shared by the API and the rollup) --

_RUN_KEYS = ("run_not_started", "run_in_progress", "run_rejected", "run_failed",
             "run_no_changes", "run_branches_ready", "run_legacy")


def _mig_item(arn, status, account="111", region="us-east-1"):
    return {"item_key": f"mig#{account}#{region}#{canonical_arn(arn)}",
            "function_arn": arn, "migration_status": status}


def test_index_migration_runs_keys_by_canonical_arn():
    qualified = _mig_item("arn:q:$LATEST", "BRANCH_READY")
    plain = _mig_item("arn:p", "FAILED")
    not_a_mig = {"item_key": "fn#111#us-east-1#arn:p", "function_arn": "arn:p"}
    idx = discover.index_migration_runs([qualified, plain, not_a_mig])
    assert set(idx) == {"arn:q", "arn:p"}
    # A $LATEST-qualified item is found by the unqualified ARN and vice-versa.
    assert discover.migration_run_for({"function_arn": "arn:q"}, idx) is qualified
    assert discover.migration_run_for({"function_arn": "arn:p:$LATEST"}, idx) is plain


def test_migration_run_for_returns_none_without_match():
    idx = discover.index_migration_runs([_mig_item("arn:p", "FAILED")])
    assert discover.migration_run_for({"function_arn": "arn:other"}, idx) is None
    assert discover.migration_run_for({}, idx) is None


def test_read_fleet_inventory_returns_fn_and_mig_rows_only(monkeypatch):
    fn = _fn_row(arn="a")
    mig = _mig_item("a", "BRANCH_READY")
    table = MagicMock()
    table.scan.return_value = {"Items": [
        fn, mig,
        {"item_key": "rollup#2025-01-01T00:00:00Z", "total_active": 1},
        {"item_key": "settings#git", "provider": "codecommit"},
    ]}
    monkeypatch.setattr(discover, "_table", lambda session=None: table)
    assert discover.read_fleet_inventory(MagicMock()) == [fn, mig]


def test_build_rollup_counts_migration_run_axis():
    """The active backlog partitioned by the migration-run axis: no item ->
    not started, QUEUED/IN_PROGRESS -> in progress, legacy PR_OPENED -> legacy;
    a resolved MIGRATED row is never counted even with a BRANCH_READY item; an
    orphan mig# item is ignored; matching is by canonical ARN ($LATEST)."""
    rows = [
        _fn_row(arn="a"),                      # no mig# item at all
        _fn_row(arn="b"), _fn_row(arn="c"),    # QUEUED / IN_PROGRESS
        _fn_row(arn="d"), _fn_row(arn="e"),    # REJECTED / FAILED
        _fn_row(arn="f"), _fn_row(arn="g"),    # NO_CHANGES / BRANCH_READY
        _fn_row(arn="h"),                      # legacy PR_OPENED
        _fn_row(arn="i", alert="Green", resolved_at="2025-01-01T00:00:00Z",
                migration_status="MIGRATED"),
        _mig_item("b", "QUEUED"), _mig_item("c", "IN_PROGRESS"),
        _mig_item("d", "REJECTED"), _mig_item("e", "FAILED"),
        _mig_item("f", "NO_CHANGES"),
        _mig_item("g:$LATEST", "BRANCH_READY"),   # canonical matching
        _mig_item("h", "PR_OPENED"),
        _mig_item("i", "BRANCH_READY"),           # resolved row: not awaiting merge
        _mig_item("zzz", "BRANCH_READY"),         # orphan: no fn# row
    ]
    rollup = build_rollup({"run_id": "r"}, {}, {}, {}, rows, "2025-01-02T00:00:00Z")
    assert rollup["run_not_started"] == 1
    assert rollup["run_in_progress"] == 2
    assert rollup["run_rejected"] == 1
    assert rollup["run_failed"] == 1
    assert rollup["run_no_changes"] == 1
    assert rollup["run_branches_ready"] == 1
    assert rollup["run_legacy"] == 1
    assert rollup["migrated_total"] == 1
    assert rollup["total_active"] == 8
    assert sum(rollup[k] for k in _RUN_KEYS) == rollup["total_active"]


def test_build_rollup_run_keys_are_zero_without_mig_items():
    rows = [_fn_row(arn="a", alert="Red"), _fn_row(arn="b", alert="Yellow"),
            _fn_row(arn="d", alert="Green", resolved_at="2025-01-01T00:00:00Z",
                    migration_status="MIGRATED")]
    rollup = build_rollup({"run_id": "r"}, {}, {}, {}, rows, "2025-01-02T00:00:00Z")
    assert rollup["total_active"] == 2
    assert rollup["run_not_started"] == rollup["total_active"]
    for key in _RUN_KEYS:
        if key != "run_not_started":
            assert rollup[key] == 0


def test_build_rollup_not_started_only_counts_active_backlog():
    """A function with no mig# item is 'not started' only if it is still active
    backlog: resolved and Green rows are not awaiting a migration."""
    rows = [_fn_row(arn="a", alert="Green"),
            _fn_row(arn="b", alert="Red", resolved_at="2025-01-01T00:00:00Z"),
            _fn_row(arn="c", alert="Red")]
    rollup = build_rollup({"run_id": "r"}, {}, {}, {}, rows, "2025-01-02T00:00:00Z")
    assert rollup["run_not_started"] == 1
    assert rollup["total_active"] == 1


def test_build_rollup_rehearsed_item_counts_as_legacy():
    rows = [_fn_row(arn="a"), _mig_item("a", "REHEARSED")]
    rollup = build_rollup({"run_id": "r"}, {}, {}, {}, rows, "2025-01-02T00:00:00Z")
    assert rollup["run_legacy"] == 1
    assert rollup["run_not_started"] == 0
