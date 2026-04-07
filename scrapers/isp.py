"""ISP availability helper — generates FCC Broadband Map URL for a lat/lon.

The FCC listAvailability API requires account authentication and is not
publicly accessible without credentials. Instead, we surface a direct link
to the FCC Broadband Map website so the user can check availability manually
and record what they find as free-text notes.
"""
from __future__ import annotations


def fcc_map_url(lat: float, lon: float) -> str:
    """Return a direct URL to the FCC Broadband Map centered on lat/lon."""
    return (
        f"https://broadbandmap.fcc.gov/home"
        f"?location_id=&addr=&city=&state=FL&zip="
        f"&lat={lat}&lon={lon}&zoom=14"
    )
