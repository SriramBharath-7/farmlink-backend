"""
base_adapter.py
------------------
The one contract every FarmLink data-source adapter implements. Kept
deliberately small: a source that fetches data, and a way to describe
itself for logging/debugging/judges. Category-specific normalization
(price shape vs. distance shape) is enforced by schema.py's dataclasses,
not by this base class -- an adapter is free to serve any record shape
as long as it's one of the schemas in schema.py.
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any
from tools.adapters.schema import now_iso


class AdapterError(Exception):
    """
    Raised by fetch() when an adapter cannot produce ANY data -- not even
    its own internal fallback. FallbackChain catches this specifically to
    move to the next adapter in priority order. An adapter must NOT raise
    a generic Exception for this (a bare Exception would look like a bug,
    not an expected "try the next source" signal) and must NOT return an
    empty list to mean failure (an empty list must only mean "the query
    legitimately has zero results").
    """


class SourceAdapter(ABC):
    source_name: str = "unknown"
    source_type: str = "unknown"  # government | public_osm | synthetic | derived

    @abstractmethod
    def fetch(self, **kwargs) -> List[Dict[str, Any]]:
        """
        Returns a list of normalized dicts (from schema.py's
        PriceRecord.to_dict() / DistanceRecord.to_dict() / etc.).
        MUST raise AdapterError -- not return [] -- when the adapter
        itself failed to produce data.
        """
        raise NotImplementedError

    def describe(self) -> Dict[str, Any]:
        return {
            "source_name": self.source_name,
            "source_type": self.source_type,
            "adapter_class": self.__class__.__name__,
        }

    @staticmethod
    def now_iso() -> str:
        return now_iso()
