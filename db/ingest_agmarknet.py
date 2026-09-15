"""
Periodic AGMARKNET ingestion for FarmLink.

Modes
-----
Single state:
    python -m db.ingest_agmarknet --ingest --state "Tamil Nadu"

National:
    python -m db.ingest_agmarknet --ingest --all-states

Dry-run single state:
    python -m db.ingest_agmarknet --dry-run --state "Tamil Nadu"

Dry-run national:
    python -m db.ingest_agmarknet --dry-run --all-states

Architecture
------------
Official data.gov.in / AGMARKNET
    -> resolve validated district registry
    -> fetch state + district partitions
    -> validate every partition and its reported total
    -> combine + deduplicate
    -> validate complete state snapshot
    -> preload Commodity + Market identities
    -> batched idempotent PostgreSQL UPSERT
    -> atomic commit per state
    -> ingestion-run SUCCESS / FAILED metadata

National ingestion is deliberately sequential.

A failure in one state does not roll back or stop states that have
already completed successfully.

Synthetic data is NEVER used as an ingestion fallback.
"""

import argparse
import random
import re
import time
from datetime import datetime, timezone
from typing import Dict, List

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from db.models import Commodity, IngestionRun, Market, MarketPrice
from db.session import get_session_factory
from tools.adapters.agmarknet_adapter import (
    AGMARKNET_BASE_URL,
    AgmarknetAdapter,
)


DEFAULT_PAGE_SIZE = 1000
DEFAULT_DB_BATCH_SIZE = 500

# National Sync v3.2:
# district-partitioned ingestion with strict state+district validation.
# The state-only endpoint is used only for bounded district discovery when
# a REAL Neon district registry does not yet exist.
MAX_STATE_SNAPSHOT_ATTEMPTS = 4
STATE_RETRY_BASE_SECONDS = 2.0
STATE_RETRY_MAX_SECONDS = 20.0
DISTRICT_PAGE_SIZE = 1000
MAX_DISTRICT_ATTEMPTS = 4
ZERO_DISTRICT_RETRY_SECONDS = 2.0


# ---------------------------------------------------------------------------
# AGMARKNET source-state values
# ---------------------------------------------------------------------------
#
# These are SOURCE QUERY VALUES, not our canonical geographic naming layer.
#
# AGMARKNET/data.gov.in currently contains several legacy/non-canonical
# spellings such as:
#
#   Kerala       -> Keralam
#   Chhattisgarh -> Chattisgarh
#   Puducherry   -> Pondicherry
#   Delhi        -> NCT of Delhi
#
# We preserve source spellings here because filters[state] must match the
# upstream dataset.
#
# States/UTs returning zero rows are skipped during national sync.
#

AGMARKNET_STATE_VALUES = (
    "Andaman and Nicobar",
    "Andhra Pradesh",
    "Assam",
    "Bihar",
    "Chandigarh",
    "Chattisgarh",
    "Gujarat",
    "Haryana",
    "Himachal Pradesh",
    "Jammu and Kashmir",
    "Karnataka",
    "Keralam",
    "Madhya Pradesh",
    "Maharashtra",
    "Manipur",
    "Meghalaya",
    "Nagaland",
    "NCT of Delhi",
    "Odisha",
    "Pondicherry",
    "Punjab",
    "Rajasthan",
    "Tamil Nadu",
    "Telangana",
    "Tripura",
    "Uttar Pradesh",
    "Uttarakhand",
    "West Bengal",
)


def utcnow_naive() -> datetime:
    """
    UTC timestamp compatible with the project's naive DateTime columns.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def clean_text(value, fallback="Unknown") -> str:
    text = str(value or "").strip()
    return text or fallback


def parse_arrival_date(value: str):
    return datetime.strptime(value, "%d/%m/%Y").date()


# ---------------------------------------------------------------------------
# Safe error handling
# ---------------------------------------------------------------------------

def sanitize_error_message(
    error: BaseException,
    api_key: str | None = None,
) -> str:
    """
    Produce metadata-safe error text.

    HTTP exceptions can contain the request URL. Since the API key is sent
    as a query parameter, never persist an exception string without
    redacting secrets first.
    """

    message = f"{type(error).__name__}: {error}"

    if api_key:
        message = message.replace(
            api_key,
            "[REDACTED]",
        )

    message = re.sub(
        r"(?i)(api[-_]?key=)[^&\s]+",
        r"\1[REDACTED]",
        message,
    )

    return message[:4000]


# ---------------------------------------------------------------------------
# Ingestion-run metadata
# ---------------------------------------------------------------------------

def create_ingestion_run(state: str) -> int:
    SessionFactory = get_session_factory()
    session = SessionFactory()

    try:
        run = IngestionRun(
            source="AGMARKNET",
            state=state,
            status="RUNNING",
            records_fetched=0,
            records_processed=0,
        )

        session.add(run)
        session.commit()
        session.refresh(run)

        return run.id

    except Exception:
        session.rollback()
        raise

    finally:
        session.close()


def mark_ingestion_success(
    run_id: int,
    records_fetched: int,
    records_processed: int,
) -> None:
    SessionFactory = get_session_factory()
    session = SessionFactory()

    try:
        run = session.get(IngestionRun, run_id)

        if run is None:
            raise RuntimeError(
                f"Ingestion run {run_id} could not be found."
            )

        run.status = "SUCCESS"
        run.records_fetched = records_fetched
        run.records_processed = records_processed
        run.completed_at = utcnow_naive()
        run.error_message = None

        session.commit()

    except Exception:
        session.rollback()
        raise

    finally:
        session.close()


def mark_ingestion_failed(
    run_id: int,
    records_fetched: int,
    error: BaseException,
    api_key: str | None = None,
) -> None:
    SessionFactory = get_session_factory()
    session = SessionFactory()

    try:
        run = session.get(IngestionRun, run_id)

        if run is None:
            raise RuntimeError(
                f"Ingestion run {run_id} could not be found."
            )

        run.status = "FAILED"
        run.records_fetched = records_fetched
        run.records_processed = 0
        run.completed_at = utcnow_naive()
        run.error_message = sanitize_error_message(
            error,
            api_key=api_key,
        )

        session.commit()

    except Exception:
        session.rollback()
        raise

    finally:
        session.close()


# ---------------------------------------------------------------------------
# AGMARKNET fetching
# ---------------------------------------------------------------------------

def fetch_state_snapshot_once(
    adapter: AgmarknetAdapter,
    state: str,
    page_size: int = DEFAULT_PAGE_SIZE,
    max_records: int | None = None,
) -> List[Dict]:

    state = clean_text(state)

    if state == "Unknown":
        raise ValueError("A valid state name is required.")

    if not adapter.api_key:
        raise RuntimeError(
            "DATA_GOV_IN_API_KEY is not set. "
            "Periodic ingestion requires the real government source."
        )

    records: List[Dict] = []
    offset = 0

    while True:
        requested_limit = page_size

        if max_records is not None:
            remaining = max_records - len(records)

            if remaining <= 0:
                break

            requested_limit = min(
                requested_limit,
                remaining,
            )

        params = {
            "api-key": adapter.api_key,
            "format": "json",
            "limit": requested_limit,
            "offset": offset,
            "filters[state]": state,
        }

        response = adapter._session.get(
            AGMARKNET_BASE_URL,
            params=params,
            headers={
                "User-Agent": "curl/8.0.0",
                "Accept": "*/*",
            },
            timeout=30,
        )

        response.raise_for_status()

        payload = response.json()
        raw_records = payload.get("records", [])

        if not raw_records:
            break

        for raw in raw_records:
            normalized = adapter._normalize(
                raw,
                "LIVE",
            ).to_dict()

            records.append(normalized)

            if (
                max_records is not None
                and len(records) >= max_records
            ):
                break

        print(
            f"Fetched {len(raw_records)} records "
            f"(offset={offset}) | "
            f"total collected={len(records)}"
        )

        if len(raw_records) < requested_limit:
            break

        offset += len(raw_records)

    return records


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _state_retry_delay(attempt: int) -> float:
    delay = min(
        STATE_RETRY_BASE_SECONDS * (2 ** (attempt - 1)),
        STATE_RETRY_MAX_SECONDS,
    )
    return delay + random.uniform(0.0, 0.75)


def fetch_state_snapshot_v2(
    adapter: AgmarknetAdapter,
    state: str,
    page_size: int = DEFAULT_PAGE_SIZE,
    max_records: int | None = None,
) -> List[Dict]:
    """
    National Sync v2.

    Fetch one COMPLETE state snapshot using the proven state-only paginator.
    Every attempt starts again at offset 0. The entire collected snapshot is
    validated before it is accepted. A contaminated/empty/broken attempt is
    discarded wholesale; pages from different attempts are NEVER stitched.

    No database writes happen here.
    """
    state = clean_text(state)

    if state == "Unknown":
        raise ValueError("A valid state name is required.")

    last_error: BaseException | None = None

    for attempt in range(1, MAX_STATE_SNAPSHOT_ATTEMPTS + 1):
        print(
            f"Snapshot attempt {attempt}/"
            f"{MAX_STATE_SNAPSHOT_ATTEMPTS}: {state}"
        )

        try:
            records = fetch_state_snapshot_once(
                adapter,
                state=state,
                page_size=page_size,
                max_records=max_records,
            )

            if not records:
                raise RuntimeError(
                    "Official source returned zero records "
                    f"for {state} on snapshot attempt {attempt}."
                )

            # Validate the WHOLE attempt before returning it.
            validate_records(records, state)

            print(
                f"Snapshot attempt {attempt}: PASS "
                f"({len(records)} validated records)"
            )
            return records

        except KeyboardInterrupt:
            raise

        except BaseException as error:
            last_error = error

            print(
                f"Snapshot attempt {attempt}: REJECTED"
            )
            print(
                sanitize_error_message(
                    error,
                    api_key=adapter.api_key,
                )
            )

            if attempt < MAX_STATE_SNAPSHOT_ATTEMPTS:
                delay = _state_retry_delay(attempt)
                print(
                    "Discarding entire attempt and retrying "
                    f"from offset 0 in {delay:.1f}s..."
                )
                time.sleep(delay)

    if last_error is None:
        last_error = RuntimeError(
            f"State snapshot failed for {state}."
        )

    raise RuntimeError(
        f"All {MAX_STATE_SNAPSHOT_ATTEMPTS} complete snapshot attempts "
        f"failed for {state}. Previous REAL database rows are preserved. "
        f"Last error: {sanitize_error_message(last_error, adapter.api_key)}"
    ) from None



def get_real_state_district_registry(state: str) -> List[str]:
    """Return districts already proven by REAL AGMARKNET rows in PostgreSQL."""
    SessionFactory = get_session_factory()
    session = SessionFactory()

    try:
        districts = session.execute(
            select(Market.district)
            .join(
                MarketPrice,
                MarketPrice.market_id == Market.id,
            )
            .where(
                Market.state == state,
                MarketPrice.source == "AGMARKNET",
            )
            .distinct()
            .order_by(Market.district)
        ).scalars().all()

        return [
            clean_text(district)
            for district in districts
            if clean_text(district) != "Unknown"
        ]
    finally:
        session.close()


def discover_state_districts(
    adapter: AgmarknetAdapter,
    state: str,
) -> List[str]:
    """
    Bootstrap-only district discovery.

    The state-only API can become contaminated at a deterministic boundary.
    We therefore request pages from offset 0 and accept ONLY the contiguous
    prefix belonging to the requested state. Discovery stops immediately at
    the first foreign-state row.

    The resulting candidate districts are NOT trusted by themselves: every
    district is subsequently queried and strictly validated by the v3 fetcher.
    """
    if not adapter.api_key:
        raise RuntimeError(
            "DATA_GOV_IN_API_KEY is not set. "
            "District discovery requires the real government source."
        )

    districts = set()
    offset = 0
    page_size = DEFAULT_PAGE_SIZE

    while True:
        params = {
            "api-key": adapter.api_key,
            "format": "json",
            "limit": page_size,
            "offset": offset,
            "filters[state]": state,
        }

        try:
            response = adapter._session.get(
                AGMARKNET_BASE_URL,
                params=params,
                headers={
                    "User-Agent": "curl/8.0.0",
                    "Accept": "*/*",
                },
                timeout=30,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as error:
            raise RuntimeError(
                "Government API district discovery request failed: "
                f"{type(error).__name__}."
            ) from None

        raw_records = payload.get("records", [])

        if not raw_records:
            break

        hit_boundary = False

        for raw in raw_records:
            raw_state = clean_text(raw.get("state"))

            if raw_state.casefold() != state.casefold():
                hit_boundary = True
                break

            district = clean_text(raw.get("district"))

            if district != "Unknown":
                districts.add(district)

        if hit_boundary:
            print(
                "State-only discovery reached the upstream contamination "
                f"boundary at approximately offset {offset}. "
                "Only the validated contiguous prefix was used."
            )
            break

        offset += len(raw_records)

        if len(raw_records) < page_size:
            break

    result = sorted(districts)

    if not result:
        raise RuntimeError(
            f"No validated districts could be discovered for {state!r}."
        )

    print(
        f"Bootstrap district candidates discovered: {len(result)}"
    )

    return result


def fetch_district_partition_once(
    adapter: AgmarknetAdapter,
    state: str,
    district: str,
    page_size: int = DISTRICT_PAGE_SIZE,
) -> List[Dict]:
    """Fetch and strictly validate one complete state+district partition."""
    records: List[Dict] = []
    offset = 0
    expected_total = None
    raw_bucket_count = 0
    discarded_count = 0

    while True:
        params = {
            "api-key": adapter.api_key,
            "format": "json",
            "limit": page_size,
            "offset": offset,
            "filters[state]": state,
            "filters[district]": district,
        }

        try:
            response = adapter._session.get(
                AGMARKNET_BASE_URL,
                params=params,
                headers={
                    "User-Agent": "curl/8.0.0",
                    "Accept": "*/*",
                },
                timeout=30,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as error:
            raise RuntimeError(
                "Government API district-partition request failed: "
                f"{type(error).__name__}."
            ) from None

        raw_records = payload.get("records", [])

        raw_total = payload.get("total")
        try:
            reported_total = (
                int(raw_total)
                if raw_total is not None
                else None
            )
        except (TypeError, ValueError):
            reported_total = None

        if expected_total is None:
            expected_total = reported_total
        elif (
            expected_total is not None
            and reported_total is not None
            and reported_total != expected_total
        ):
            raise RuntimeError(
                "District partition total changed during pagination. "
                f"State={state!r}, district={district!r}, "
                f"expected={expected_total}, received={reported_total}."
            )

        if not raw_records:
            break

        raw_bucket_count += len(raw_records)

        for position, raw in enumerate(raw_records, start=1):
            raw_state = clean_text(raw.get("state"))
            raw_district = clean_text(raw.get("district"))

            # data.gov.in district filters can return a broader candidate
            # bucket (empirically: East/West Godavari are returned together).
            # Never persist those foreign rows. Exact-match locally on BOTH
            # state and district and discard everything else.
            if (
                raw_state.casefold() != state.casefold()
                or raw_district.casefold() != district.casefold()
            ):
                discarded_count += 1
                continue

            records.append(
                adapter._normalize(
                    raw,
                    "LIVE",
                ).to_dict()
            )

        offset += len(raw_records)

        if len(raw_records) < page_size:
            break

        if (
            expected_total is not None
            and offset >= expected_total
        ):
            break

    # The API's total belongs to the candidate bucket. Validate that we
    # consumed the whole bucket, then keep only locally exact observations.
    if (
        expected_total is not None
        and raw_bucket_count != expected_total
    ):
        raise RuntimeError(
            "District candidate bucket did not match its reported total. "
            f"State={state!r}, district={district!r}, "
            f"reported={expected_total}, fetched={raw_bucket_count}."
        )

    if expected_total and not records:
        raise RuntimeError(
            "District candidate bucket contained no exact state+district rows. "
            f"State={state!r}, district={district!r}, "
            f"bucket={raw_bucket_count}, discarded={discarded_count}."
        )

    if discarded_count:
        print(
            f"    {district}: exact={len(records)}, "
            f"discarded_foreign={discarded_count}, "
            f"bucket={raw_bucket_count}"
        )

    return records


def fetch_district_partition(
    adapter: AgmarknetAdapter,
    state: str,
    district: str,
) -> List[Dict]:
    """
    Retry a whole district partition; never stitch failed attempts.

    A district coming from the REAL PostgreSQL registry is expected to have
    previously proven AGMARKNET coverage. The upstream dataset can briefly
    expose a zero-row view while it is being refreshed, so an empty partition
    is treated as suspicious and retried rather than immediately accepted.

    If every complete attempt is still empty, fail the district/state sync.
    This preserves the previously committed REAL rows instead of declaring an
    incomplete current snapshot successful.
    """
    last_error: BaseException | None = None

    for attempt in range(1, MAX_DISTRICT_ATTEMPTS + 1):
        try:
            records = fetch_district_partition_once(
                adapter,
                state=state,
                district=district,
            )

            if not records:
                raise RuntimeError(
                    "Official source returned zero exact records for a "
                    "registered district. Treating the empty partition as "
                    "transient/suspicious rather than accepting an incomplete "
                    f"snapshot. State={state!r}, district={district!r}."
                )

            return records

        except KeyboardInterrupt:
            raise

        except BaseException as error:
            last_error = error

            if attempt < MAX_DISTRICT_ATTEMPTS:
                delay = _state_retry_delay(attempt)
                print(
                    f"  {district}: attempt {attempt} rejected; "
                    f"retrying whole district in {delay:.1f}s..."
                )
                time.sleep(delay)

    raise RuntimeError(
        f"District partition failed after {MAX_DISTRICT_ATTEMPTS} attempts. "
        f"State={state!r}, district={district!r}. "
        f"Last error: {sanitize_error_message(last_error, adapter.api_key)}"
    ) from None


def _record_identity(record: Dict) -> tuple:
    return (
        clean_text(record.get("market")).casefold(),
        clean_text(record.get("district")).casefold(),
        clean_text(record.get("state")).casefold(),
        clean_text(record.get("commodity")).casefold(),
        clean_text(record.get("arrival_date")),
        clean_text(record.get("variety")).casefold(),
        clean_text(record.get("grade")).casefold(),
        clean_text(record.get("source")).casefold(),
    )


def fetch_state_snapshot(
    adapter: AgmarknetAdapter,
    state: str,
    page_size: int = DEFAULT_PAGE_SIZE,
    max_records: int | None = None,
) -> List[Dict]:
    """
    National Sync v3.2 state fetcher.

    Existing states use the REAL Neon district registry.
    Uninitialized states bootstrap candidate districts from the validated
    contiguous state-only prefix. Every district partition is then fetched and
    validated independently. The final state snapshot is deduplicated.

    max_records is applied only after complete district partitions are fetched,
    so dry-run limits never cause partial district validation.
    """
    state = clean_text(state)

    if state == "Unknown":
        raise ValueError("A valid state name is required.")

    if not adapter.api_key:
        raise RuntimeError(
            "DATA_GOV_IN_API_KEY is not set. "
            "Periodic ingestion requires the real government source."
        )

    districts = get_real_state_district_registry(state)
    registry_source = "REAL Neon"

    if not districts:
        registry_source = "bootstrap discovery"
        districts = discover_state_districts(
            adapter,
            state,
        )

    print(
        f"District registry: {len(districts)} "
        f"({registry_source})"
    )

    combined: List[Dict] = []

    for number, district in enumerate(districts, start=1):
        partition = fetch_district_partition(
            adapter,
            state=state,
            district=district,
        )

        print(
            f"  [{number}/{len(districts)}] "
            f"{district}: {len(partition)}"
        )

        combined.extend(partition)

    unique: Dict[tuple, Dict] = {}

    for record in combined:
        unique[_record_identity(record)] = record

    records = list(unique.values())

    duplicates = len(combined) - len(records)

    if duplicates:
        print(
            f"Duplicate source observations collapsed: {duplicates}"
        )

    validate_records(records, state)

    print(
        f"National Sync v3.2 validated state snapshot: "
        f"{len(records)} unique records"
    )

    if max_records is not None:
        return records[:max_records]

    return records


def validate_records(
    records: List[Dict],
    requested_state: str,
) -> None:

    for index, record in enumerate(
        records,
        start=1,
    ):

        if record.get("data_status") != "LIVE":
            raise RuntimeError(
                "Refusing to persist non-LIVE record "
                f"at position {index}."
            )

        record_state = clean_text(
            record.get("state")
        )

        if (
            record_state.casefold()
            != requested_state.casefold()
        ):
            raise RuntimeError(
                "Government source returned a record "
                "for an unexpected state. "
                f"Requested={requested_state!r}, "
                f"received={record_state!r}, "
                f"position={index}."
            )


# ---------------------------------------------------------------------------
# Identity resolution
# ---------------------------------------------------------------------------

def ensure_commodities(
    session,
    records: List[Dict],
) -> Dict[str, int]:

    required_names = {
        clean_text(record["commodity"])
        for record in records
    }

    existing = session.execute(
        select(Commodity).where(
            Commodity.name.in_(
                required_names
            )
        )
    ).scalars().all()

    commodity_map = {
        commodity.name: commodity.id
        for commodity in existing
    }

    missing_names = (
        required_names
        - set(commodity_map)
    )

    if missing_names:
        print(
            f"Creating {len(missing_names)} "
            "missing commodities..."
        )

        session.add_all(
            Commodity(name=name)
            for name in sorted(missing_names)
        )

        session.flush()

        created = session.execute(
            select(Commodity).where(
                Commodity.name.in_(
                    missing_names
                )
            )
        ).scalars().all()

        for commodity in created:
            commodity_map[
                commodity.name
            ] = commodity.id

    return commodity_map


def ensure_markets(
    session,
    records: List[Dict],
    state: str,
) -> Dict[tuple[str, str, str], int]:

    required_keys = {
        (
            clean_text(record["market"]),
            clean_text(record["district"]),
            clean_text(record.get("state")),
        )
        for record in records
    }

    existing = session.execute(
        select(Market).where(
            Market.state == state
        )
    ).scalars().all()

    market_map = {
        (
            market.name,
            market.district,
            market.state,
        ): market.id
        for market in existing
    }

    missing_keys = (
        required_keys
        - set(market_map)
    )

    if missing_keys:
        print(
            f"Creating {len(missing_keys)} "
            "missing markets..."
        )

        session.add_all(
            Market(
                name=name,
                district=district,
                state=market_state,
            )
            for (
                name,
                district,
                market_state,
            ) in sorted(missing_keys)
        )

        session.flush()

        existing = session.execute(
            select(Market).where(
                Market.state == state
            )
        ).scalars().all()

        market_map = {
            (
                market.name,
                market.district,
                market.state,
            ): market.id
            for market in existing
        }

    return market_map


# ---------------------------------------------------------------------------
# Price rows + UPSERT
# ---------------------------------------------------------------------------

def build_price_rows(
    records: List[Dict],
    commodity_map: Dict[str, int],
    market_map: Dict[
        tuple[str, str, str],
        int,
    ],
) -> List[Dict]:

    rows: List[Dict] = []

    for record in records:
        commodity_name = clean_text(
            record["commodity"]
        )

        market_key = (
            clean_text(record["market"]),
            clean_text(record["district"]),
            clean_text(record.get("state")),
        )

        commodity_id = commodity_map.get(
            commodity_name
        )

        market_id = market_map.get(
            market_key
        )

        if commodity_id is None:
            raise RuntimeError(
                "Commodity identity could not "
                f"be resolved: {commodity_name!r}"
            )

        if market_id is None:
            raise RuntimeError(
                "Market identity could not "
                f"be resolved: {market_key!r}"
            )

        rows.append(
            {
                "market_id": market_id,
                "commodity_id": commodity_id,
                "arrival_date": parse_arrival_date(
                    record["arrival_date"]
                ),
                "variety": clean_text(
                    record.get("variety")
                ),
                "grade": clean_text(
                    record.get("grade")
                ),
                "min_price": record[
                    "min_price"
                ],
                "max_price": record[
                    "max_price"
                ],
                "modal_price": record[
                    "modal_price"
                ],
                "unit": (
                    record.get("unit")
                    or "quintal"
                ),
                "source": "AGMARKNET",
            }
        )

    return rows


def upsert_price_batch(
    session,
    rows: List[Dict],
) -> None:

    if not rows:
        return

    statement = insert(
        MarketPrice
    ).values(rows)

    statement = (
        statement.on_conflict_do_update(
            constraint=(
                "uq_market_price_observation"
            ),
            set_={
                "min_price": (
                    statement.excluded.min_price
                ),
                "max_price": (
                    statement.excluded.max_price
                ),
                "modal_price": (
                    statement.excluded.modal_price
                ),
                "unit": (
                    statement.excluded.unit
                ),
            },
        )
    )

    session.execute(statement)


# ---------------------------------------------------------------------------
# Persist one already-fetched state
# ---------------------------------------------------------------------------

def persist_state_records(
    state: str,
    records: List[Dict],
    batch_size: int,
) -> int:

    SessionFactory = get_session_factory()
    session = SessionFactory()

    try:
        print("Resolving commodity identities...")

        commodity_map = ensure_commodities(
            session,
            records,
        )

        print(
            "Commodity identities ready: "
            f"{len(commodity_map)}"
        )

        print(
            "Resolving market identities..."
        )

        market_map = ensure_markets(
            session,
            records,
            state,
        )

        print(
            "Market identities ready: "
            f"{len(market_map)}"
        )

        print(
            "Preparing market-price rows..."
        )

        price_rows = build_price_rows(
            records,
            commodity_map,
            market_map,
        )

        print(
            f"Prepared {len(price_rows)} "
            "market-price rows."
        )

        print()
        print(
            "Writing market prices "
            "in batches..."
        )

        total = len(price_rows)

        for start in range(
            0,
            total,
            batch_size,
        ):
            batch = price_rows[
                start:start + batch_size
            ]

            upsert_price_batch(
                session,
                batch,
            )

            processed = min(
                start + len(batch),
                total,
            )

            print(
                "  Database progress: "
                f"{processed}/{total}"
            )

        print()
        print(
            "All batches processed. "
            "Committing transaction..."
        )

        session.commit()

        return total

    except BaseException:
        print()
        print(
            "ERROR: price ingestion failed."
        )
        print(
            "Rolling back price transaction..."
        )

        session.rollback()
        raise

    finally:
        session.close()


# ---------------------------------------------------------------------------
# Single-state ingestion
# ---------------------------------------------------------------------------

def ingest_state(
    state: str,
    limit: int | None = None,
    batch_size: int = DEFAULT_DB_BATCH_SIZE,
    zero_records_are_error: bool = True,
) -> str:

    adapter = AgmarknetAdapter()
    state = clean_text(state)

    print()
    print(
        "========================================"
    )
    print(f"AGMARKNET STATE SYNC: {state}")
    print(
        "========================================"
    )

    run_id: int | None = None
    records: List[Dict] = []

    try:
        records = fetch_state_snapshot(
            adapter,
            state=state,
            page_size=(
                min(
                    DEFAULT_PAGE_SIZE,
                    limit,
                )
                if limit is not None
                else DEFAULT_PAGE_SIZE
            ),
            max_records=limit,
        )

        if not records:
            if zero_records_are_error:
                run_id = create_ingestion_run(
                    state
                )

                raise RuntimeError(
                    "Official source returned "
                    f"zero records for {state}. "
                    "Nothing will be written."
                )

            print(
                "No current AGMARKNET records "
                f"for {state}. Skipping."
            )

            return "NO_DATA"

        print()
        print(
            "Government records fetched: "
            f"{len(records)}"
        )

        print("Validating records...")

        validate_records(
            records,
            state,
        )

        print("Validation passed.")

        # Only create an ingestion audit run once
        # real records have been fetched in national
        # mode. In single-state mode, a zero-record
        # attempt is still recorded as FAILED.
        run_id = create_ingestion_run(
            state
        )

        print(
            f"Ingestion run ID: {run_id}"
        )
        print()

        processed = persist_state_records(
            state=state,
            records=records,
            batch_size=batch_size,
        )

        mark_ingestion_success(
            run_id=run_id,
            records_fetched=len(records),
            records_processed=processed,
        )

        print()
        print(
            f"STATE COMPLETE: {state}"
        )
        print(
            f"Records fetched: {len(records)}"
        )
        print(
            f"Records processed: {processed}"
        )
        print("Run status: SUCCESS")

        return "SUCCESS"

    except BaseException as error:
        if run_id is None:
            try:
                run_id = create_ingestion_run(
                    state
                )
            except Exception:
                run_id = None

        if run_id is not None:
            try:
                mark_ingestion_failed(
                    run_id=run_id,
                    records_fetched=len(
                        records
                    ),
                    error=error,
                    api_key=adapter.api_key,
                )

            except Exception as metadata_error:
                print()
                print(
                    "WARNING: failure metadata "
                    "could not be updated."
                )
                print(
                    "Metadata error: "
                    f"{metadata_error}"
                )

        raise


# ---------------------------------------------------------------------------
# National ingestion
# ---------------------------------------------------------------------------

def ingest_all_states(
    batch_size: int = DEFAULT_DB_BATCH_SIZE,
    limit_per_state: int | None = None,
) -> Dict[str, str]:

    print()
    print(
        "========================================"
    )
    print(
        "FARMLINK AGMARKNET NATIONAL SYNC"
    )
    print(
        "========================================"
    )
    print(
        "Strategy: sequential state-isolated sync"
    )
    print(
        "Synthetic fallback: DISABLED"
    )
    print(
        f"Configured source states: "
        f"{len(AGMARKNET_STATE_VALUES)}"
    )
    print()

    results: Dict[str, str] = {}

    for number, state in enumerate(
        AGMARKNET_STATE_VALUES,
        start=1,
    ):
        print()
        print(
            f"[{number}/"
            f"{len(AGMARKNET_STATE_VALUES)}] "
            f"{state}"
        )

        try:
            status = ingest_state(
                state=state,
                limit=limit_per_state,
                batch_size=batch_size,
                zero_records_are_error=True,
            )

            results[state] = status

        except KeyboardInterrupt:
            print()
            print(
                "National sync interrupted "
                "by user."
            )
            raise

        except Exception as error:
            results[state] = "FAILED"

            print()
            print(
                f"State sync FAILED: {state}"
            )
            print(
                sanitize_error_message(
                    error
                )
            )
            print(
                "Continuing with next state..."
            )

    success_states = [
        state
        for state, status in results.items()
        if status == "SUCCESS"
    ]

    no_data_states = [
        state
        for state, status in results.items()
        if status == "NO_DATA"
    ]

    failed_states = [
        state
        for state, status in results.items()
        if status == "FAILED"
    ]

    print()
    print(
        "========================================"
    )
    print("NATIONAL SYNC SUMMARY")
    print(
        "========================================"
    )

    for state, status in results.items():
        print(
            f"{state:<40} {status}"
        )

    print()
    print(
        f"SUCCESS : {len(success_states)}"
    )
    print(
        f"NO DATA : {len(no_data_states)}"
    )
    print(
        f"FAILED  : {len(failed_states)}"
    )

    if failed_states:
        print()
        print(
            "National sync completed "
            "with state-level failures."
        )
    else:
        print()
        print(
            "National sync completed."
        )

    return results


# ---------------------------------------------------------------------------
# Failed-state recovery
# ---------------------------------------------------------------------------

def get_latest_failed_states() -> List[str]:
    """Return configured states whose latest AGMARKNET attempt FAILED."""
    SessionFactory = get_session_factory()
    session = SessionFactory()

    try:
        runs = session.execute(
            select(IngestionRun)
            .where(IngestionRun.source == "AGMARKNET")
            .order_by(
                IngestionRun.state.asc(),
                IngestionRun.id.desc(),
            )
        ).scalars().all()

        latest_by_state = {}

        for run in runs:
            state = clean_text(run.state)
            if state not in latest_by_state:
                latest_by_state[state] = run

        return [
            state
            for state in AGMARKNET_STATE_VALUES
            if (
                state in latest_by_state
                and latest_by_state[state].status == "FAILED"
            )
        ]
    finally:
        session.close()


def retry_failed_states(
    batch_size: int = DEFAULT_DB_BATCH_SIZE,
) -> Dict[str, str]:
    """
    Retry only states whose latest AGMARKNET attempt is FAILED.

    IMPORTANT: this calls the unchanged v3.2 ingest_state(), so recovery
    uses the exact same district-partition fetch/validation/write path as
    normal v3.2 ingestion.
    """
    failed_states = get_latest_failed_states()

    print()
    print("========================================")
    print("FARMLINK FAILED-STATE RETRY")
    print("========================================")

    if not failed_states:
        print("No failed AGMARKNET states found. Nothing to retry.")
        return {}

    print(f"Failed states found: {len(failed_states)}")
    for state in failed_states:
        print(f"  - {state}")

    results: Dict[str, str] = {}

    for number, state in enumerate(failed_states, start=1):
        print()
        print(f"[{number}/{len(failed_states)}] Retrying {state}")

        try:
            results[state] = ingest_state(
                state=state,
                batch_size=batch_size,
                zero_records_are_error=True,
            )
        except KeyboardInterrupt:
            raise
        except Exception as error:
            results[state] = "FAILED"
            print()
            print(f"Retry FAILED: {state}")
            print(sanitize_error_message(error))
            print("Continuing with next failed state...")

    print()
    print("========================================")
    print("FAILED-STATE RETRY SUMMARY")
    print("========================================")

    for state, status in results.items():
        print(f"{state:<40} {status}")

    print()
    print(f"SUCCESS : {sum(v == 'SUCCESS' for v in results.values())}")
    print(f"FAILED  : {sum(v != 'SUCCESS' for v in results.values())}")

    return results


# ---------------------------------------------------------------------------
# Dry runs
# ---------------------------------------------------------------------------

def dry_run_state(
    state: str,
    limit: int,
) -> str:

    adapter = AgmarknetAdapter()
    state = clean_text(state)

    print()
    print(
        f"DRY RUN: {state}"
    )

    records = fetch_state_snapshot(
        adapter,
        state=state,
        page_size=min(
            DEFAULT_PAGE_SIZE,
            limit,
        ),
        max_records=limit,
    )

    print(
        f"LIVE records fetched: {len(records)}"
    )

    if not records:
        return "NO_DATA"

    validate_records(
        records,
        state,
    )

    sample = records[0]

    print(
        "Validation: PASS"
    )
    print(
        "Sample: "
        f"{sample.get('commodity')} | "
        f"{sample.get('market')} | "
        f"{sample.get('arrival_date')}"
    )

    return "SUCCESS"


def dry_run_all_states(
    limit_per_state: int,
) -> None:

    print()
    print(
        "========================================"
    )
    print(
        "FARMLINK NATIONAL DRY RUN"
    )
    print(
        "========================================"
    )
    print(
        "Database writes: DISABLED"
    )
    print(
        f"Records requested per state: "
        f"{limit_per_state}"
    )
    print()

    results: Dict[str, str] = {}

    for number, state in enumerate(
        AGMARKNET_STATE_VALUES,
        start=1,
    ):

        print()
        print(
            f"[{number}/"
            f"{len(AGMARKNET_STATE_VALUES)}] "
            f"{state}"
        )

        try:
            results[state] = dry_run_state(
                state=state,
                limit=limit_per_state,
            )

        except KeyboardInterrupt:
            raise

        except Exception as error:
            results[state] = "FAILED"

            print(
                "Dry-run error: "
                f"{sanitize_error_message(error)}"
            )

    print()
    print(
        "========================================"
    )
    print(
        "NATIONAL DRY-RUN SUMMARY"
    )
    print(
        "========================================"
    )

    for state, status in results.items():
        print(
            f"{state:<40} {status}"
        )

    print()
    print(
        "SUCCESS : "
        f"{sum(v == 'SUCCESS' for v in results.values())}"
    )
    print(
        "NO DATA : "
        f"{sum(v == 'NO_DATA' for v in results.values())}"
    )
    print(
        "FAILED  : "
        f"{sum(v == 'FAILED' for v in results.values())}"
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:

    parser = argparse.ArgumentParser(
        description=(
            "FarmLink periodic AGMARKNET ingestion"
        )
    )

    mode = (
        parser.add_mutually_exclusive_group(
            required=True
        )
    )

    mode.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Fetch and validate LIVE records "
            "without database writes"
        ),
    )

    mode.add_argument(
        "--ingest",
        action="store_true",
        help=(
            "Fetch LIVE records and persist "
            "them to PostgreSQL"
        ),
    )

    target = (
        parser.add_mutually_exclusive_group(
            required=True
        )
    )

    target.add_argument(
        "--state",
        type=str,
        help=(
            'One AGMARKNET source state, '
            'for example "Tamil Nadu"'
        ),
    )

    target.add_argument(
        "--all-states",
        action="store_true",
        help=(
            "Process all configured "
            "AGMARKNET source states"
        ),
    )

    target.add_argument(
        "--retry-failed",
        action="store_true",
        help=(
            "Retry only states whose latest "
            "AGMARKNET ingestion attempt FAILED"
        ),
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help=(
            "Single-state: maximum records. "
            "All-states: maximum records "
            "PER STATE."
        ),
    )

    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_DB_BATCH_SIZE,
        help=(
            "Rows per PostgreSQL UPSERT "
            "batch (default: 500)"
        ),
    )

    args = parser.parse_args()

    if (
        args.limit is not None
        and args.limit <= 0
    ):
        raise SystemExit(
            "--limit must be greater than zero."
        )

    if args.batch_size <= 0:
        raise SystemExit(
            "--batch-size must be greater than zero."
        )

    if args.state:
        state = args.state.strip()

        if not state:
            raise SystemExit(
                "--state cannot be empty."
            )

        if args.dry_run:
            dry_run_state(
                state=state,
                limit=args.limit or 20,
            )
            return

        ingest_state(
            state=state,
            limit=args.limit,
            batch_size=args.batch_size,
            zero_records_are_error=True,
        )

        return

    if args.retry_failed:
        if args.dry_run:
            raise SystemExit(
                "--retry-failed is a recovery write action; "
                "use it with --ingest."
            )

        results = retry_failed_states(
            batch_size=args.batch_size,
        )

        if any(status != "SUCCESS" for status in results.values()):
            raise SystemExit(1)

        return

    if args.all_states:
        if args.dry_run:
            dry_run_all_states(
                limit_per_state=(
                    args.limit or 20
                )
            )
            return

        results = ingest_all_states(
            batch_size=args.batch_size,
            limit_per_state=args.limit,
        )

        # Finish all states, then tell a cloud scheduler if the run was partial.
        if any(status != "SUCCESS" for status in results.values()):
            raise SystemExit(1)


if __name__ == "__main__":
    main()