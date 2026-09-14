"""
schema.py
-----------
Common record shapes every adapter normalizes into, so a caller never
needs to know which adapter produced a value to know how trustworthy it
is. This directly implements the spec's requirement (section 4):

    "Every data record should retain provenance ... The system must
    ALWAYS distinguish LIVE / CACHED / SYNTHETIC / DEMO / FALLBACK."

DATA_STATUS values (extended by one beyond the original spec list, noted
below):
  LIVE      - fetched from the real external source just now
  CACHED    - a previously-fetched LIVE value, served from a local cache
  SYNTHETIC - demo/placeholder data, never claimed to be real
  DEMO      - explicitly-curated demo data for a presentation scenario
  FALLBACK  - the adapter tried a live/primary path and fell back to a
              secondary one (e.g. OSM routing failed -> haversine)
  DERIVED   - EXTENSION beyond the original spec list: a value computed
              from other LIVE/SYNTHETIC data (e.g. a moving average, or
              a standalone haversine estimate never even attempting a
              live call). Distinct from FALLBACK because nothing failed
              -- this is the adapter's normal, intended behavior.
"""

from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Optional, Any

VALID_DATA_STATUSES = {"LIVE", "CACHED", "SYNTHETIC", "DEMO", "FALLBACK", "DERIVED"}
VALID_SOURCE_TYPES = {"government", "public_osm", "synthetic", "derived"}


def _validate_status(status: str):
    if status not in VALID_DATA_STATUSES:
        raise ValueError(f"invalid data_status '{status}', must be one of {sorted(VALID_DATA_STATUSES)}")


def now_iso() -> str:
    return datetime.now().isoformat()


@dataclass
class PriceRecord:
    """Normalized shape for any commodity-price-producing adapter."""
    commodity: str
    market: str
    district: str
    state: str
    modal_price: float
    min_price: float
    max_price: float
    unit: str
    arrival_date: str
    source: str
    source_type: str
    data_status: str
    fetched_at: str
    fallback_reason: Optional[str] = None

    def __post_init__(self):
        _validate_status(self.data_status)

    def to_dict(self):
        return asdict(self)


@dataclass
class DistanceRecord:
    """Normalized shape for any distance/routing-producing adapter."""
    origin: str
    destination: str
    distance_km: float
    duration_minutes: Optional[float]
    source: str
    source_type: str
    data_status: str
    fetched_at: str
    fallback_reason: Optional[str] = None

    def __post_init__(self):
        _validate_status(self.data_status)

    def to_dict(self):
        return asdict(self)
