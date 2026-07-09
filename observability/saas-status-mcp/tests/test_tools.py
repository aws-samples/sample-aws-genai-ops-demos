"""Unit tests for the SaaS Status MCP tools (offline, httpx mocked via respx).

The agent modules use flat imports (designed for the AgentCore zip root), so we
add the agent/ directory to sys.path and import `tools` directly. No network and
no AWS calls: PROVIDERS_BUCKET is unset, so config.py loads the local
providers.json seed.
"""

import json
import sys
from pathlib import Path

import pytest
import respx
from httpx import Response

# Make the flat agent modules importable (config, statuspage_client, tools)
_AGENT_DIR = Path(__file__).resolve().parent.parent / "agent"
sys.path.insert(0, str(_AGENT_DIR))

import tools  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> dict:
    with open(FIXTURES / name, "r") as f:
        return json.load(f)


# ─── list_providers ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_providers_returns_registry():
    result = await tools.list_providers()
    names = [p["name"] for p in result["providers"]]
    assert "snowflake" in names
    assert "datadog" in names
    assert "mongodb" in names
    assert len(names) >= 20  # shipped registry is broad


# ─── get_service_status ───────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_get_service_status_operational():
    respx.get("https://status.snowflake.com/api/v2/status.json").mock(
        return_value=Response(200, json=_fixture("status_operational.json"))
    )
    result = await tools.get_service_status("snowflake")
    assert result["provider"] == "snowflake"
    assert result["status"] == "operational"
    assert result["url"] == "https://status.snowflake.com"


@pytest.mark.asyncio
@respx.mock
async def test_get_service_status_degraded():
    respx.get("https://status.mongodb.com/api/v2/status.json").mock(
        return_value=Response(200, json=_fixture("status_degraded.json"))
    )
    result = await tools.get_service_status("mongodb")
    assert result["status"] == "degraded_performance"


@pytest.mark.asyncio
async def test_get_service_status_unknown_provider():
    result = await tools.get_service_status("nonexistent")
    assert "error" in result
    assert "Unknown provider" in result["error"]


# ─── get_active_events ────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_get_active_events_with_incident():
    respx.get("https://status.mongodb.com/api/v2/incidents/unresolved.json").mock(
        return_value=Response(200, json=_fixture("incidents_unresolved.json"))
    )
    respx.get("https://status.mongodb.com/api/v2/scheduled-maintenances/active.json").mock(
        return_value=Response(200, json={"scheduled_maintenances": []})
    )

    result = await tools.get_active_events("mongodb")
    assert result["total_active"] == 1
    event = result["events"][0]
    assert event["event_type"] == "incident"
    assert event["id"] == "7g5qmxgkc2y4"
    assert event["impact"] == "major"
    assert len(event["affected_components"]) == 2
    assert event["latest_update"]["status"] == "monitoring"
    assert event["updates"] is None  # include_history=False by default


@pytest.mark.asyncio
@respx.mock
async def test_get_active_events_with_maintenance():
    respx.get("https://status.datadoghq.com/api/v2/incidents/unresolved.json").mock(
        return_value=Response(200, json={"incidents": []})
    )
    respx.get("https://status.datadoghq.com/api/v2/scheduled-maintenances/active.json").mock(
        return_value=Response(200, json=_fixture("maintenances_active.json"))
    )

    result = await tools.get_active_events("datadog")
    assert result["total_active"] == 1
    event = result["events"][0]
    assert event["event_type"] == "maintenance"
    assert event["scheduled_for"] == "2026-07-06T06:00:00.000Z"
    assert event["scheduled_until"] == "2026-07-06T10:00:00.000Z"


@pytest.mark.asyncio
@respx.mock
async def test_get_active_events_empty():
    respx.get("https://status.snowflake.com/api/v2/incidents/unresolved.json").mock(
        return_value=Response(200, json={"incidents": []})
    )
    respx.get("https://status.snowflake.com/api/v2/scheduled-maintenances/active.json").mock(
        return_value=Response(200, json={"scheduled_maintenances": []})
    )

    result = await tools.get_active_events("snowflake")
    assert result["total_active"] == 0
    assert result["events"] == []


@pytest.mark.asyncio
@respx.mock
async def test_get_active_events_with_history():
    respx.get("https://status.mongodb.com/api/v2/incidents/unresolved.json").mock(
        return_value=Response(200, json=_fixture("incidents_unresolved.json"))
    )
    respx.get("https://status.mongodb.com/api/v2/scheduled-maintenances/active.json").mock(
        return_value=Response(200, json={"scheduled_maintenances": []})
    )

    result = await tools.get_active_events("mongodb", include_history=True)
    event = result["events"][0]
    assert event["updates"] is not None
    assert len(event["updates"]) == 3


# ─── check_all_dependencies ──────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_check_all_dependencies_mixed():
    # Snowflake: operational, no events
    respx.get("https://status.snowflake.com/api/v2/status.json").mock(
        return_value=Response(200, json=_fixture("status_operational.json"))
    )
    respx.get("https://status.snowflake.com/api/v2/incidents/unresolved.json").mock(
        return_value=Response(200, json={"incidents": []})
    )
    respx.get("https://status.snowflake.com/api/v2/scheduled-maintenances/active.json").mock(
        return_value=Response(200, json={"scheduled_maintenances": []})
    )

    # MongoDB: degraded, 1 incident
    respx.get("https://status.mongodb.com/api/v2/status.json").mock(
        return_value=Response(200, json=_fixture("status_degraded.json"))
    )
    respx.get("https://status.mongodb.com/api/v2/incidents/unresolved.json").mock(
        return_value=Response(200, json=_fixture("incidents_unresolved.json"))
    )
    respx.get("https://status.mongodb.com/api/v2/scheduled-maintenances/active.json").mock(
        return_value=Response(200, json={"scheduled_maintenances": []})
    )

    result = await tools.check_all_dependencies(["snowflake", "mongodb"])
    assert result["any_degraded"] is True
    assert result["degraded_providers"] == ["mongodb"]
    assert len(result["results"]) == 2
    assert result["results"][0]["status"] == "operational"
    assert result["results"][0]["active_events"] == 0
    assert result["results"][1]["status"] == "degraded_performance"
    assert result["results"][1]["active_events"] == 1


@pytest.mark.asyncio
async def test_check_all_dependencies_too_many():
    result = await tools.check_all_dependencies(["a"] * 11)
    assert "error" in result
    assert "Maximum 10" in result["error"]
