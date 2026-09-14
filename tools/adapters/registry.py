"""
registry.py
-------------
Small factory so callers (decision_pipeline.py, api_v2.py, future code)
request a data source BY NAME/CATEGORY rather than importing and wiring
adapter classes directly everywhere. This is what makes "adding a third
source" a one-line registry change instead of a hunt-and-replace across
the codebase.
"""

from typing import List
from tools.adapters.fallback_chain import FallbackChain
from tools.adapters.agmarknet_adapter import AgmarknetAdapter
from tools.adapters.osm_adapter import OSMRoutingAdapter, StaticHaversineAdapter

PRICE_ADAPTERS = {
    "agmarknet": AgmarknetAdapter,
}

ROUTING_ADAPTERS = {
    "osm": OSMRoutingAdapter,
    "static_haversine": StaticHaversineAdapter,
}


def get_price_chain(sources: List[str] = None) -> FallbackChain:
    """Default: just Agmarknet (which has its own internal live->synthetic
    fallback already). Pass e.g. ["agmarknet"] explicitly, or extend
    PRICE_ADAPTERS + this list when a second real price source exists."""
    sources = sources or ["agmarknet"]
    adapters = [PRICE_ADAPTERS[name]() for name in sources]
    return FallbackChain(adapters)


def get_routing_chain(sources: List[str] = None) -> FallbackChain:
    """Default priority: try live OSM first, fall back to the static
    haversine proxy. Pass ["static_haversine"] alone for a guaranteed-
    fast, guaranteed-offline demo mode."""
    sources = sources or ["osm", "static_haversine"]
    adapters = [ROUTING_ADAPTERS[name]() for name in sources]
    return FallbackChain(adapters)
