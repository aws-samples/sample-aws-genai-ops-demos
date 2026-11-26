import boto3
from botocore.stub import ANY, Stubber

from s3dp.collect import backup


def test_region_posture():
    c = boto3.client("backup", region_name="eu-west-1", aws_access_key_id="x", aws_secret_access_key="x")
    st = Stubber(c)
    st.add_response("list_protected_resources", {"Results": [
        {"ResourceArn": "arn:aws:s3:::orders-prod", "ResourceType": "S3"},
        {"ResourceArn": "arn:aws:rds:eu-west-1:111122223333:db:x", "ResourceType": "RDS"}]}, {})
    st.add_response("list_backup_plans", {"BackupPlansList": [{"BackupPlanId": "p1", "BackupPlanName": "daily"}]}, {})
    st.add_response("get_backup_plan", {"BackupPlan": {"BackupPlanName": "daily", "Rules": [
        {"RuleName": "r", "TargetBackupVaultName": "v", "CopyActions": [{"DestinationBackupVaultArn": "arn"}]}]}},
        {"BackupPlanId": "p1"})
    st.add_response("list_backup_selections", {"BackupSelectionsList": [{"SelectionId": "s1"}]}, {"BackupPlanId": "p1"})
    st.add_response("get_backup_selection", {"BackupSelection": {"SelectionName": "s", "IamRoleArn": "arn:aws:iam::1:role/r",
        "Resources": ["arn:aws:s3:::orders-prod"]}}, {"BackupPlanId": "p1", "SelectionId": "s1"})
    st.add_response("list_backup_vaults", {"BackupVaultList": [
        {"BackupVaultName": "v", "Locked": False, "NumberOfRecoveryPoints": 12, "VaultType": "BACKUP_VAULT"}]}, {})
    st.add_response("describe_region_settings", {"ResourceTypeOptInPreference": {"S3": False}}, {})
    st.add_response("list_restore_testing_plans", {"RestoreTestingPlans": []}, {})
    st.add_response("list_frameworks", {"Frameworks": []}, {})
    with st:
        p = backup.collect_region(c)
    assert p["protected_s3"] == {"orders-prod": ["daily"]}
    assert p["plans_total"] == 1 and p["plans_with_copy_actions"] == 1
    assert p["vaults"] == [{"name": "v", "locked": False, "recovery_points": 12, "air_gapped": False}]
    assert p["s3_opt_in"] is False and p["restore_testing_plans"] == 0 and p["frameworks"] == 0
    assert p["errors"] == {}


def test_denied_region_settings_is_recorded_as_unknown():
    c = boto3.client("backup", region_name="eu-west-1", aws_access_key_id="x", aws_secret_access_key="x")
    st = Stubber(c)
    st.add_response("list_protected_resources", {"Results": []}, {})
    st.add_response("list_backup_plans", {"BackupPlansList": []}, {})
    st.add_response("list_backup_vaults", {"BackupVaultList": []}, {})
    st.add_client_error("describe_region_settings", "AccessDeniedException", http_status_code=403)
    st.add_response("list_restore_testing_plans", {"RestoreTestingPlans": []}, {})
    st.add_response("list_frameworks", {"Frameworks": []}, {})
    with st:
        p = backup.collect_region(c)
    assert p["s3_opt_in"] is None and p["errors"]["region_settings"] == "AccessDeniedException"


def _client():
    return boto3.client("backup", region_name="eu-west-1", aws_access_key_id="x", aws_secret_access_key="x")


def test_selection_errors_do_not_overwrite_each_other_and_non_s3_arns_are_ignored():
    c = _client()
    st = Stubber(c)
    st.add_response("list_protected_resources", {"Results": []}, {})
    st.add_response("list_backup_plans", {"BackupPlansList": [{"BackupPlanId": "p1", "BackupPlanName": "daily"}]}, {})
    st.add_response("get_backup_plan", {"BackupPlan": {"BackupPlanName": "daily", "Rules": []}}, {"BackupPlanId": "p1"})
    st.add_response("list_backup_selections", {"BackupSelectionsList": [{"SelectionId": "s1"}, {"SelectionId": "s2"}]},
                    {"BackupPlanId": "p1"})
    st.add_client_error("get_backup_selection", "AccessDeniedException", http_status_code=403)
    st.add_client_error("get_backup_selection", "ThrottlingException", http_status_code=400)
    st.add_response("list_backup_vaults", {"BackupVaultList": [{"BackupVaultName": "v"}]}, {})
    st.add_response("describe_region_settings", {"ResourceTypeOptInPreference": {}}, {})
    st.add_response("list_restore_testing_plans", {"RestoreTestingPlans": []}, {})
    st.add_response("list_frameworks", {"Frameworks": []}, {})
    with st:
        p = backup.collect_region(c)
    assert p["errors"] == {"selection:daily:s1": "AccessDeniedException", "selection:daily:s2": "ThrottlingException"}
    assert p["vaults"][0]["recovery_points"] is None


def test_s3_arn_recognition_is_by_service_segment():
    assert backup._s3_names(["arn:aws-cn:s3:::cn-bucket", "arn:aws:rds:eu-west-1:1:db:x", "arn:aws:s3:::b/prefix"]) == [
        "cn-bucket", "b/prefix"]


def test_transport_failure_is_recorded_like_an_api_error():
    from botocore.exceptions import EndpointConnectionError
    errors = {}

    def boom():
        raise EndpointConnectionError(endpoint_url="https://x")

    assert backup._safe(errors, "vaults", boom, []) == []
    assert errors == {"vaults": "EndpointConnectionError"}


def test_unreadable_calls_are_none_not_empty():
    c = _client()
    st = Stubber(c)
    st.add_client_error("list_protected_resources", "AccessDeniedException", http_status_code=403)
    st.add_client_error("list_backup_plans", "AccessDeniedException", http_status_code=403)
    st.add_client_error("list_backup_vaults", "AccessDeniedException", http_status_code=403)
    st.add_response("describe_region_settings", {"ResourceTypeOptInPreference": {"S3": True}}, {})
    st.add_client_error("list_restore_testing_plans", "AccessDeniedException", http_status_code=403)
    st.add_client_error("list_frameworks", "AccessDeniedException", http_status_code=403)
    with st:
        p = backup.collect_region(c)
    assert p["protected_s3"] is None and p["plans_total"] is None and p["plans_with_copy_actions"] is None
    assert p["vaults"] is None and p["restore_testing_plans"] is None and p["frameworks"] is None
    assert p["s3_opt_in"] is True
    assert set(p["errors"]) == {"protected_resources", "plans", "vaults", "restore_testing", "frameworks"}


def test_unreadable_plan_body_makes_copy_count_unknown():
    c = _client()
    st = Stubber(c)
    st.add_response("list_protected_resources", {"Results": []}, {})
    st.add_response("list_backup_plans", {"BackupPlansList": [{"BackupPlanId": "p1", "BackupPlanName": "daily"},
                                                              {"BackupPlanId": "p2", "BackupPlanName": "weekly"}]}, {})
    st.add_response("get_backup_plan", {"BackupPlan": {"BackupPlanName": "daily", "Rules": [
        {"RuleName": "r", "TargetBackupVaultName": "v", "CopyActions": [{"DestinationBackupVaultArn": "arn"}]}]}},
        {"BackupPlanId": "p1"})
    st.add_response("list_backup_selections", {"BackupSelectionsList": []}, {"BackupPlanId": "p1"})
    st.add_client_error("get_backup_plan", "AccessDeniedException", http_status_code=403)
    st.add_response("list_backup_selections", {"BackupSelectionsList": []}, {"BackupPlanId": "p2"})
    st.add_response("list_backup_vaults", {"BackupVaultList": []}, {})
    st.add_response("describe_region_settings", {"ResourceTypeOptInPreference": {}}, {})
    st.add_response("list_restore_testing_plans", {"RestoreTestingPlans": []}, {})
    st.add_response("list_frameworks", {"Frameworks": []}, {})
    with st:
        p = backup.collect_region(c)
    assert p["plans_total"] == 2 and p["plans_with_copy_actions"] is None
    assert p["protected_s3"] == {} and p["vaults"] == [] and p["frameworks"] == 0
    assert p["errors"] == {"plan:weekly": "AccessDeniedException"}
