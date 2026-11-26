import boto3
from botocore.stub import Stubber

from s3dp.collect import account


def _c(svc, region="us-east-1"):
    c = boto3.client(svc, region_name=region, aws_access_key_id="x", aws_secret_access_key="x")
    return c, Stubber(c)


def test_account_bpa_absent_means_off():
    c, st = _c("s3control")
    st.add_client_error("get_public_access_block", "NoSuchPublicAccessBlockConfiguration", http_status_code=404,
                        expected_params={"AccountId": "111122223333"})
    with st:
        assert account.account_bpa(c, "111122223333") == "off"


def test_account_bpa_denied_is_unknown():
    c, st = _c("s3control")
    st.add_client_error("get_public_access_block", "AccessDenied", http_status_code=403,
                        expected_params={"AccountId": "111122223333"})
    with st:
        assert account.account_bpa(c, "111122223333") == "unknown"


def test_s3_regions_from_cost_explorer():
    c, st = _c("ce")
    st.add_response("get_cost_and_usage", {"ResultsByTime": [{"Groups": [
        {"Keys": ["eu-west-1"], "Metrics": {"UnblendedCost": {"Amount": "120.5", "Unit": "USD"}}},
        {"Keys": ["NoRegion"], "Metrics": {"UnblendedCost": {"Amount": "3", "Unit": "USD"}}},
        {"Keys": ["us-east-1"], "Metrics": {"UnblendedCost": {"Amount": "0", "Unit": "USD"}}}]}]})
    with st:
        assert account.s3_spend_by_region(c, "2026-11-25") == {"eu-west-1": 120.5}


def _rule(name, ident=None):
    r = {"ConfigRuleName": name, "Source": {"Owner": "AWS", "SourceIdentifier": ident or name.upper()}}
    return r


def test_config_recording_comes_from_recorder_status_not_existence():
    c, st = _c("config")
    st.add_response("describe_configuration_recorders", {"ConfigurationRecorders": [{"name": "default"}]}, {})
    st.add_response("describe_configuration_recorder_status",
                    {"ConfigurationRecordersStatus": [{"name": "default", "recording": False}]}, {})
    st.add_response("describe_config_rules", {"ConfigRules": [_rule("a", "S3_BUCKET_VERSIONING_ENABLED"),
                                                              _rule("b", "IAM_ROOT")], "NextToken": "t"}, {})
    st.add_response("describe_config_rules", {"ConfigRules": [_rule("c", "S3_BUCKET_LOGGING_ENABLED")]}, {"NextToken": "t"})
    with st:
        assert account.config_status(c) == {"recording": False,
                                            "s3_rules": ["s3-bucket-logging-enabled", "s3-bucket-versioning-enabled"]}


def test_config_rules_failure_is_unknown_not_empty():
    c, st = _c("config")
    st.add_response("describe_configuration_recorders", {"ConfigurationRecorders": [{"name": "d"}]}, {})
    st.add_response("describe_configuration_recorder_status",
                    {"ConfigurationRecordersStatus": [{"name": "d", "recording": True}]}, {})
    st.add_client_error("describe_config_rules", "AccessDeniedException", http_status_code=403)
    with st:
        assert account.config_status(c) == {"recording": True, "s3_rules": None}


def test_config_recorder_failure_is_unknown():
    c, st = _c("config")
    st.add_client_error("describe_configuration_recorders", "AccessDeniedException", http_status_code=403)
    with st:
        assert account.config_status(c) == {"recording": None, "s3_rules": None}


def test_security_hub_only_invalid_access_means_disabled():
    for code, want in (("InvalidAccessException", False), ("ResourceNotFoundException", None),
                       ("AccessDeniedException", None)):
        c, st = _c("securityhub")
        st.add_client_error("get_enabled_standards", code, http_status_code=400)
        with st:
            assert account.security_hub_enabled(c) is want


def test_stack_filter_keeps_every_status_but_delete_complete():
    c, _ = _c("cloudformation")
    enum = c.meta.service_model.operation_model("ListStacks").input_shape.members["StackStatusFilter"].member.enum
    assert set(account.STACK_STATUSES_KEPT) == set(enum) - {"DELETE_COMPLETE"}


def test_spend_by_region_follows_next_page_token():
    c, st = _c("ce")
    g = lambda k, a: {"Keys": [k], "Metrics": {"UnblendedCost": {"Amount": a, "Unit": "USD"}}}  # noqa: E731
    st.add_response("get_cost_and_usage", {"ResultsByTime": [{"Groups": [g("eu-west-1", "10")]}],
                                           "NextPageToken": "n"})
    st.add_response("get_cost_and_usage", {"ResultsByTime": [{"Groups": [g("us-east-1", "5")]}]})
    with st:
        assert account.s3_spend_by_region(c, "2026-11-25") == {"eu-west-1": 10.0, "us-east-1": 5.0}


def test_account_bpa_flags_are_kept_one_by_one():
    c = boto3.client("s3control", region_name="eu-west-1", aws_access_key_id="x", aws_secret_access_key="x")
    with Stubber(c) as st:
        st.add_response("get_public_access_block", {"PublicAccessBlockConfiguration": {
            "BlockPublicAcls": True, "IgnorePublicAcls": True, "BlockPublicPolicy": False}})
        flags = account.account_bpa_flags(c, "111122223333")
    assert flags == {"BlockPublicAcls": True, "IgnorePublicAcls": True, "BlockPublicPolicy": False,
                     "RestrictPublicBuckets": False}
    assert account.bpa_state(flags) == "off" and account.bpa_state(None) == "unknown"
    assert account.bpa_state(dict.fromkeys(account.PAB_FLAGS, True)) == "on"
