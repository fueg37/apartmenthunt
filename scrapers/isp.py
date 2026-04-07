"""FCC Broadband Map API lookup for ISP availability at a given lat/lon."""
from __future__ import annotations

import httpx

# FCC technology codes → human-readable type
_TECH_NAMES: dict[int, str] = {
    10: "DSL",
    20: "cable",       # HFC (hybrid fiber-coaxial)
    30: "cable",       # other wireline
    40: "cable",       # cable (DOCSIS 3.x)
    50: "fiber",       # fiber to the premises
    60: "satellite",
    61: "licensed fixed wireless",
    70: "fixed_wireless",
    71: "fixed_wireless",
    300: "cable",      # optical carrier / fiber to node
    400: "copper",
}


async def fetch_isp_availability(lat: float, lon: float) -> dict:
    """Query FCC Broadband Map for residential fixed broadband at lat/lon.

    Returns a structured dict:
    {
        "providers": [{"name": str, "type": str, "down": int, "up": int}, ...],
        "best_down_mbps": int,
        "has_fiber": bool,
    }
    """
    url = "https://broadbandmap.fcc.gov/api/public/map/listAvailability"
    body = {
        "latitude": lat,
        "longitude": lon,
        "unit": 0,
        "category": "Residential Fixed Broadband",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=body)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(
            f"FCC API returned {exc.response.status_code} — "
            "check https://broadbandmap.fcc.gov manually"
        ) from exc
    except Exception as exc:
        raise RuntimeError(f"FCC API request failed: {exc}") from exc

    availability = data.get("availability") or []

    providers: list[dict] = []
    seen: set[tuple] = set()

    for entry in availability:
        provider_name = (
            entry.get("brand_name")
            or entry.get("holding_company")
            or "Unknown ISP"
        )
        tech_code = int(entry.get("technology", 0))
        max_down = int(entry.get("max_advertised_download_speed") or 0)
        max_up = int(entry.get("max_advertised_upload_speed") or 0)

        # Only count actual broadband (FCC defines ≥25 Mbps down / ≥3 Mbps up)
        if max_down < 25:
            continue

        tech_name = _TECH_NAMES.get(tech_code, "other")
        key = (provider_name.lower(), tech_name)
        if key in seen:
            continue
        seen.add(key)

        providers.append({"name": provider_name, "type": tech_name, "down": max_down, "up": max_up})

    # Sort: fiber first, then by download speed descending
    providers.sort(key=lambda p: (p["type"] != "fiber", -p["down"]))

    has_fiber = any(p["type"] == "fiber" for p in providers)
    best_down = max((p["down"] for p in providers), default=0)

    return {
        "providers": providers,
        "best_down_mbps": best_down,
        "has_fiber": has_fiber,
    }
