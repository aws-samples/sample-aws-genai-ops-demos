"""Unit tests for the list_regions action (Settings regions dropdown). Offline."""
from unittest.mock import MagicMock, patch

import account_discovery


def test_list_enabled_regions_sorts_and_omits_all_regions():
    ec2 = MagicMock()
    ec2.describe_regions.return_value = {"Regions": [
        {"RegionName": "us-west-2"}, {"RegionName": "eu-central-1"}, {"RegionName": "ap-southeast-1"}]}
    with patch("aws_utils.get_region", return_value="eu-central-1"), \
            patch.object(account_discovery.boto3, "client", return_value=ec2) as client:
        out = account_discovery.list_enabled_regions()
    assert out == {"success": True, "regions": ["ap-southeast-1", "eu-central-1", "us-west-2"],
                   "deploy_region": "eu-central-1"}
    client.assert_called_once_with("ec2", region_name="eu-central-1")
    # No AllRegions: disabled opt-in regions cannot be scanned, so they must not be offered.
    ec2.describe_regions.assert_called_once()
    assert ec2.describe_regions.call_args.kwargs == {}


def test_list_enabled_regions_reports_failure_without_a_fallback_list():
    with patch("aws_utils.get_region", return_value="eu-central-1"), \
            patch.object(account_discovery.boto3, "client", side_effect=RuntimeError("no creds")):
        out = account_discovery.list_enabled_regions()
    assert out["success"] is False
    assert "no creds" in out["error"]
    assert "regions" not in out


def test_list_regions_action_routes_to_account_discovery():
    from actions import handle_api_action
    with patch.object(account_discovery, "list_enabled_regions", return_value={"success": True, "regions": []}) as fn:
        out = handle_api_action("list_regions", {})
    fn.assert_called_once_with()
    assert out == {"success": True, "regions": []}
