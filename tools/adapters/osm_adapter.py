"""
osm_adapter.py
----------------
Two routing adapters, both wrapping already-built/tested modules with
zero duplicated logic:

  OSMRoutingAdapter    - wraps osm_routing_tool.estimate_district_to_district
                         (real Nominatim+OSRM, mock-tested, see AUDIT_REPORT.md)
  StaticHaversineAdapter - wraps district_geo.py's static table + haversine,
                         usable completely independent of OSM (e.g. an
                         explicit "offline demo mode" choice, not just an
                         internal fallback)

Having both as standalone, swappable adapters is the actual point of
this interface: a caller (or a config flag) can pick
StaticHaversineAdapter directly for a guaranteed-fast offline demo, or
chain OSMRoutingAdapter -> StaticHaversineAdapter via FallbackChain for
normal operation. Neither choice requires touching decision_pipeline.py.
"""

from typing import List, Dict, Any
from tools.adapters.base_adapter import SourceAdapter, AdapterError
from tools.adapters.schema import DistanceRecord


class OSMRoutingAdapter(SourceAdapter):
    source_name = "OSM"
    source_type = "public_osm"

    def fetch(self, origin_district: str, destination_district: str) -> List[Dict[str, Any]]:
        from tools.osm_routing_tool import estimate_district_to_district  # lazy: avoids a hard `requests` dependency for callers who only ever use StaticHaversineAdapter

        result = estimate_district_to_district(origin_district, destination_district)
        if "error" in result:
            raise AdapterError(result["error"])

        is_live = result["distance_source"] == "OSRM_LIVE"
        record = DistanceRecord(
            origin=result["origin"],
            destination=result["destination"],
            distance_km=result["distance_km"],
            duration_minutes=result["duration_minutes"],
            source=self.source_name,
            source_type=self.source_type,
            data_status="LIVE" if is_live else "FALLBACK",
            fetched_at=self.now_iso(),
            fallback_reason=None if is_live else "OSRM demo server unavailable/rate-limited/no route -- see osm_routing_tool.py",
        )
        return [record.to_dict()]


class StaticHaversineAdapter(SourceAdapter):
    source_name = "STATIC_HAVERSINE"
    source_type = "derived"

    def fetch(self, origin_district: str, destination_district: str) -> List[Dict[str, Any]]:
        from tools.district_geo import distance_between_districts

        straight_line = distance_between_districts(origin_district, destination_district)
        if straight_line is None:
            raise AdapterError(
                f"district not in static coordinate table: '{origin_district}' or '{destination_district}'"
            )

        record = DistanceRecord(
            origin=origin_district,
            destination=destination_district,
            distance_km=round(straight_line * 1.25, 1),
            duration_minutes=None,
            source=self.source_name,
            source_type=self.source_type,
            data_status="DERIVED",
            fetched_at=self.now_iso(),
        )
        return [record.to_dict()]
