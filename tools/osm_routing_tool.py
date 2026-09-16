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
import math
import threading
import requests
from typing import Optional, Dict, Tuple
from tools.district_geo import DISTRICT_COORDS, haversine_km
from tools.location_lookup import normalize_location

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
_request_start_lock = threading.Lock()


def _respect_rate_limit():
    global _last_call_ts
    elapsed = time.time() - _last_call_ts
    if elapsed < MIN_SECONDS_BETWEEN_CALLS:
        time.sleep(MIN_SECONDS_BETWEEN_CALLS - elapsed)
    _last_call_ts = time.time()


def _load_geocode_cache() -> Dict:
    try:
        with open(GEOCODE_CACHE_PATH, "r", encoding="utf-8") as f:
            cache = json.load(f)
            return cache if isinstance(cache, dict) else {}
    except (OSError, ValueError):
        pass
    return {}


def _save_geocode_cache(cache: Dict):
    try:
        os.makedirs(os.path.dirname(GEOCODE_CACHE_PATH), exist_ok=True)
        with open(GEOCODE_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2, ensure_ascii=False)
    except OSError:
        pass  # A read-only cache must not discard a successful geocode.


def _valid_coords(record):
    try:
        lat, lon = float(record["lat"]), float(record["lon"])
        return math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180
    except (TypeError, ValueError, KeyError):
        return False


class ExternalWorkExhausted(Exception):
    pass


class DiscoveryRoutingBudget:
    """One discovery request's network allowance and geocode memo."""
    def __init__(self, max_attempts=12, seconds=15):
        self.max_attempts = max_attempts
        self.deadline = time.monotonic() + seconds
        self.attempts = 0
        self.exhaustion_reason = None
        self.geocodes = {}

    def timeout(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            self.exhaustion_reason = "DEADLINE_EXHAUSTED"
        elif self.attempts >= self.max_attempts:
            self.exhaustion_reason = "HTTP_BUDGET_EXHAUSTED"
        if self.exhaustion_reason:
            raise ExternalWorkExhausted(self.exhaustion_reason)
        return min(REQUEST_TIMEOUT_SECONDS, remaining)


def _http_get(url, budget=None, **kwargs):
    if budget is None:
        _request_start_lock.acquire()
    else:
        budget.timeout()
        if not _request_start_lock.acquire(timeout=max(0, budget.deadline - time.monotonic())):
            budget.exhaustion_reason = "DEADLINE_EXHAUSTED"
            raise ExternalWorkExhausted(budget.exhaustion_reason)
    try:
        if budget is not None:
            budget.timeout()
        _respect_rate_limit()
        timeout = budget.timeout() if budget is not None else REQUEST_TIMEOUT_SECONDS
        if budget is not None:
            budget.attempts += 1
    finally:
        _request_start_lock.release()
    # Redirects must not create uncounted HTTP requests.
    return requests.get(url, timeout=timeout, **({"allow_redirects": False} if budget else {}), **kwargs)


def cached_district_coordinates(state, district, cache):
    """Pure cache/static lookup: never invokes Nominatim."""
    state, district = normalize_location(state, district)
    key = json.dumps(["india", state.casefold(), district.casefold()])
    record = cache.get(key)
    if _valid_coords(record):
        return float(record["lat"]), float(record["lon"])
    return DISTRICT_COORDS.get(district) if state == "Maharashtra" else None


def geocode_district(district: str, state: str = "Maharashtra", country: str = "India",
                     use_cache: bool = True, budget=None) -> Dict:
    key = (*normalize_location(state, district), country.strip().casefold())
    if budget is not None and key in budget.geocodes:
        return budget.geocodes[key]
    result = _geocode_district(district, state, country, use_cache, budget)
    if budget is not None:
        budget.geocodes[key] = result
    return result


def _geocode_district(district: str, state: str = "Maharashtra", country: str = "India",
                      use_cache: bool = True, budget=None) -> Dict:
    """
    Resolves a district name to (lat, lon) via Nominatim, caching to disk
    by normalized country/state/district so repeated lookups avoid HTTP.
    District-only legacy cache entries are deliberately ignored.

    Falls back to the static DISTRICT_COORDS table in district_geo.py if
    the state is Maharashtra and the live call fails (timeout, rate limit, malformed
    response, network unavailable) or the district isn't recognized by
    Nominatim under this query. This function NEVER raises for a network
    failure -- it degrades and tags the source instead.

    Returns:
        {"lat": float, "lon": float, "source": "OSM_LIVE"|"STATIC_FALLBACK"|"NOT_FOUND"}
    """
    state, district = normalize_location(state, district)
    country = " ".join(country.split()).title()
    if not state or not district:
        return {"lat": None, "lon": None, "source": "NOT_FOUND"}
    cache_key = json.dumps([country.casefold(), state.casefold(), district.casefold()])
    cache = _load_geocode_cache() if use_cache else {}
    # Legacy district-only keys cannot prove which state was queried.
    if cache_key in cache and _valid_coords(cache[cache_key]):
        return {**cache[cache_key], "lat": float(cache[cache_key]["lat"]),
                "lon": float(cache[cache_key]["lon"]), "source": "OSM_LIVE_CACHED"}

    budget_reason = None
    query = f"{district}, {state}, {country}"
    try:
        resp = _http_get(
            NOMINATIM_URL, budget=budget,
            params={"q": query, "format": "json", "limit": 1, "countrycodes": "in"},
            headers={"User-Agent": USER_AGENT},
        )
        resp.raise_for_status()
        results = resp.json()
        if isinstance(results, list) and results and _valid_coords(results[0]):
            lat, lon = float(results[0]["lat"]), float(results[0]["lon"])
            record = {"lat": lat, "lon": lon, "source": "OSM_LIVE", "matched_display_name": results[0].get("display_name")}
            if use_cache:
                cache[cache_key] = record
                _save_geocode_cache(cache)
            return record
    except ExternalWorkExhausted as exc:
        budget_reason = str(exc)
    except (requests.RequestException, ValueError, KeyError, IndexError, TypeError):
        pass  # fall through to static fallback below -- never raise

    static = DISTRICT_COORDS.get(district) if state == "Maharashtra" and country == "India" else None
    if static:
        return {"lat": static[0], "lon": static[1], "source": "STATIC_FALLBACK", "budget_reason": budget_reason}
    return {"lat": None, "lon": None, "source": "NOT_FOUND", "budget_reason": budget_reason}


def get_road_route(origin_latlon: Tuple[float, float], dest_latlon: Tuple[float, float], budget=None) -> Dict:
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

    budget_reason = None
    try:
        coords = f"{lon1},{lat1};{lon2},{lat2}"  # OSRM wants lon,lat order
        url = OSRM_ROUTE_URL_TEMPLATE.format(coords=coords)
        resp = _http_get(
            url, budget=budget,
            params={"overview": "false", "alternatives": "false"},
            headers={"User-Agent": USER_AGENT},
        )
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("code") == "Ok" and payload.get("routes"):
            route = payload["routes"][0]
            distance, duration = float(route["distance"]), float(route["duration"])
            if not all(math.isfinite(v) and v >= 0 for v in (distance, duration)):
                raise ValueError("Invalid route measurements")
            return {
                "distance_km": round(distance / 1000, 1),
                "duration_minutes": round(duration / 60, 1),
                "source": "OSRM_LIVE",
                "attribution": "Route data (c) OpenStreetMap contributors, ODbL",
            }
    except ExternalWorkExhausted as exc:
        budget_reason = str(exc)
    except (requests.RequestException, ValueError, KeyError, IndexError, TypeError, AttributeError):
        pass  # fall through -- never raise

    straight_line = haversine_km((lat1, lon1), (lat2, lon2))
    return {
        "distance_km": round(straight_line * 1.25, 1),
        "duration_minutes": None,
        "source": "HAVERSINE_FALLBACK",
        "fallback_reason": budget_reason or "OSRM demo server unavailable, rate-limited, or returned no route",
        "budget_reason": budget_reason,
    }


def estimate_district_to_district(origin_district: str, destination_district: str,
                                  origin_state: str = "Maharashtra",
                                  destination_state: str = "Maharashtra", budget=None) -> Dict:
    """
    Convenience wrapper: geocode both districts, then route between them.
    This is the function decision_pipeline.py / logistics_core.py should
    call to replace the pure-haversine estimate_distance() -- same
    output shape (distance_km present), plus explicit source tagging.
    """
    origin_geo = geocode_district(origin_district, state=origin_state, budget=budget)
    dest_geo = (geocode_district(destination_district, state=destination_state, budget=budget)
                if origin_geo["source"] != "NOT_FOUND" else origin_geo)

    if origin_geo["source"] == "NOT_FOUND" or dest_geo["source"] == "NOT_FOUND":
        return {
            "error": f"Could not resolve one or both districts: '{origin_district}', '{destination_district}'",
            "origin": origin_district, "destination": destination_district,
            "distance_km": None,
            "distance_source": origin_geo.get("budget_reason") or dest_geo.get("budget_reason") or "UNRESOLVED_COORDINATES",
            "budget_reason": origin_geo.get("budget_reason") or dest_geo.get("budget_reason"),
        }

    route = get_road_route((origin_geo["lat"], origin_geo["lon"]), (dest_geo["lat"], dest_geo["lon"]), budget=budget)
    return {
        "origin": origin_district,
        "destination": destination_district,
        "distance_km": route["distance_km"],
        "duration_minutes": route["duration_minutes"],
        "distance_source": route["source"],
        "budget_reason": route.get("budget_reason") or origin_geo.get("budget_reason") or dest_geo.get("budget_reason"),
        "origin_geocode_source": origin_geo["source"],
        "destination_geocode_source": dest_geo["source"],
    }
