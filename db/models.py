"""
db/models.py
---------------
SQLAlchemy declarative models for the PostgreSQL schema described in the
architecture blueprint's "Core Database Schema" section, reconciled
against what the already-built deterministic core (tools/) actually
produces and expects.

Reconciliation notes (deviations from the blueprint doc, and why):

1. `buyer_profiles.buyer_type` includes RETAILER and WHOLESALER (per the
   blueprint) even though the current synthetic `buyers_augmented.json`
   only ever populates TRADER/PROCESSOR/EXPORTER/INSTITUTIONAL -- kept
   for forward compatibility, documented here rather than silently
   dropped.

2. `buyer_profiles` carries `target_price_index` and
   `minimum_grade_accepted` -- these come from
   `tools/augment_buyer_data.py`, not the original blueprint doc (the
   blueprint's `buyer_demands` table has `target_price` per *demand*,
   but the matching engine actually built in this project scores off a
   per-buyer price index applied to the mandi modal price -- see
   matching_engine.py). Both fields are nullable + carry a
   `buyer_data_status` (REAL/SYNTHETIC/USER_GENERATED, per blueprint
   section 25) so the provenance is never silently lost when this table
   is seeded from buyers_augmented.json.

3. `grievances.complaint_type` uses the exact vocabulary from
   `tools/grievance_core.py`'s `COMPLAINT_RULES`
   (payment_not_received / quality_mismatch / quantity_mismatch /
   buyer_unresponsive / logistics_delay / other) rather than the
   blueprint doc's own category list (PAYMENT_DELAY / QUALITY_DISPUTE /
   ... / BUYER_BEHAVIOUR / PRICE_DISPUTE). The blueprint is aspirational
   prose; grievance_core.py is tested, running code with 9 passing
   tests. The DB enum must match the code that populates it, not the
   doc -- diverging from the doc here is intentional, not an oversight.
   `matched_known_category` and `raw_complaint_type_input` are carried
   through unchanged from grievance_core.py's bugfixed output shape (the
   bug it fixed: unmatched categories silently displaying as if they'd
   matched -- this schema keeps that fix's audit trail visible).

4. The blueprint's architecture diagram lists "Logistics" and "Payments"
   as top-level DB entities but never gives their column-level schema
   the way it does for users/farmer_profiles/buyer_profiles/lots. Those
   two tables (`logistics_bookings`, `payments`) are therefore designed
   here from the blueprint's *prose* (sections 19, 29, the transaction
   state machine in section 28) rather than copied from a schema that
   doesn't exist in the source doc. Flagged so this isn't mistaken for
   a verbatim blueprint transcription.

5. `transactions.status` implements the exact state sequence from
   blueprint section 28 (OFFER_ACCEPTED -> PAYMENT_INITIATED ->
   LOGISTICS_ARRANGED -> IN_TRANSIT -> DELIVERED -> PAYMENT_CONFIRMED ->
   COMPLETED, plus CANCELLED as an exit state). Sequencing itself
   ("must never jump randomly between states") is enforced by a
   Postgres trigger in the migration, not just documented in a
   docstring -- see migrations/versions -- because a comment can't stop
   a bad UPDATE, and the blueprint says "must" for this specific rule.

6. `market_prices.source` uses the blueprint's own four values verbatim
   (AGMARKNET / DATA_GOV_IN / ENAM / SYNTHETIC, blueprint section 7) --
   this one aligns cleanly with the doc since it's a data-source tag,
   not application behavior, and the blueprint's list is already
   accurate per AUDIT_REPORT.md's research (ENAM has no public API, so
   in practice this value will not be written by any real ingestion job
   yet -- it exists in the enum for schema completeness/future use, not
   because a job populates it today).

7. `logistics_providers` and `cold_storage_facilities` tables are added
   to hold what today lives only in `data/logistics_providers.json` and
   `data/cold_storage.json` -- so the seed script has somewhere real to
   put that data instead of the app continuing to read flat files
   forever. `data_status` defaults to SYNTHETIC on every row seeded from
   those files (see db/seed_from_json.py).

No table in this file claims a capability that isn't backed by an
actual migration + test in this same delivery -- see DB_REPORT.md.
"""

from datetime import datetime, date, timezone
from decimal import Decimal

from sqlalchemy import (
    Column, Integer, BigInteger, String, Text, Boolean, Numeric, Date,
    DateTime, ForeignKey, UniqueConstraint, CheckConstraint, Index,
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import JSONB, ARRAY
from sqlalchemy.orm import declarative_base, relationship


def _utcnow_naive() -> datetime:
    """
    Replacement for the deprecated datetime.utcnow() (removal scheduled
    in a future Python version -- this codebase hit 743 deprecation
    warnings across one test run before this fix). Returns the same
    VALUE datetime.utcnow() did (naive, UTC wall-clock time), matching
    the existing plain `DateTime` (not `DateTime(timezone=True)`) column
    type exactly -- this is a warning fix, not a behavior change.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)

Base = declarative_base()


# ---------------------------------------------------------------------------
# Shared enums
# ---------------------------------------------------------------------------

USER_ROLES = ("FARMER", "BUYER", "FPO_ADMIN", "GOVERNMENT_ADMIN", "PLATFORM_ADMIN")
BUYER_TYPES = ("TRADER", "PROCESSOR", "EXPORTER", "RETAILER", "INSTITUTION", "WHOLESALER")
VERIFICATION_STATUSES = ("PENDING", "VERIFIED", "REJECTED")
LOT_STATUSES = (
    "DRAFT", "PUBLISHED", "MATCHED", "NEGOTIATING", "SOLD",
    "IN_TRANSIT", "DELIVERED", "CLOSED", "CANCELLED",
)
OFFER_STATUSES = ("PENDING", "COUNTERED", "ACCEPTED", "REJECTED", "EXPIRED")
TRANSACTION_STATUSES = (
    "OFFER_ACCEPTED", "PAYMENT_INITIATED", "LOGISTICS_ARRANGED",
    "IN_TRANSIT", "DELIVERED", "PAYMENT_CONFIRMED", "COMPLETED", "CANCELLED",
)
PAYMENT_STATUSES = ("INITIATED", "PROCESSING", "CONFIRMED", "FAILED")
MARKET_PRICE_SOURCES = ("AGMARKNET", "DATA_GOV_IN", "ENAM", "SYNTHETIC")
DATA_STATUSES = ("REAL", "SYNTHETIC", "USER_GENERATED")
GRIEVANCE_COMPLAINT_TYPES = (
    "payment_not_received", "quality_mismatch", "quantity_mismatch",
    "buyer_unresponsive", "logistics_delay", "other",
)
GRIEVANCE_SEVERITIES = ("low", "medium", "high")
GRIEVANCE_STATUSES = ("open_awaiting_response", "escalated_to_admin_review", "resolved", "closed")
GRADES = ("A", "B", "C")
LOGISTICS_BOOKING_STATUSES = ("BOOKED", "IN_TRANSIT", "DELIVERED", "CANCELLED")
DISTANCE_SOURCES = ("HAVERSINE_PROXY", "LIVE_OSM", "LIVE_OSM_ATTEMPTED_FALLBACK")


def enum(name, values):
    return SAEnum(*values, name=name)


# ---------------------------------------------------------------------------
# Users / profiles / FPOs (blueprint sections: Core Database Schema)
# ---------------------------------------------------------------------------

class User(Base):
    __tablename__ = "users"

    id = Column(BigInteger, primary_key=True)
    name = Column(String(200), nullable=False)
    phone = Column(String(20), nullable=False, unique=True)
    email = Column(String(255), unique=True)
    password_hash = Column(String(255), nullable=False)
    role = Column(enum("user_role", USER_ROLES), nullable=False)
    language = Column(String(10), nullable=False, server_default="en")
    location_text = Column(String(200))  # blueprint has location_id but defines no locations table
    created_at = Column(DateTime, nullable=False, default=_utcnow_naive)

    farmer_profile = relationship("FarmerProfile", back_populates="user", uselist=False)
    buyer_profile = relationship("BuyerProfile", back_populates="user", uselist=False)


class Fpo(Base):
    __tablename__ = "fpos"

    id = Column(BigInteger, primary_key=True)
    name = Column(String(200), nullable=False)
    district = Column(String(100), nullable=False)
    registration_number = Column(String(100), unique=True)
    verification_status = Column(enum("fpo_verification_status", VERIFICATION_STATUSES),
                                  nullable=False, server_default="PENDING")

    members = relationship("FpoMember", back_populates="fpo")


class FarmerProfile(Base):
    __tablename__ = "farmer_profiles"

    id = Column(BigInteger, primary_key=True)
    user_id = Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True)
    land_size = Column(Numeric(8, 2))  # acres
    primary_location = Column(String(200))
    fpo_id = Column(BigInteger, ForeignKey("fpos.id", ondelete="SET NULL"))
    preferred_language = Column(String(10), nullable=False, server_default="en")
    crops = Column(ARRAY(String))

    user = relationship("User", back_populates="farmer_profile")
    fpo = relationship("Fpo")


class FpoMember(Base):
    __tablename__ = "fpo_members"

    id = Column(BigInteger, primary_key=True)
    fpo_id = Column(BigInteger, ForeignKey("fpos.id", ondelete="CASCADE"), nullable=False)
    farmer_id = Column(BigInteger, ForeignKey("farmer_profiles.id", ondelete="CASCADE"), nullable=False)
    joined_at = Column(DateTime, nullable=False, default=_utcnow_naive)

    fpo = relationship("Fpo", back_populates="members")

    __table_args__ = (UniqueConstraint("fpo_id", "farmer_id", name="uq_fpo_member"),)


class BuyerProfile(Base):
    __tablename__ = "buyer_profiles"

    id = Column(BigInteger, primary_key=True)
    user_id = Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    external_buyer_code = Column(String(20), unique=True)  # e.g. "B0001", to trace back to buyers.json
    business_name = Column(String(200), nullable=False)
    buyer_type = Column(enum("buyer_type", BUYER_TYPES), nullable=False)
    verification_status = Column(enum("buyer_verification_status", VERIFICATION_STATUSES),
                                  nullable=False, server_default="PENDING")
    reliability_score = Column(Numeric(3, 1))  # matches buyers.json's 0.0-5.0 scale
    location = Column(String(200))
    payment_terms = Column(String(100))
    # From tools/augment_buyer_data.py -- see module docstring note (2).
    target_price_index = Column(Numeric(5, 3))
    minimum_grade_accepted = Column(enum("buyer_min_grade", GRADES))
    past_deals_completed = Column(Integer, server_default="0")
    past_deals_disputed = Column(Integer, server_default="0")
    registered_since = Column(Date)
    kyc_verified = Column(Boolean, nullable=False, server_default="false")
    data_status = Column(enum("buyer_data_status", DATA_STATUSES), nullable=False, server_default="SYNTHETIC")

    user = relationship("User", back_populates="buyer_profile")
    demands = relationship("BuyerDemand", back_populates="buyer")


# ---------------------------------------------------------------------------
# Commodities / markets / prices (blueprint section 7)
# ---------------------------------------------------------------------------

class Commodity(Base):
    __tablename__ = "commodities"

    id = Column(BigInteger, primary_key=True)
    name = Column(String(100), nullable=False, unique=True)
    local_name = Column(String(100))
    category = Column(String(100))


class Market(Base):
    __tablename__ = "markets"

    id = Column(BigInteger, primary_key=True)
    name = Column(String(200), nullable=False)
    apmc_name = Column(String(200))
    district = Column(String(100), nullable=False)
    state = Column(String(100), nullable=False, server_default="Maharashtra")
    latitude = Column(Numeric(9, 6))
    longitude = Column(Numeric(9, 6))

    __table_args__ = (UniqueConstraint("name", "district", name="uq_market_name_district"),)


class MarketPrice(Base):
    __tablename__ = "market_prices"

    id = Column(BigInteger, primary_key=True)
    market_id = Column(BigInteger, ForeignKey("markets.id", ondelete="CASCADE"), nullable=False)
    commodity_id = Column(BigInteger, ForeignKey("commodities.id", ondelete="CASCADE"), nullable=False)
    arrival_date = Column(Date, nullable=False)
    min_price = Column(Numeric(10, 2), nullable=False)
    max_price = Column(Numeric(10, 2), nullable=False)
    modal_price = Column(Numeric(10, 2), nullable=False)
    arrival_quantity = Column(Numeric(12, 2))
    unit = Column(String(20), nullable=False, server_default="quintal")
    source = Column(enum("market_price_source", MARKET_PRICE_SOURCES), nullable=False)
    created_at = Column(DateTime, nullable=False, default=_utcnow_naive)

    __table_args__ = (
        CheckConstraint("min_price >= 0 AND max_price >= min_price AND modal_price >= min_price "
                         "AND modal_price <= max_price", name="ck_market_price_bounds"),
        Index("ix_market_prices_lookup", "commodity_id", "market_id", "arrival_date"),
    )


class BuyerDemand(Base):
    __tablename__ = "buyer_demands"

    id = Column(BigInteger, primary_key=True)
    buyer_id = Column(BigInteger, ForeignKey("buyer_profiles.id", ondelete="CASCADE"), nullable=False)
    commodity_id = Column(BigInteger, ForeignKey("commodities.id", ondelete="CASCADE"), nullable=False)
    required_quantity = Column(Numeric(12, 2), nullable=False)
    minimum_grade = Column(enum("demand_min_grade", GRADES))
    maximum_grade = Column(enum("demand_max_grade", GRADES))
    target_price = Column(Numeric(10, 2))
    location = Column(String(200))
    valid_from = Column(Date)
    valid_until = Column(Date)
    status = Column(String(20), nullable=False, server_default="OPEN")

    buyer = relationship("BuyerProfile", back_populates="demands")


# ---------------------------------------------------------------------------
# Lots / images / grading (blueprint sections 9-11)
# ---------------------------------------------------------------------------

class Lot(Base):
    __tablename__ = "lots"

    id = Column(BigInteger, primary_key=True)
    farmer_id = Column(BigInteger, ForeignKey("farmer_profiles.id", ondelete="CASCADE"), nullable=False)
    fpo_id = Column(BigInteger, ForeignKey("fpos.id", ondelete="SET NULL"))
    commodity_id = Column(BigInteger, ForeignKey("commodities.id", ondelete="RESTRICT"), nullable=False)
    quantity = Column(Numeric(12, 2), nullable=False)
    unit = Column(String(20), nullable=False, server_default="quintal")
    grade = Column(enum("lot_grade", GRADES), nullable=False)
    harvest_date = Column(Date)
    location = Column(String(200))
    latitude = Column(Numeric(9, 6))
    longitude = Column(Numeric(9, 6))
    expected_price = Column(Numeric(10, 2))
    description = Column(Text)
    status = Column(enum("lot_status", LOT_STATUSES), nullable=False, server_default="DRAFT")
    created_at = Column(DateTime, nullable=False, default=_utcnow_naive)
    updated_at = Column(DateTime, nullable=False, default=_utcnow_naive, onupdate=_utcnow_naive)

    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_lot_quantity_positive"),
    )


class LotImage(Base):
    __tablename__ = "lot_images"

    id = Column(BigInteger, primary_key=True)
    lot_id = Column(BigInteger, ForeignKey("lots.id", ondelete="CASCADE"), nullable=False)
    image_url = Column(String(500), nullable=False)
    image_type = Column(String(50))
    created_at = Column(DateTime, nullable=False, default=_utcnow_naive)


class QualityGrade(Base):
    __tablename__ = "quality_grades"

    id = Column(BigInteger, primary_key=True)
    lot_id = Column(BigInteger, ForeignKey("lots.id", ondelete="CASCADE"), nullable=False)
    grade = Column(enum("quality_grade", GRADES), nullable=False)
    quality_score = Column(Numeric(5, 2))
    defect_percentage = Column(Numeric(5, 2))
    size_category = Column(String(50))
    moisture = Column(Numeric(5, 2))
    inspection_method = Column(String(50), nullable=False, server_default="AI_ESTIMATED")
    verified = Column(Boolean, nullable=False, server_default="false")

    __table_args__ = (
        CheckConstraint("inspection_method IN ('AI_ESTIMATED', 'HUMAN_VERIFIED')",
                         name="ck_quality_inspection_method"),
    )


# ---------------------------------------------------------------------------
# Offers / transactions / payments (blueprint sections 12-13, 18, 28-29)
# ---------------------------------------------------------------------------

class Offer(Base):
    __tablename__ = "offers"

    id = Column(BigInteger, primary_key=True)
    lot_id = Column(BigInteger, ForeignKey("lots.id", ondelete="CASCADE"), nullable=False)
    buyer_id = Column(BigInteger, ForeignKey("buyer_profiles.id", ondelete="CASCADE"), nullable=False)
    parent_offer_id = Column(BigInteger, ForeignKey("offers.id", ondelete="SET NULL"))
    offer_price_per_quintal = Column(Numeric(10, 2), nullable=False)
    quantity_quintals = Column(Numeric(12, 2), nullable=False)
    # Denormalized net-realization snapshot at offer time, from finance_tool.py,
    # so the audit trail shows what the farmer was actually told, even if the
    # underlying transport-cost table changes later.
    net_realization_at_offer = Column(Numeric(12, 2))
    match_score_at_offer = Column(Numeric(5, 2))  # from matching_engine.py, if this offer came from a ranked match
    status = Column(enum("offer_status", OFFER_STATUSES), nullable=False, server_default="PENDING")
    created_at = Column(DateTime, nullable=False, default=_utcnow_naive)
    updated_at = Column(DateTime, nullable=False, default=_utcnow_naive, onupdate=_utcnow_naive)
    expires_at = Column(DateTime)

    __table_args__ = (
        CheckConstraint("offer_price_per_quintal >= 0 AND quantity_quintals > 0",
                         name="ck_offer_values_positive"),
    )


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(BigInteger, primary_key=True)
    lot_id = Column(BigInteger, ForeignKey("lots.id", ondelete="RESTRICT"), nullable=False)
    offer_id = Column(BigInteger, ForeignKey("offers.id", ondelete="RESTRICT"), nullable=False, unique=True)
    farmer_id = Column(BigInteger, ForeignKey("farmer_profiles.id", ondelete="RESTRICT"), nullable=False)
    buyer_id = Column(BigInteger, ForeignKey("buyer_profiles.id", ondelete="RESTRICT"), nullable=False)
    quantity_quintals = Column(Numeric(12, 2), nullable=False)
    agreed_price_per_quintal = Column(Numeric(10, 2), nullable=False)
    gross_revenue = Column(Numeric(12, 2), nullable=False)
    net_realization = Column(Numeric(12, 2), nullable=False)  # from finance_tool.py at accept time
    status = Column(enum("transaction_status", TRANSACTION_STATUSES),
                     nullable=False, server_default="OFFER_ACCEPTED")
    created_at = Column(DateTime, nullable=False, default=_utcnow_naive)
    updated_at = Column(DateTime, nullable=False, default=_utcnow_naive, onupdate=_utcnow_naive)


class Payment(Base):
    __tablename__ = "payments"

    id = Column(BigInteger, primary_key=True)
    transaction_id = Column(BigInteger, ForeignKey("transactions.id", ondelete="CASCADE"),
                             nullable=False, unique=True)
    status = Column(enum("payment_status", PAYMENT_STATUSES), nullable=False, server_default="INITIATED")
    amount = Column(Numeric(12, 2), nullable=False)
    expected_payment_date = Column(Date)
    payment_reference = Column(String(100))
    # Blueprint section 29 is explicit: "Do NOT claim that the prototype is
    # processing real money unless actual payment infrastructure is
    # implemented." This column makes that constraint machine-checkable
    # rather than just a comment someone can forget.
    is_simulated = Column(Boolean, nullable=False, server_default="true")
    created_at = Column(DateTime, nullable=False, default=_utcnow_naive)
    updated_at = Column(DateTime, nullable=False, default=_utcnow_naive, onupdate=_utcnow_naive)


class LogisticsProvider(Base):
    """Mirrors data/logistics_providers.json -- see module docstring note (7)."""
    __tablename__ = "logistics_providers"

    id = Column(BigInteger, primary_key=True)
    external_provider_code = Column(String(20), unique=True)  # e.g. "LG001"
    name = Column(String(200), nullable=False)
    vehicle_types = Column(ARRAY(String))
    rate_per_km_per_quintal = Column(Numeric(6, 2), nullable=False)
    base_district = Column(String(100))
    avg_rating = Column(Numeric(3, 1))
    data_status = Column(enum("logistics_provider_data_status", DATA_STATUSES),
                          nullable=False, server_default="SYNTHETIC")


class ColdStorageFacility(Base):
    """Mirrors data/cold_storage.json -- see module docstring note (7)."""
    __tablename__ = "cold_storage_facilities"

    id = Column(BigInteger, primary_key=True)
    external_facility_code = Column(String(20), unique=True)  # e.g. "CS001"
    name = Column(String(200), nullable=False)
    district = Column(String(100), nullable=False)
    capacity_quintals = Column(Numeric(12, 2))
    available_capacity_pct = Column(Numeric(5, 2))
    rate_per_quintal_per_day = Column(Numeric(6, 2))
    supports_crops = Column(ARRAY(String))
    data_status = Column(enum("cold_storage_data_status", DATA_STATUSES),
                          nullable=False, server_default="SYNTHETIC")


class LogisticsBooking(Base):
    """See module docstring note (4): designed from blueprint prose, not a
    verbatim table the doc defines."""
    __tablename__ = "logistics_bookings"

    id = Column(BigInteger, primary_key=True)
    transaction_id = Column(BigInteger, ForeignKey("transactions.id", ondelete="CASCADE"), nullable=False)
    provider_id = Column(BigInteger, ForeignKey("logistics_providers.id", ondelete="SET NULL"))
    storage_facility_id = Column(BigInteger, ForeignKey("cold_storage_facilities.id", ondelete="SET NULL"))
    distance_km = Column(Numeric(8, 1))
    distance_source = Column(enum("distance_source", DISTANCE_SOURCES))
    transport_cost_inr = Column(Numeric(10, 2))
    status = Column(enum("logistics_booking_status", LOGISTICS_BOOKING_STATUSES),
                     nullable=False, server_default="BOOKED")
    created_at = Column(DateTime, nullable=False, default=_utcnow_naive)


# ---------------------------------------------------------------------------
# Grievances (blueprint section 21, reconciled with grievance_core.py --
# see module docstring note (3))
# ---------------------------------------------------------------------------

class Grievance(Base):
    __tablename__ = "grievances"

    id = Column(BigInteger, primary_key=True)
    ticket_id = Column(String(30), nullable=False, unique=True)  # GRV-YYYYMMDD-XXXXXX
    transaction_id = Column(BigInteger, ForeignKey("transactions.id", ondelete="SET NULL"))
    filed_by_user_id = Column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"))
    complaint_type = Column(enum("grievance_complaint_type", GRIEVANCE_COMPLAINT_TYPES), nullable=False)
    raw_complaint_type_input = Column(String(100))
    matched_known_category = Column(Boolean, nullable=False)
    complaint_text = Column(Text)
    days_since_issue = Column(Integer, nullable=False, server_default="0")
    escalated = Column(Boolean, nullable=False, server_default="false")
    severity = Column(enum("grievance_severity", GRIEVANCE_SEVERITIES), nullable=False)
    status = Column(enum("grievance_status", GRIEVANCE_STATUSES),
                     nullable=False, server_default="open_awaiting_response")
    created_at = Column(DateTime, nullable=False, default=_utcnow_naive)
    updated_at = Column(DateTime, nullable=False, default=_utcnow_naive, onupdate=_utcnow_naive)
    resolved_at = Column(DateTime)


# ---------------------------------------------------------------------------
# Audit logs (blueprint section 43)
# ---------------------------------------------------------------------------

class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(BigInteger, primary_key=True)
    user_id = Column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"))
    action = Column(String(100), nullable=False)
    entity_type = Column(String(100), nullable=False)
    entity_id = Column(String(100), nullable=False)
    timestamp = Column(DateTime, nullable=False, default=_utcnow_naive)
    event_metadata = Column(JSONB)  # blueprint calls this "metadata"; renamed to avoid
                                     # colliding with SQLAlchemy's reserved Base.metadata
