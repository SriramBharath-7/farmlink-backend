"""
osm_routing_tool.py
----------------------
Real OpenStreetMap integration: Nominatim (geocoding) + OSRM (routing),
replacing the static-lookup-table proxy in district_geo.py with an
actual live call -- WITH a mandatory fallback to that same proxy,
because both are public demo servers with an explicit "no uptime
guarantees" usage policy. This is not optional robustness; the policies
say outright these servers can vanish at any time.

Verified against the real, current API docs/usage policies (checked via
web search, not assumed from training data):
  - Nominatim Usage Policy: max 1 request/sec, must send an identifying
    User-Agent (or email), cache results instead of re-querying.
    https://nominatim.org/release-docs/latest/api/Search/
  - OSRM demo server (router.project-osrm.org): max 1 request/sec,
    requires a valid identifying User-Agent, requires ODbL attribution
    in any UI showing the route, "provided on best effort basis, no
    quality/uptime guarantees."
    https://github.com/Project-OSRM/osrm-backend/wiki/Api-usage-policy

IMPORTANT — before using this in production:
  1. Replace USER_AGENT below with your team's real contact info. Faking
     another app's User-Agent gets you blocked per the policy above.
  2. Run scripts/precache_osm_district_coords.py ONCE from a machine
     with normal internet access (this sandbox's egress allowlist does
     not include these two domains) to build the coordinate cache, so
     runtime traffic to Nominatim is near-zero (districts don't move).
  3. Display OSM/ODbL attribution wherever a route/map is shown to the
     user, per the OSRM usage policy.

Because these are shared public demo servers, EVERY call in this module
has a strict timeout and an unconditional fallback to the haversine
proxy already used elsewhere in this project (district_geo.py). A
farmer-facing recommendation must never fail because a free demo server
is slow or down.
"""

import json
import os
import time
import requests
from typing import Optional, Dict, Tuple
from tools.district_geo import DISTRICT_COORDS, haversine_km

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OSRM_ROUTE_URL_TEMPLATE = "https://router.project-osrm.org/route/v1/driving/{coords}"

# REQUIRED by both services' usage policies -- replace with a real
# contact before deploying. Faking a generic/browser User-Agent is
# explicitly against policy and risks an IP ban for the whole team.
USER_AGENT = "FarmLink-SIH26132/1.0 (contact: REPLACE_WITH_TEAM_EMAIL)"

REQUEST_TIMEOUT_SECONDS = 6
MIN_SECONDS_BETWEEN_CALLS = 1.05  # policy says max 1/sec; pad slightly

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
GEOCODE_CACHE_PATH = os.path.join(BASE_DIR, "..", "data", "osm_geocode_cache.json")

_last_call_ts = 0.0


def _respect_rate_limit():
    global _last_call_ts
    elapsed = time.time() - _last_call_ts
    if elapsed < MIN_SECONDS_BETWEEN_CALLS:
        time.sleep(MIN_SECONDS_BETWEEN_CALLS - elapsed)
    _last_call_ts = time.time()


def _load_geocode_cache() -> Dict:
    if os.path.exists(GEOCODE_CACHE_PATH):
        with open(GEOCODE_CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_geocode_cache(cache: Dict):
    os.makedirs(os.path.dirname(GEOCODE_CACHE_PATH), exist_ok=True)
    with open(GEOCODE_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)


def geocode_district(district: str, state: str = "Maharashtra", country: str = "India",
                      use_cache: bool = True) -> Dict:
    """
    Resolves a district name to (lat, lon) via Nominatim, caching to disk
    so repeated calls for the same district (there are ~36 Maharashtra
    districts total -- a closed, small set) don't re-hit the live API.

    Falls back to the static DISTRICT_COORDS table in district_geo.py if
    the live call fails for any reason (timeout, rate limit, malformed
    response, network unavailable) or the district isn't recognized by
    Nominatim under this query. This function NEVER raises for a network
    failure -- it degrades and tags the source instead.

    Returns:
        {"lat": float, "lon": float, "source": "OSM_LIVE"|"STATIC_FALLBACK"|"NOT_FOUND"}
    """
    cache = _load_geocode_cache() if use_cache else {}
    if use_cache and district in cache:
        return {**cache[district], "source": cache[district].get("source", "OSM_LIVE") + "_CACHED"}

    query = f"{district}, {state}, {country}"
    try:
        _respect_rate_limit()
        resp = requests.get(
            NOMINATIM_URL,
            params={"q": query, "format": "json", "limit": 1, "countrycodes": "in"},
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        results = resp.json()
        if results:
            lat, lon = float(results[0]["lat"]), float(results[0]["lon"])
            record = {"lat": lat, "lon": lon, "source": "OSM_LIVE", "matched_display_name": results[0].get("display_name")}
            if use_cache:
                cache[district] = record
                _save_geocode_cache(cache)
            return record
    except (requests.RequestException, ValueError, KeyError, IndexError):
        pass  # fall through to static fallback below -- never raise

    static = DISTRICT_COORDS.get(district.strip().title())
    if static:
        return {"lat": static[0], "lon": static[1], "source": "STATIC_FALLBACK"}
    return {"lat": None, "lon": None, "source": "NOT_FOUND"}


def get_road_route(origin_latlon: Tuple[float, float], dest_latlon: Tuple[float, float]) -> Dict:
    """
    Real road-network distance/duration via the OSRM public demo server.
    Falls back to the haversine x1.25 proxy (same formula already used
    in logistics_tools.py/logistics_core.py) on ANY failure -- this
    server has an explicit "no guarantees" policy, so a hard dependency
    here would make the whole sell-decision pipeline as unreliable as a
    free demo server, which is not acceptable for a farmer-facing tool.

    Args:
        origin_latlon: (lat, lon)
        dest_latlon: (lat, lon)

    Returns:
        {"distance_km": float, "duration_minutes": float|None, "source": "OSRM_LIVE"|"HAVERSINE_FALLBACK"}
    """
    lat1, lon1 = origin_latlon
    lat2, lon2 = dest_latlon

    if None in (lat1, lon1, lat2, lon2):
        return {"distance_km": None, "duration_minutes": None, "source": "UNRESOLVED_COORDINATES"}

    try:
        _respect_rate_limit()
        coords = f"{lon1},{lat1};{lon2},{lat2}"  # OSRM wants lon,lat order
        url = OSRM_ROUTE_URL_TEMPLATE.format(coords=coords)
        resp = requests.get(
            url,
            params={"overview": "false", "alternatives": "false"},
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("code") == "Ok" and payload.get("routes"):
            route = payload["routes"][0]
            return {
                "distance_km": round(route["distance"] / 1000, 1),
                "duration_minutes": round(route["duration"] / 60, 1),
                "source": "OSRM_LIVE",
                "attribution": "Route data (c) OpenStreetMap contributors, ODbL",
            }
    except (requests.RequestException, ValueError, KeyError, IndexError):
        pass  # fall through -- never raise

    straight_line = haversine_km((lat1, lon1), (lat2, lon2))
    return {
        "distance_km": round(straight_line * 1.25, 1),
        "duration_minutes": None,
        "source": "HAVERSINE_FALLBACK",
        "fallback_reason": "OSRM demo server unavailable, rate-limited, or returned no route",
    }


def estimate_district_to_district(origin_district: str, destination_district: str) -> Dict:
    """
    Convenience wrapper: geocode both districts, then route between them.
    This is the function decision_pipeline.py / logistics_core.py should
    call to replace the pure-haversine estimate_distance() -- same
    output shape (distance_km present), plus explicit source tagging.
    """
    origin_geo = geocode_district(origin_district)
    dest_geo = geocode_district(destination_district)

    if origin_geo["source"] == "NOT_FOUND" or dest_geo["source"] == "NOT_FOUND":
        return {
            "error": f"Could not resolve one or both districts: '{origin_district}', '{destination_district}'",
            "origin": origin_district, "destination": destination_district,
        }

    route = get_road_route((origin_geo["lat"], origin_geo["lon"]), (dest_geo["lat"], dest_geo["lon"]))
    return {
        "origin": origin_district,
        "destination": destination_district,
        "distance_km": route["distance_km"],
        "duration_minutes": route["duration_minutes"],
        "distance_source": route["source"],
        "origin_geocode_source": origin_geo["source"],
        "destination_geocode_source": dest_geo["source"],
    }
