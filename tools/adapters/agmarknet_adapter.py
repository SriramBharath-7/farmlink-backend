"""
agmarknet_adapter.py
-----------------------
Wraps the EXISTING mandi_price_tool.py logic (confirmed via web search
in this project's audit to be a real, documented, NDSAP-licensed
data.gov.in API -- see AUDIT_REPORT.md's DATA SOURCES section) into the
SourceAdapter interface.

This does NOT change that behavior -- same resource ID, same live-key
check, same synthetic fallback file. It formalizes what was already
correct into the pluggable interface, rather than reinventing it.
"""

import os
import json
import requests
from datetime import datetime
from typing import List, Dict, Any, Optional

from tools.adapters.base_adapter import SourceAdapter, AdapterError
from tools.adapters.schema import PriceRecord

AGMARKNET_RESOURCE_ID = "9ef84268-d588-465a-a308-a864a43d0070"
AGMARKNET_BASE_URL = f"https://api.data.gov.in/resource/{AGMARKNET_RESOURCE_ID}"

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_MOCK_PATH = os.path.join(BASE_DIR, "data", "mock_mandi_prices.json")


class AgmarknetAdapter(SourceAdapter):
    source_name = "AGMARKNET"
    source_type = "government"

    def __init__(self, mock_data_path: Optional[str] = None, api_key: Optional[str] = None,
                 http_session=None):
        self.mock_data_path = mock_data_path or DEFAULT_MOCK_PATH
        self.api_key = api_key if api_key is not None else os.environ.get("DATA_GOV_IN_API_KEY", "").strip()
        self._session = http_session or requests

    def fetch(self, commodity: str, district: str = "", limit: int = 20) -> List[Dict[str, Any]]:
        if self.api_key:
            try:
                live_records = self._fetch_live(commodity, district, limit)
                if live_records:
                    return [self._normalize(r, "LIVE").to_dict() for r in live_records]
            except Exception:
                pass  # any live-path failure falls through to the synthetic path below

        try:
            all_records = self._load_mock()
        except (FileNotFoundError, json.JSONDecodeError) as e:
            raise AdapterError(f"live path unavailable/no key, and mock data file unreadable: {e}")

        filtered = [
            r for r in all_records
            if r["commodity"].lower() == commodity.lower()
            and (not district or r["district"].lower() == district.lower())
        ]
        filtered.sort(key=lambda r: datetime.strptime(r["arrival_date"], "%d/%m/%Y"), reverse=True)
        return [self._normalize(r, "SYNTHETIC", fallback_reason="no_live_key_or_live_call_failed").to_dict()
                for r in filtered[:limit]]

    def _fetch_live(self, commodity, district, limit):
        params = {
            "api-key": self.api_key, "format": "json", "limit": limit,
            "filters[state]": "Maharashtra", "filters[commodity]": commodity,
        }
        if district:
            params["filters[district]"] = district
        resp = self._session.get(AGMARKNET_BASE_URL, params=params, timeout=8)
        resp.raise_for_status()
        return resp.json().get("records", [])

    def _load_mock(self):
        with open(self.mock_data_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _normalize(self, raw: Dict, status: str, fallback_reason: Optional[str] = None) -> PriceRecord:
        return PriceRecord(
            commodity=raw["commodity"],
            market=raw.get("market", f"{raw.get('district', '')} APMC"),
            district=raw["district"],
            state=raw.get("state", "Maharashtra"),
            modal_price=float(raw["modal_price"]),
            min_price=float(raw["min_price"]),
            max_price=float(raw["max_price"]),
            unit="quintal",
            arrival_date=raw["arrival_date"],
            source=self.source_name,
            source_type=self.source_type,
            data_status=status,
            fetched_at=self.now_iso(),
            fallback_reason=fallback_reason,
        )
