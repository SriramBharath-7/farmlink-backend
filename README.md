# FarmLink — Deterministic Intelligence Layer + Database

This is an ADDITIVE package: nothing in your existing repo is deleted or
overwritten. Drop these folders/files alongside your current
`agents/`, `tools/`, `data/`, `api.py`.

**Built across two sessions** (deterministic core + adapters in one, the
Postgres schema in another) and reconciled/independently re-verified in
a third — see `AUDIT_REPORT.md`'s "COMPONENT #2" section for the full,
honest account of a real handoff discrepancy that happened and how it
was resolved. Don't take any test count on faith without checking that
section.

## What's in here

```
tools/
  forecast_tool.py                 price forecasting (moving avg + trend + confidence)
  finance_tool.py                  net realization (gross - transport - storage)
  matching_engine.py               weighted buyer match scoring
  logistics_core.py                pure distance/transport/storage functions
  grievance_core.py                pure grievance rules + farmer-message templating
  osm_routing_tool.py              real Nominatim+OSRM adapter w/ haversine fallback
  augment_buyer_data.py            one-time script: adds price/grade fields buyers.json lacked
  decision_pipeline.py             wires engines together; data_source_mode="direct"|"adapter"
  deterministic_crewai_tools.py    @tool wrappers so CrewAI agents call the engines directly
  precache_osm_district_coords.py  one-time script: run with real internet access (see note below)
  logistics_tools.py               delegates to logistics_core.py (bugfix included)
  grievance_rules_tool.py          delegates to grievance_core.py (bugfix included)
  district_geo.py                  unchanged copy, included because osm_routing_tool.py imports it
  adapters/                        source-agnostic adapter interface:
    schema.py                        PriceRecord/DistanceRecord w/ provenance tagging
    base_adapter.py                  SourceAdapter contract + AdapterError
    fallback_chain.py                generalized live->fallback priority chain
    agmarknet_adapter.py             wraps existing verified-real data.gov.in logic
    osm_adapter.py                   wraps OSM (live) + static haversine (standalone)
    registry.py                      get_price_chain()/get_routing_chain() factories

agents/
  orchestrator_v2.py               numbers computed before the LLM runs; LLM only narrates

db/
  models.py                        19-table SQLAlchemy schema (datetime.utcnow() dep. fixed)
  session.py                       engine/session factory, reads DATABASE_URL from env
  seed_from_json.py                one-shot loader: existing JSON files -> Postgres, SYNTHETIC-tagged

migrations/                        Alembic: full schema + transaction state-machine trigger
alembic.ini

api_v2.py                          structured FastAPI app (additive to your existing api.py)

tests/                             110 tests total -- see "Running tests" below

data/
  buyers_augmented.json            generated output of augment_buyer_data.py
  buyers.json, cold_storage.json, logistics_providers.json,
  mock_mandi_prices.json           COPIES of your existing data files, included only
                                    so this package is self-contained. Unmodified from
                                    your original project -- safe to skip when dropping
                                    into your existing repo.

AUDIT_REPORT.md                    full audit: what's implemented, tested, and still open
DB_REPORT.md                       superseded by AUDIT_REPORT.md's COMPONENT #2 section;
                                    kept for the full paper trail, not deleted
requirements-additions.txt         new deps beyond your existing requirements.txt
```

## Installation

```bash
pip install -r requirements-additions.txt --break-system-packages
# (your existing requirements.txt should already be installed for crewai/fastapi/etc.)
```

## Running tests

```bash
pytest tests/
# Expected: 128 passed
```

**Use `pytest tests/`, not `python3 -m unittest discover tests`.** This
matters: `tests/test_db_schema.py` is written in pytest style (bare
`def test_x():` functions), and `unittest discover` silently finds and
runs **zero** tests from it — no error, no skip notice, it just doesn't
count them. This exact gap caused a real test-count discrepancy between
two build sessions (see `AUDIT_REPORT.md`, COMPONENT #2). `pytest
tests/` correctly runs both styles together in one command.

96 of the 110 tests need no network or database access at all (mocked
HTTP for the OSM adapter tests). The remaining **14 tests in
`test_db_schema.py` require a reachable PostgreSQL server**:

```bash
export TEST_DATABASE_ADMIN_URL="postgresql+psycopg2://<user>:<password>@<host>:5432/postgres"
pytest tests/test_db_schema.py -v
```

The role in that URL needs `CREATEDB` privilege — the suite creates and
drops its own scratch database (`farmlink_schema_pytest`) and never
touches a real deployment database.

## Before using in production

1. **`tools/osm_routing_tool.py`**: replace `REPLACE_WITH_TEAM_EMAIL` in
   `USER_AGENT` with a real contact — required by Nominatim/OSRM's usage
   policies, not optional.
2. **`tools/precache_osm_district_coords.py`**: run once from a machine
   with normal internet access.
3. **`tools/logistics_tools.py`** / **`tools/grievance_rules_tool.py`**:
   these `import crewai` — compile cleanly and delegate to fully tested
   logic, but haven't been run through an actual CrewAI agent end-to-end.
4. **Database**: run `alembic upgrade head` against your real Postgres
   instance, then `python3 -m db.seed_from_json` to load demo data.
   `/agents/sell-decision` in `api_v2.py` can now read from Postgres —
   set `DATABASE_URL` and pass `"data_source_mode": "postgres"` in the
   request body. `"direct"` (the default) still reads flat JSON files
   unchanged. The other three endpoints (`match-buyers`, `logistics`,
   `grievance`) don't have a Postgres mode yet — only `sell-decision`.
5. Run `python3 tools/augment_buyer_data.py` if you want to regenerate
   `buyers_augmented.json` from a fresh `buyers.json`.

## Read this first

`AUDIT_REPORT.md` — specifically the CHECKLIST and COMPONENT #1/#2
sections — has the complete, current state. Don't take a claim at face
value anywhere in this package (including this README) without checking
that file.
