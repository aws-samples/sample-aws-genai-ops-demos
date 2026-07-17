"""Tool implementations for the SaaS Status MCP server.

Four provider-agnostic tools:
- list_providers
- get_service_status
- get_active_events   (core investigation tool)
- check_all_dependencies
"""

import asyncio
import logging
from typing import Any

from config import get_provider, get_provider_names, get_providers
from statuspage_client import (
    fetch_incidents_unresolved,
    fetch_maintenances_active,
    fetch_status,
)

logger = logging.getLogger(__name__)

MAX_PROVIDERS = 10

# Statuspage.io top-level status.json indicators that mean "healthy"
HEALTHY_INDICATORS = {"none"}


# ─── list_providers ───────────────────────────────────────────────

async def list_providers() -> dict:
    """Return every provider configured in providers.json (local read)."""
    return {
        "providers": [
            {"name": p["name"], "display_name": p["display_name"], "url": p["url"]}
            for p in get_providers()
        ]
    }


# ─── get_service_status ───────────────────────────────────────────

async def get_service_status(provider: str) -> dict:
    """Current overall operational status for one provider."""
    provider_config = get_provider(provider)
    if provider_config is None:
        return {"error": f"Unknown provider '{provider}'. Available: {get_provider_names()}"}

    try:
        data = await fetch_status(provider_config["api_base"])
        status_info = data.get("status", {})
        page_info = data.get("page", {})
        # Statuspage.io uses indicator "none" to mean all-operational; map it for clarity
        indicator = status_info.get("indicator", "unknown")
        status = "operational" if indicator == "none" else indicator
        return {
            "provider": provider_config["name"],
            "status": status,
            "description": status_info.get("description", ""),
            "last_updated": page_info.get("updated_at", ""),
            "url": provider_config["url"],
        }
    except Exception as e:
        logger.error("Failed to fetch status for %s: %s", provider, e)
        return {
            "provider": provider_config["name"],
            "status": "error",
            "description": f"Failed to reach status page: {e}",
            "last_updated": "",
            "url": provider_config["url"],
        }


# ─── get_active_events ─────────────────────────────────────────────

async def get_active_events(provider: str, include_history: bool = False) -> dict:
    """All active events (unresolved incidents + active maintenances) for a provider."""
    provider_config = get_provider(provider)
    if provider_config is None:
        return {"error": f"Unknown provider '{provider}'. Available: {get_provider_names()}"}

    api_base = provider_config["api_base"]
    events: list[dict] = []

    try:
        incidents_data, maintenances_data = await asyncio.gather(
            fetch_incidents_unresolved(api_base),
            fetch_maintenances_active(api_base),
            return_exceptions=True,
        )

        if isinstance(incidents_data, dict):
            for incident in incidents_data.get("incidents", []):
                events.append(_normalize(incident, provider_config["name"], "incident", include_history))
        else:
            logger.warning("Failed incidents fetch for %s: %s", provider, incidents_data)

        if isinstance(maintenances_data, dict):
            for maint in maintenances_data.get("scheduled_maintenances", []):
                events.append(_normalize(maint, provider_config["name"], "maintenance", include_history))
        else:
            logger.warning("Failed maintenances fetch for %s: %s", provider, maintenances_data)

    except Exception as e:
        logger.error("Unexpected error fetching events for %s: %s", provider, e)
        return {"provider": provider_config["name"], "events": [], "total_active": 0, "error": str(e)}

    return {"provider": provider_config["name"], "events": events, "total_active": len(events)}


def _normalize(item: dict[str, Any], provider_name: str, event_type: str, include_history: bool) -> dict:
    """Normalize a raw incident or maintenance into the unified event shape."""
    updates = item.get("incident_updates", [])
    latest = updates[0] if updates else None

    event = {
        "event_type": event_type,
        "provider": provider_name,
        "id": item.get("id", ""),
        "name": item.get("name", ""),
        "status": item.get("status", ""),
        "impact": item.get("impact", "none" if event_type == "incident" else "maintenance"),
        "created_at": item.get("created_at", ""),
        "updated_at": item.get("updated_at", ""),
        "started_at": item.get("started_at") if event_type == "incident" else None,
        "resolved_at": item.get("resolved_at") if event_type == "incident" else None,
        "scheduled_for": item.get("scheduled_for") if event_type == "maintenance" else None,
        "scheduled_until": item.get("scheduled_until") if event_type == "maintenance" else None,
        "shortlink": item.get("shortlink", ""),
        "affected_components": [
            {"name": c.get("name", ""), "status": c.get("status", "")}
            for c in item.get("components", [])
        ],
        "latest_update": (
            {
                "status": latest.get("status", ""),
                "body": latest.get("body", ""),
                "created_at": latest.get("created_at", ""),
            }
            if latest
            else None
        ),
    }

    if include_history:
        event["updates"] = [
            {"status": u.get("status", ""), "body": u.get("body", ""), "created_at": u.get("created_at", "")}
            for u in updates
        ]
    else:
        event["updates"] = None

    return event


# ─── check_all_dependencies ────────────────────────────────────────

async def check_all_dependencies(providers: list[str]) -> dict:
    """Bulk-check status + active event count across multiple providers in parallel."""
    if len(providers) > MAX_PROVIDERS:
        return {"error": f"Maximum {MAX_PROVIDERS} providers per call. Got {len(providers)}."}

    available = get_provider_names()
    invalid = [p for p in providers if p.lower().strip() not in available]
    if invalid:
        return {"error": f"Unknown provider(s): {invalid}. Available: {available}"}

    results = await asyncio.gather(*[_check_single(p) for p in providers], return_exceptions=True)

    processed = []
    degraded = []
    for name, result in zip(providers, results):
        if isinstance(result, Exception):
            logger.error("Error checking %s: %s", name, result)
            processed.append({"provider": name, "status": "error", "active_events": 0, "error": str(result)})
        else:
            processed.append(result)
            # Statuspage.io indicator "none" == all operational; anything else (minor/major/critical) is degraded
            if result["status"] not in ("none", "operational"):
                degraded.append(result["provider"])

    return {
        "results": processed,
        "any_degraded": len(degraded) > 0,
        "degraded_providers": degraded,
    }


async def _check_single(provider: str) -> dict:
    status_result, events_result = await asyncio.gather(
        get_service_status(provider),
        get_active_events(provider),
    )
    return {
        "provider": status_result["provider"],
        "status": status_result["status"],
        "active_events": events_result.get("total_active", 0),
    }
