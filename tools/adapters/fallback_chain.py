"""
fallback_chain.py
--------------------
Generalizes the "try live, fall back to synthetic" pattern that was
previously hand-written separately inside mandi_price_tool.py and
osm_routing_tool.py, so any adapter category can express a priority
order once instead of re-implementing the try/except dance.

This directly implements the spec's section 5 ("Data Source Priority"):
    Primary official source -> Secondary official source
        -> Cached recent data -> Synthetic/demo data
"""

from typing import List, Tuple, Dict, Any
from tools.adapters.base_adapter import SourceAdapter, AdapterError


class FallbackChain:
    def __init__(self, adapters: List[SourceAdapter]):
        if not adapters:
            raise ValueError("FallbackChain requires at least one adapter")
        self.adapters = adapters

    def fetch(self, **kwargs) -> Tuple[List[Dict[str, Any]], Dict[str, Any], List[Dict[str, Any]]]:
        """
        Tries each adapter in order. Returns (records, winning_adapter_
        description, attempted_and_failed_list) on success.

        Raises AdapterError only if EVERY adapter in the chain failed --
        the caller then genuinely has no data, not a degraded version of it.
        """
        attempts_failed = []
        for adapter in self.adapters:
            try:
                records = adapter.fetch(**kwargs)
                return records, adapter.describe(), attempts_failed
            except AdapterError as e:
                attempts_failed.append({"adapter": adapter.describe(), "error": str(e)})
                continue
        raise AdapterError(
            f"All {len(self.adapters)} adapters in chain failed: {attempts_failed}"
        )
