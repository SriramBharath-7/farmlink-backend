"""
precache_osm_district_coords.py
-----------------------------------
ONE-TIME SETUP SCRIPT. Run this from a machine with normal internet
access (NOT this sandbox -- its egress allowlist doesn't include
nominatim.openstreetmap.org). This cannot be executed or verified live
in this session; it's written against the confirmed real Nominatim API
contract (see osm_routing_tool.py's module docstring for sources).

What it does:
  Geocodes every Maharashtra district already listed in district_geo.py
  via live Nominatim calls (respecting the 1 req/sec policy), and writes
  the results to data/osm_geocode_cache.json. After this runs once,
  osm_routing_tool.geocode_district() reads from that cache instead of
  hitting the live API on every request -- there are only ~33 districts
  and they don't move, so there's no reason to re-query at runtime.

Run:
    python3 tools/precache_osm_district_coords.py

Before running:
  1. Set a real contact in osm_routing_tool.USER_AGENT.
  2. Confirm you have normal internet access (this will fail cleanly,
     via the same fallback path, if you don't -- but the point of this
     script is to populate the cache, so it needs to actually reach
     Nominatim at least once per district).

After running, spot-check a few entries against the static
DISTRICT_COORDS table in district_geo.py -- they should be close (same
city/district headquarters) but won't be identical, since Nominatim
resolves to OSM's centroid/administrative-boundary match rather than
the hand-picked city-center points already in that table.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools.district_geo import DISTRICT_COORDS
from tools.osm_routing_tool import geocode_district, USER_AGENT


def main():
    if "REPLACE_WITH_TEAM_EMAIL" in USER_AGENT:
        print("STOP: set a real contact in osm_routing_tool.USER_AGENT before "
              "running this against the live Nominatim server. This is required "
              "by Nominatim's Usage Policy, not optional politeness.")
        sys.exit(1)

    districts = sorted(DISTRICT_COORDS.keys())
    print(f"Geocoding {len(districts)} districts via live Nominatim (this will "
          f"take >= {len(districts)} seconds due to the 1 req/sec rate limit)...")

    results = {}
    for i, district in enumerate(districts, 1):
        result = geocode_district(district, use_cache=True)  # True so each result persists to disk immediately
        results[district] = result
        status = result["source"]
        print(f"  [{i}/{len(districts)}] {district}: {status} "
              f"({result.get('lat')}, {result.get('lon')})")
        if status == "STATIC_FALLBACK":
            print(f"    -- live lookup failed, fell back to the static table entry")

    live_count = sum(1 for r in results.values() if r["source"] == "OSM_LIVE")
    print(f"\nDone. {live_count}/{len(districts)} resolved live via Nominatim.")
    print("Cache written to data/osm_geocode_cache.json.")


if __name__ == "__main__":
    main()
