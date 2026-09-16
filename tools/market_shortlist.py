"""Bounded, network-free market selection; lanes never determine final rank."""
from datetime import datetime
from tools.location_lookup import normalize_location
from tools.district_geo import haversine_km
from tools.osm_routing_tool import cached_district_coordinates


def shortlist_markets(grouped, origin, quantity, coordinate_cache):
    candidates = []
    origin_coords = cached_district_coordinates(*origin, coordinate_cache)
    for identity, records in grouped.items():
        latest = sorted(records, key=lambda r: datetime.strptime(r["arrival_date"], "%d/%m/%Y"))[-1]
        location = normalize_location(latest["state"], latest["district"])
        coords = cached_district_coordinates(*location, coordinate_cache)
        candidates.append({
            "identity": identity, "records": records,
            "current_modal": float(latest["modal_price"]),
            "gross": float(latest["modal_price"]) * quantity,
            "location": location,
            "proximity": haversine_km(origin_coords, coords) if origin_coords and coords else None,
        })
    economic_key = lambda c: (-c["gross"], c["identity"])
    local = sorted((c for c in candidates if c["location"][0] == origin[0]),
                   key=lambda c: (c["location"] != origin, *economic_key(c)))
    geographic = sorted((c for c in candidates if c["proximity"] is not None),
                        key=lambda c: (c["proximity"], *economic_key(c)))
    national = sorted(candidates, key=economic_key)
    selected, seen = [], set()

    def take(lane, count):
        added = 0
        for candidate in lane:
            if added >= count or len(selected) >= 12:
                break
            if candidate["identity"] not in seen:
                seen.add(candidate["identity"])
                selected.append(candidate)
                added += 1

    take(local, 3)
    take(geographic, 6)
    take(national, 3)
    take(geographic, 12 - len(selected))
    take(national, 12 - len(selected))
    return selected
