# FarmLink — Deterministic Intelligence Layer: Audit Report

Scope of this pass: the data/intelligence core (your part), not the
frontend, database, auth, or full multi-source ingestion research. Every
claim below is backed by code that ran, not by a prompt that exists.

---

## CHECKLIST

```
[x] Price forecasting (deterministic, tested)
[x] Net realization engine (deterministic, tested, single source of truth)
[x] Buyer match scoring (deterministic, weighted, tested)
[x] Source/status provenance on outputs (SYNTHETIC/DERIVED tags)
[x] Live/synthetic distinction preserved (mandi_price_tool.py pattern kept)
[x] Fallback-with-disclosure (state-wide widen, flagged, not silent)
[x] Integration pipeline proven end-to-end on real project data files
[x] Unit tests (128 tests, all passing) — 96 no-DB + 32 Postgres-backed, see run-command note in COMPONENT #2/#3
[x] Agent-layer wiring: tool wrappers + reasoning-only task + hard overwrite
[x] Pure-function extraction of logistics/grievance tools (testable w/o crewai)
[x] Real bug found+fixed: grievance tool mislabeled unmatched categories
[x] e-NAM API access actually researched (web search) — confirmed no public API exists
[x] Real OSM (Nominatim + OSRM) adapter built against verified real API docs, mock-tested
[ ] OSM adapter never run against the live network — sandbox can't reach these domains, see note below
[x] Agmarknet API access actually researched — confirmed real, documented, NDSAP-licensed
[ ] Multi-source ingestion beyond Agmarknet — not possible for e-NAM (no public API); IMD/Bhuvan not researched yet
[x] Source-agnostic adapter interface (tools/adapters/ — schema, base contract, fallback chain, 2 real adapters, registry)
[ ] Caching layer
[ ] Frontend integration
[x] Database/schema implementation (PostgreSQL, tested against a real Postgres 16 instance — see COMPONENT #2)
[ ] Full security audit (auth, CORS, injection, secrets)
[x] `/agents/match-buyers`, `/agents/logistics`, `/agents/grievance` now structured (api_v2.py)
[x] `logistics_tools.py`/`grievance_rules_tool.py` actually repointed at core modules (not just documented) — bugfix is now live
[x] Full FastAPI structured layer, tested via TestClient with real HTTP requests (11 tests)
[ ] Demo-mode UI toggle (LIVE vs DEMO visual distinction)
[ ] Documentation rewrite (README/architecture.md)
```

---

## IMPLEMENTED

**`tools/forecast_tool.py`** — `forecast_price(records, horizon_days)`. Computes
current modal price, 7d/14d moving averages, trend classification
(UPWARD/DOWNWARD/STABLE via a documented ±1.5% threshold vs. an
excluding-today baseline), volatility (stdev of daily % change), an
expected range derived from trend + volatility (not fabricated), and a
confidence score with a written formula (`0.85 - data_penalty -
volatility_penalty`, clamped 0.30–0.90). Explicitly refuses to forecast
below 3 records (`insufficient_data: true`) rather than guessing. 8 unit
tests, all passing, including flat/upward/downward/noisy/unsorted-input
cases.

**`tools/finance_tool.py`** — `calculate_net_realization(...)` and
`compare_offers(...)`. One formula, used everywhere: Gross = price ×
qty; Net = Gross − transport − storage − other − platform fee. 8 unit
tests, including the exact "higher headline price loses after
transport" scenario from your own strategy doc (Buyer A ₹3000/q + ₹8000
transport vs Buyer B ₹2900/q + ₹2000 transport — B wins on net, as
claimed).

**`tools/matching_engine.py`** — `calculate_match_score(...)` and
`rank_matched_buyers(...)`. Implements the exact weighted formula from
the blueprint (Price 35/Demand Fit 20/Reliability 20/Distance 10/Quality
10/Quantity 5), each sub-score normalized 0–100 before weighting.
Buyers failing the grade requirement are scored but sorted to the
bottom regardless of price (verified by test — a buyer offering +10%
price but requiring grade A loses to a lower-price buyer accepting
grade B, when the farmer's lot is grade B). 7 unit tests.

**`tools/augment_buyer_data.py`** — a real gap the earlier audit missed:
`buyers.json` had no price or grade-requirement fields at all, so match
scoring was mathematically impossible without them. This adds
`target_price_index` (type-dependent premium/discount vs. mandi modal,
documented ranges) and `minimum_grade_accepted`, seeded and
reproducible, clearly tagged `"data_status": "SYNTHETIC"` with
per-field provenance. Ran once against the real `buyers.json`, produced
`buyers_augmented.json` (100 records).

**`tools/decision_pipeline.py`** — `build_sell_decision(...)`. Wires the
three engines together against your real data files
(`mock_mandi_prices.json`, `buyers_augmented.json`,
`logistics_providers.json`, `district_geo.py`). Ran live against
`Onion / Nashik / 20 quintals / grade A`: correctly discovered Nashik
has **zero** Onion price records in the dataset, widened to state-wide
data, and flagged `widened_to_statewide: true` instead of silently
returning nothing or hallucinating a Nashik-specific number. Returns a
ranked, fully-costed buyer shortlist with per-buyer score breakdowns.

**`tools/deterministic_crewai_tools.py`** — `@tool`-wrapped versions of
the above so CrewAI agents can call them directly instead of "reasoning"
about prices/scores in free text.

**`agents/orchestrator_v2.py`** — `run_sell_decision(...)`. The
belt-and-suspenders integration: the full numeric result is computed in
plain Python *before* the agent runs; the agent's only contribution is
a `reasoning_text` field; nothing the LLM does can alter a number in the
response, because the numbers were fixed before the LLM was ever called.

**Tests**: 23 tests across 3 files, `python3 -m unittest` run and passing
(shown in this session). Not padding — includes negative-value
rejection, zero-division guards, unsorted-input stability, and the
specific "distance beats headline price" and "grade mismatch sinks
ranking" scenarios that are the actual selling point of this project.
(This count is for the original three engines specifically; see "Files
delivered" below for the full 71-test suite across all subsequent passes.)

---

## REMAINING (genuinely not done — don't claim otherwise)

- **Multi-source ingestion adapters** (e-NAM, IMD, Bhuvan, state APMC
  portals): not built. This requires per-source legal/API research
  (terms of service, rate limits, auth) that's a research task, not a
  coding task — I have not verified e-NAM API terms and won't fabricate
  that verification. Your existing `mandi_price_tool.py`'s live/synthetic
  fallback pattern is the right shape to extend; I did not add new
  adapters this pass.
- ~~**Source-agnostic adapter interface**~~ — DONE, see "COMPONENT #1" section above.
- **Caching layer**: not built.
- **Database schema** (the full Postgres `users/lots/offers/transactions`
  model from the blueprint): not built. Everything here still reads
  flat JSON files, same as the existing prototype.
- **Frontend**: untouched. The structured JSON these tools now produce
  is ready for a frontend to consume, but no UI work was done.
- **Security audit**: not performed (secrets, CORS, injection, prompt
  injection on tool inputs). Flagging as open, not silently skipping.
- **`/agents/match-buyers`, `/agents/logistics`, `/agents/grievance`**
  API endpoints still return `{"result": "<free text>"}` per the
  existing `api.py` — only the new pipeline/orchestrator_v2 path is
  structured. Existing endpoints were left alone rather than rewritten
  wholesale, per "don't delete functional features" — recommend
  migrating them to the same pattern next.
- **Grievance and logistics tools**: already deterministic (confirmed in
  the first audit), but have zero automated tests. Should get the same
  test treatment as the three new engines.
- **Demo-mode visual toggle**: not applicable without frontend work.

---

## DATA SOURCES (this pass: actually researched, not assumed)

| Source | Access | Status (checked via web search this session) |
|---|---|---|
| data.gov.in / Agmarknet "Current Daily Price of Various Commodities" | **Real, documented, registration-based REST API.** Released under the National Data Sharing and Accessibility Policy (NDSAP) by the Ministry of Agriculture's Directorate of Marketing & Inspection. Free API key via data.gov.in signup, JSON responses, filterable by state/commodity/district. | Your existing `mandi_price_tool.py`'s `AGMARKNET_RESOURCE_ID` and endpoint pattern match this real, legitimate API. Whether *your specific key* is valid/working is still unverified (that requires an actual request with real credentials, which I can't do without them) — but the source itself is confirmed real and permitted, not fabricated. |
| e-NAM (enam.gov.in) | **No documented public developer API found.** It's a trading portal (web + mobile app) for registered mandi participants — bidding, MIS dashboards, arrivals/price viewing in-app. Searches turned up the official portal, Wikipedia, app store listings, and government scheme pages, but no public REST API documentation, no developer portal, no published rate limits or auth flow for third-party integration. | **Do not build an `enam_adapter` that calls a live e-NAM endpoint.** There's nothing documented to call. If e-NAM data is wanted, the only legitimate paths are: (a) an official data-sharing partnership with SFAC/the Ministry (a business development ask, not an engineering one), or (b) citing e-NAM's *published* statistics (mandi counts, commodity counts) as context, the way your own strategy doc already does, without pretending to have a live feed. |
| Open-Meteo (weather) | Live, no key needed, unaffected by this pass | Working |
| `mock_mandi_prices.json` | Local synthetic file | This is what `decision_pipeline.py` actually ran against |
| `buyers.json` → `buyers_augmented.json` | Local synthetic, now with price/grade fields | New this pass, clearly tagged |
| `logistics_providers.json`, `cold_storage.json` | Local synthetic | Unchanged |

**Implication for section 3/4 of the full spec**: the "multi-source ingestion" ask assumed several government sources would have open APIs the way Agmarknet does. That's not true across the board — e-NAM specifically does not. The honest architecture is: Agmarknet is your one real live government source; everything else (buyer directory, logistics, storage, reliability) is synthetic by necessity, not by laziness, because no public API exists for it. This is worth saying explicitly to a judge rather than implying more sources are "pending integration."

---

## OSM Integration (this pass — replacing the tech-stack slide's "OpenStreetMap" claim with real code)

`tools/osm_routing_tool.py` adds real Nominatim geocoding + OSRM routing,
verified against the current, actual API docs and usage policies (not
assumed from training data — checked via web search this session):

- **Nominatim** (`/search`): confirmed real, public, max 1 req/sec,
  requires an identifying User-Agent, results should be cached rather
  than re-queried.
- **OSRM demo server** (`router.project-osrm.org/route/v1/...`):
  confirmed real, public, same 1 req/sec cap, requires identifying
  User-Agent + ODbL attribution when displayed, explicitly "best effort,
  no uptime guarantee."

Because both are shared free demo servers with no uptime guarantee,
every call has a strict timeout and an **unconditional fallback** to the
existing, already-tested haversine x1.25 proxy — a farmer-facing
recommendation must never break because a free public server is slow.
Wired into `decision_pipeline.py` behind a `use_live_routing` flag
(default `False`), so nothing about the existing tested behavior changed
unless explicitly opted into.

**What I could not do in this sandbox**: `nominatim.openstreetmap.org`
and `router.project-osrm.org` are not in this environment's network
egress allowlist, so I could not make one real live call to prove this
end-to-end the way `decision_pipeline.py` was proven against your actual
JSON files. What I did instead:
1. Verified the exact request/response shape against the current, real
   API documentation (not memory).
2. Wrote the adapter against that verified shape, with mandatory fallback.
3. Tested the parsing, header, rate-limiting, and fallback logic with
   `unittest.mock` responses built from that same verified shape (13
   tests, all passing) — this proves the *code* is correct, not that the
   *live network path* has been exercised.

**Two things you must do before this goes live**:
1. Replace `REPLACE_WITH_TEAM_EMAIL` in `osm_routing_tool.USER_AGENT`
   with a real contact — both usage policies require this, and a fake
   User-Agent risks an IP ban for your whole team's IP range.
2. Run `python3 tools/precache_osm_district_coords.py` once, from a
   machine with normal internet access, to populate
   `data/osm_geocode_cache.json` before demo day — this also serves as
   your actual proof the live path works, and you'll be able to see for
   yourself how many of the ~33 districts resolve live vs. fall back.

---

## AGENTIC AI (what changed)

Before: `_price_task` and `_matching_task` prompts asked the LLM to
"analyse the trend" and "estimate net price... factor in reliability"
in free text — the arithmetic happened inside the model's generation,
invisibly.

After: `orchestrator_v2.run_sell_decision()` computes the entire numeric
result first; the single remaining agent call only writes
`reasoning_text`, and the orchestrator overwrites nothing it says back
into the numbers. The original `agent_definitions.py` agents/tools
(price, matching, logistics, grievance) are untouched and still work as
before — this is an additive path, not a replacement, so nothing that
worked before is broken.

---

## DETERMINISTIC INTELLIGENCE (exactly what backend code now calculates)

- Price trend, moving averages, expected range, confidence — `forecast_tool.py`
- Gross/net revenue, per-quintal net, offer comparison ranking — `finance_tool.py`
- Buyer price/demand-fit/reliability/distance/quality/quantity sub-scores,
  weighted match score, final ranking — `matching_engine.py`
- Distance (haversine + road proxy), cheapest transport quote — reused from
  existing `district_geo.py` / `logistics_providers.json`, now called
  directly by the pipeline instead of only being available to an agent

None of the above numbers can be altered by an LLM in the current
`decision_pipeline.py` / `orchestrator_v2.py` path.

---

## DEMO READINESS

**Will work reliably**: `build_sell_decision()` — no external network
call, no API key dependency, runs in milliseconds against local JSON,
produces a complete ranked buyer shortlist with net realization and
score breakdowns. Good for a live demo even with no internet.

**Will work if `.env` keys are present**: the original `mandi_price_tool.py`
live path and Open-Meteo weather — unaffected by this pass, same
reliability as before.

**Won't work yet**: anything requiring the frontend, the Postgres schema,
or e-NAM/IMD/Bhuvan data — those weren't touched.

---

## COMPONENT #1 — Source-agnostic adapter interface (this pass)

Built per the plan's component ordering, as a genuine, consumed
abstraction rather than parallel dead code:

- **`tools/adapters/schema.py`** — `PriceRecord`/`DistanceRecord`
  dataclasses enforcing the LIVE/CACHED/SYNTHETIC/DEMO/FALLBACK
  provenance tagging from spec section 4. One deliberate extension
  beyond the spec's list: added `DERIVED` for values like the haversine
  proxy, which aren't a "failure fallback" of anything — they're the
  intended, correct behavior of a specific adapter. Both dataclasses
  validate their own `data_status` on construction (can't build an
  invalid record).
- **`tools/adapters/base_adapter.py`** — `SourceAdapter` ABC +
  `AdapterError`. One contract: `fetch(**kwargs) -> List[Dict]`, must
  raise `AdapterError` (not return `[]`, not raise a bare `Exception`)
  when the adapter itself failed.
- **`tools/adapters/fallback_chain.py`** — `FallbackChain` generalizes
  the live→synthetic pattern that was previously hand-written
  separately inside `mandi_price_tool.py` and `osm_routing_tool.py` into
  one reusable primitive (spec section 5's priority-chain requirement).
- **`tools/adapters/agmarknet_adapter.py`** — wraps the existing,
  verified-real data.gov.in logic. Same resource ID, same live-then-
  synthetic behavior, zero duplicated logic — this is
  `mandi_price_tool.py`'s calculation made pluggable, not a rewrite.
- **`tools/adapters/osm_adapter.py`** — two adapters:
  `OSMRoutingAdapter` (wraps `osm_routing_tool.py`) and
  `StaticHaversineAdapter` (wraps `district_geo.py`, exposed as its own
  standalone, independently selectable adapter — not just an internal
  fallback baked into OSM's code path).
- **`tools/adapters/registry.py`** — `get_price_chain()` /
  `get_routing_chain()` factories so adding a third source later is a
  registry entry, not a hunt-and-replace across the codebase.

**Proof this isn't decorative**: `decision_pipeline.build_sell_decision()`
gained a `data_source_mode: "direct"|"adapter"` parameter. `"direct"`
(default) is byte-for-byte unchanged from before (tested explicitly).
`"adapter"` routes the exact same query through the new
`FallbackChain`/registry machinery instead of reading JSON files
directly. A cross-check test (`test_decision_pipeline_adapter_mode.py`)
confirms both paths produce **identical** price, recommendation, top
buyer, distance, and net realization for the same query — the adapter
layer is a genuine alternate route to the same numbers, not an unused
parallel structure.

One test (`test_real_chain_osm_then_static_haversine_end_to_end`)
deliberately does NOT mock the network — it exercises the real
`OSMRoutingAdapter` against this sandbox's actual (blocked) network
access, confirming the chain genuinely falls through to
`StaticHaversineAdapter` under real failure conditions, not just a
simulated one.

26 new tests this pass (19 for the adapter interface itself, 6 for the
direct/adapter cross-check, +1 already counted) — **96 total, all
passing.**



Previously I documented a refactor pattern for `logistics_tools.py` and
`grievance_rules_tool.py` and left the actual files untouched. That's
now done for real:

- **`logistics_tools.py`** and **`grievance_rules_tool.py`** are
  rewritten to delegate entirely to `logistics_core.py`/`grievance_core.py`
  — zero duplicated logic, same public `@tool` interface, same
  docstrings. The grievance labeling bug fix is now live in the file
  that actually ships, not just in an isolated core module.
- **`decision_pipeline.py`** gained `build_matching_result()` and
  `build_logistics_result()` — the same engines, exposed standalone for
  the `/agents/match-buyers` and `/agents/logistics` endpoints, so a
  caller doesn't have to run the full sell-decision just to get a buyer
  shortlist or a logistics quote. Both reuse the exact same underlying
  calculations as `build_sell_decision()` (verified by a test asserting
  the two paths produce byte-identical buyer shortlists).
- **`grievance_core.py`** gained `generate_farmer_message()` — a
  deterministic, templated explanation (no LLM) proving a rules-table
  result doesn't need an LLM to be explainable.
- **`api_v2.py`** — a real FastAPI app (additive; the original `api.py`
  is untouched) with typed Pydantic response models for all five
  endpoints. Ran through FastAPI's `TestClient` — actual HTTP request/
  response validation, not just calling Python functions directly.
  LLM narration (`reasoning_text`) is optional and **gracefully
  degrades**: if crewai/the LLM provider isn't available (confirmed in
  this sandbox — crewai isn't installed here), the endpoint still
  returns the full structured, correct result with
  `llm_explanation_status: "unavailable: ..."` instead of failing the
  request. This was actually exercised, not just designed — the test
  suite includes a case that hits this exact degraded path and confirms
  the numeric result is unaffected.
- 20 new tests this pass (7 for the matching/logistics pipeline
  extensions, 2 for the grievance message generator, 11 for the FastAPI
  layer) — 71 total, all passing.

**What I still could not execute**: `logistics_tools.py` and
`grievance_rules_tool.py` both `import crewai`, which isn't installed in
this sandbox — I verified they compile cleanly (`py_compile`) and that
the logic they delegate to is the same code covered by 17 passing tests,
but I have not run these two specific files end-to-end the way
`api_v2.py` was run end-to-end.

## COMPONENT #2 — PostgreSQL schema + migrations (built in a separate chat, reconciled and independently re-verified here)

**Context for whoever reads this next**: this component was built in a
different chat session, using a handoff that (incorrectly) claimed 96
passing tests and a completed adapter interface. Neither was true of the
zip actually attached there — the adapter interface genuinely didn't
exist in that zip's `tools/`. That chat caught the discrepancy itself
(documented in the now-superseded `DB_REPORT.md`, kept in this package
for the full paper trail) and correctly treated 71 as ground truth for
what it had. This section reconciles that work with the adapter
interface from COMPONENT #1, and independently re-verifies everything
rather than just merging the two chats' claims together.

### What was built (by the other session)

- **`db/models.py`** — SQLAlchemy declarative models for the full
  blueprint schema: 19 tables (`users`, `fpos`, `farmer_profiles`,
  `fpo_members`, `buyer_profiles`, `commodities`, `markets`,
  `market_prices`, `buyer_demands`, `lots`, `lot_images`,
  `quality_grades`, `offers`, `transactions`, `payments`,
  `logistics_providers`, `cold_storage_facilities`,
  `logistics_bookings`, `grievances`, `audit_logs`) plus Alembic's own
  `alembic_version`.
- **`migrations/`** — two Alembic revisions: the full schema
  (autogenerated against a real empty Postgres, not hand-typed DDL that
  could drift from the ORM models), plus a dedicated migration adding a
  **Postgres trigger function enforcing the blueprint's transaction
  state machine** at the database layer (section 28's "must never jump
  randomly between states" — enforced by Postgres itself, not just
  application code that could be bypassed).
- **`db/session.py`**, **`db/seed_from_json.py`** — engine/session
  factory (env-var driven, no hardcoded credentials) and a one-shot
  loader for the existing JSON demo data into the new tables, tagging
  every row `SYNTHETIC`.
- **`tests/test_db_schema.py`** — 14 tests, **written in pytest style**
  (bare `def test_x():` functions + `@pytest.fixture`), which matters —
  see "Real bug #2" below.

Documented schema deviations from the blueprint (the doc doesn't fully
specify `offers`/`transactions`/`payments`/`logistics`/`grievances`
columns, so these were designed from the blueprint's prose + the
deterministic core's actual tested output shapes) are in
`db/models.py`'s docstring and were reviewed, not just taken on faith.

**A real bug the other session caught in its own work**: Alembic's
autogenerated `downgrade()` dropped tables but not the standalone
Postgres ENUM types each `Enum` column creates, so
downgrade-then-upgrade failed with `type "..." already exists`. Fixed
with explicit `DROP TYPE IF EXISTS` statements, re-verified. Good
discipline, consistent with this project's standard throughout.

### What I independently verified in THIS session (not taken on trust)

I installed PostgreSQL 16 in this sandbox (`apt-get install postgresql`
— succeeded after retrying past a transient mirror 404), created the
`farmlink` role the test suite expects, and ran the actual tests myself:

- **`python3 -m pytest tests/test_db_schema.py -v` → all 14 passed**,
  against a real, live Postgres 16 server, right here, not asserted from
  the other session's report.
- Merged in the adapter interface (`tools/adapters/`,
  `test_adapters.py`, `test_decision_pipeline_adapter_mode.py`) from
  COMPONENT #1 — confirmed the other session's `decision_pipeline.py`
  was byte-for-byte the pre-adapter version (no DB-specific edits to
  it), so the merge is clean, nothing lost.
- **Combined: `python3 -m pytest tests/ -q` → 110 passed** (96 from
  COMPONENT #1's baseline + 14 DB tests), in one run, in this sandbox,
  right now.

### Real bug #2, found in this reconciliation pass: the test-count confusion had a root cause

`test_db_schema.py` is pytest-style; every other test file in this
project is `unittest.TestCase`-style. **`python3 -m unittest discover
tests`** — the documented run command everywhere else in this
project — **silently finds and runs 0 tests from a pytest-style file**.
Not an error, not a skip notice — it just doesn't appear in the count.
This is almost certainly *why* the other session's math read "71 + 14 =
85" instead of accounting for what should have been "96 + 14 = 110": the
71 baseline it had was correct for what was in its zip, but the standard
run command can't reveal the DB tests' existence either way — the person
running it has to already know to invoke pytest separately for that one
file.

**Fix applied**: confirmed `python3 -m pytest tests/` alone correctly
discovers and runs *all* tests, unittest-style and pytest-style, in one
command (110 passed, verified). This is now the single documented run
command in `README.md` — not "run these two different commands and
remember why."

### A third thing fixed in this pass: datetime.utcnow() deprecation

Running the DB tests produced **743 deprecation warnings** — Python is
scheduled to remove `datetime.utcnow()`, and `db/models.py` used it as
the `default=`/`onupdate=` callable on 16 columns. Fixed with a
`_utcnow_naive()` helper (`datetime.now(timezone.utc).replace(tzinfo=None)`
— same naive-UTC *value* `utcnow()` produced, matching the existing
plain `DateTime` column type exactly, so this is a warning fix with zero
behavior change, not a schema change). Re-ran the full 110-test suite
after the fix: still 110 passed, warnings gone. This wasn't flagged by
either prior session — found by actually reading the test output instead
of just checking the pass/fail line.

### What's still NOT done (from the other session's own honest accounting, unchanged)

- `api_v2.py` is **not** wired to Postgres — still reads flat JSON
  files. The DB exists and is correct on its own; nothing consumes it
  yet. (This is the gap flagged before this component started — still
  open, not silently resolved by this component's completion.)
- No live Agmarknet ingestion pipeline — `seed_from_json.py` is
  explicitly a one-shot demo loader, says so in its own docstring.
- No `locations` table (blueprint implies one via `users.location_id`
  but never defines it) — modeled as a plain text field instead of
  inventing an unspecified table.
- 5 separate `A`/`B`/`C` enum types instead of one shared grade enum —
  functionally correct, not the most elegant schema, flagged rather than
  silently left.
- Caching layer, frontend, security audit — untouched, per the existing
  plan.

### Corrected total

**110/110 tests passing at the time** (COMPONENT #3 below adds the
Postgres repository layer, pipeline cross-check, and API wiring on top
of this — 128 total by the end of that pass), independently verified
against a real
Postgres 16 instance and a fresh Python environment in this session, run
via the single command `pytest tests/` from the project root.



```
tools/forecast_tool.py
tools/finance_tool.py
tools/matching_engine.py
tools/augment_buyer_data.py
tools/decision_pipeline.py
tools/deterministic_crewai_tools.py
tools/logistics_core.py          (new: pure extraction, see refactor note in file)
tools/grievance_core.py          (new: pure extraction; fixed a real labeling bug)
tools/osm_routing_tool.py        (new: real Nominatim+OSRM adapter, mock-tested)
tools/precache_osm_district_coords.py  (new: one-time setup script, run outside this sandbox)
agents/orchestrator_v2.py
tests/test_forecast.py
tests/test_finance.py
tests/test_matching.py
tests/test_logistics_core.py     (new)
tests/test_grievance_core.py     (updated: +2 tests for generate_farmer_message)
tests/test_osm_routing_tool.py   (13 tests, mocked HTTP, verified real API shapes)
tests/test_decision_pipeline_extensions.py  (new: 7 tests)
tests/test_api_v2.py             (new: 11 tests, real FastAPI TestClient)
tools/adapters/                  (new: schema, base_adapter, fallback_chain,
                                  agmarknet_adapter, osm_adapter, registry)
tests/test_adapters.py           (new: 19 tests)
tests/test_decision_pipeline_adapter_mode.py  (new: 6 tests, direct/adapter cross-check)
api_v2.py                        (new: structured FastAPI layer, additive to api.py)
data/buyers_augmented.json       (generated output)
```

Drop the `tools/` and `agents/` files into your existing repo alongside
the current ones (nothing existing was deleted or overwritten). Run
`python3 -m unittest discover tests` from the project root — 96 tests,
all passing (includes real FastAPI TestClient requests against every
endpoint in api_v2.py, not just isolated function calls).

**One action item for the existing codebase**: `grievance_rules_tool.py`
has the labeling bug described above (an unrecognized complaint_type
gets the "other" rule applied but keeps displaying the raw unmatched
string as if it were a real category). `grievance_core.py`'s
`evaluate_grievance()` fixes this — recommend pointing the existing
`@tool`-decorated function at it per the refactor note in
`logistics_core.py`/`grievance_core.py`.

## COMPONENT #3 — Wiring the API to Postgres (this pass — the gap flagged across three prior sessions, actually closed now)

Every session up to this point, including COMPONENT #2's own honest
accounting, said the same true thing: the database exists and is
correct, but nothing reads or writes it — `api_v2.py` still read flat
JSON files. This pass closes that gap for real, and found two
independent, previously-undetected bugs while doing it.

### Real bug #1: `buyer_demands` was silently empty

`db/seed_from_json.py`'s `seed_buyers()` inserted `BuyerProfile` rows
only — it never read `commodities_wanted` or `typical_volume_quintals`
from the source JSON, so `buyer_demands` (the table that actually
records which commodities a buyer wants and how much) was **completely
empty** after seeding. Every existing test passed anyway, because
`test_seed_script_matches_json_file_row_counts` only checked
`buyer_profiles`/`market_prices`/logistics/storage row counts — nobody
had checked this specific table. Without it, buyer matching against
Postgres was structurally impossible: there was no way to know what a
buyer wanted at all.

**Fixed**: `seed_buyers()` now inserts one `buyer_demands` row per
`(buyer, commodity in commodities_wanted)`, with `required_quantity` set
from `typical_volume_quintals`. `target_price` is deliberately left
`NULL` — this project's pricing model (`matching_engine.py`) derives a
buyer's offer price dynamically as `target_price_index × that day's
mandi modal price`, not a fixed stored figure; storing a snapshot would
go stale the moment mandi prices move. Verified: `265` buyer_demands
rows inserted, cross-checked against the source JSON
(`sum(len(b["commodities_wanted"]) for b in buyers) == 265`, exact
match), plus a spot-check of one specific buyer's rows against its JSON
record. New assertions folded into the existing seed test (not a
separate test — `test_db` is module-scoped and `seed_from_json.run()`
can only run once per module without a unique-constraint violation on a
second insert of the same buyers).

### Real bug #2: a connection leak in `seed_from_json.run()`, and a wrong first fix

Writing `tests/test_repositories.py` (new, 9 tests, exercising the
Postgres-backed data-access layer against a real seeded database) kept
failing at teardown: `DROP DATABASE` → `"database is being accessed by
other users"`. Traced with `pg_stat_activity` directly (not guessed):
`seed_from_json.run()` called `session.close()` but never disposed the
underlying engine — invisible for a one-shot CLI script (the process
exits, the OS reclaims the socket) but a genuine leak when `run()` is
called as a library function inside a longer-lived process. **First
fix attempt was itself wrong**: called `get_engine(database_url)`
separately and disposed *that* — but `get_session_factory()` calls
`get_engine()` internally too, creating a second, different engine
instance bound to the actual session, which was never disposed. Re-ran
the exact failure scenario after the first "fix" — still leaked,
confirming the fix hadn't worked before claiming otherwise. Correct
fix: get the engine the session is actually bound to via
`session.get_bind()`, and dispose that one. Verified with a direct
`pg_stat_activity` check showing zero connections after `run()`
returns, then re-ran the full test suite to confirm.

### What was built

- **`db/repositories.py`** — `get_price_records()` and
  `get_candidate_buyers()`, returning plain dicts shaped **exactly**
  like the JSON-file path already produces (`arrival_date` as a
  `"DD/MM/YYYY"` string, not a `Date` object — formatted at the
  repository boundary specifically so `forecast_tool.py` needs zero
  changes to consume DB-backed data). 9 tests, including cross-checks
  against the source JSON (not just row counts) and a direct feed into
  `forecast_price()`/`rank_matched_buyers()` proving no shim is needed.
- **`decision_pipeline.build_sell_decision()`** gained a third
  `data_source_mode="postgres"` option (alongside the existing
  `"direct"`/`"adapter"`), requiring an explicit `db_session` —
  deliberately raises `ValueError` rather than silently falling back to
  JSON if one isn't provided, since that would defeat the entire point
  of asking for DB-backed data. Cross-checked against `"direct"` mode
  for the same query: **identical** price, trend, recommendation, top
  buyer, and net realization (`tests/test_decision_pipeline_postgres_
  mode.py`, 6 tests).
- **`api_v2.py`**'s `/agents/sell-decision` endpoint gained
  `data_source_mode` in its request body. Opens a DB session lazily
  (only when `"postgres"` is requested — no hard `sqlalchemy` dependency
  for callers who never use it), closes it in a `finally` block
  regardless of outcome, and returns a clean `503` with a reason
  (`{"error": "postgres_unavailable", "reason": "..."}`) if the database
  is unreachable — never a raw, unhandled `500`. Verified via real HTTP
  requests (`fastapi.testclient.TestClient`) against both a live seeded
  database and a deliberately-unreachable one
  (`tests/test_api_v2_postgres_mode.py`, 3 tests).

### Real bug #3: the same engine-leak pattern, a third time, in `api_v2.py` itself

While stress-testing the integration (running the full suite repeatedly
from a cold zip extraction), one specific test intermittently errored
at fixture setup with a raw connection failure — roughly 1 in 4-5 runs,
never reproducing in isolation. Two wrong theories were tried and ruled
out before finding the real cause, documented here rather than only
showing the final answer:

1. **First guess: DDL lock contention** between two module-scoped
   fixtures' `CREATE`/`DROP DATABASE` calls. This was **wrong** — it was
   an early, cheap explanation than didn't hold up to further testing.
2. **Second attempt: moved the failing test off the shared DB-seeding
   fixture**, since its actual assertion (a 422 on invalid
   `data_source_mode`) happens before any DB call in `sell_decision()`
   and genuinely didn't need a database. This was a real improvement
   (a test shouldn't depend on infrastructure it doesn't use) but
   **did not fix the flake** — it still failed intermittently afterward,
   proving the problem was never really about that one test.
3. **Actual root cause, found by re-reading `api_v2.py`'s own code**:
   `sell_decision()`'s `finally` block called `db_session.close()` but
   never disposed the engine `_open_db_session()` created for that
   request — the exact same leak pattern as `seed_from_json.run()`
   (real bug #2 above), independently present a second time in a
   different file. Every `data_source_mode="postgres"` request leaked
   one engine's connection pool; enough accumulated leaked connections
   eventually blocked a test fixture's `DROP DATABASE`.

**Fixed** the same way as bug #2: get the engine via
`db_session.get_bind()` and dispose it explicitly, after closing the
session. **Verified properly this time**: 8 consecutive full-suite runs
from the same cold extraction, all 128/128 clean — not "it passed once
after the fix," which is what made the first two (wrong) attempts look
resolved when they weren't.

**The pattern worth naming**: three real bugs this session
(`buyer_demands` silently empty, and this exact same
session-closed-but-engine-never-disposed mistake made independently
twice, in two different files) all came from the same underlying habit
— trusting that closing a SQLAlchemy `Session` is equivalent to
releasing its connection. It isn't. Anywhere else in this codebase that
opens a session outside of a request/test lifecycle scope managed by a
framework should be checked for the same mistake before being trusted.

**One more change made along the way, kept separate from the above
because it did NOT turn out to be the fix for this specific flake**:
`db/session.py`'s `get_engine()` now sets `pool_pre_ping=True`, which
makes SQLAlchemy verify a pooled connection is still alive before
handing it out, transparently reconnecting if not. This is standard
practice for any long-lived connection pool in a real deployment
(handles DB restarts, failovers, firewall-dropped idle connections) —
genuinely worth having regardless of whether it explains any single
observed test flake.

### Verification standard held throughout

Every claim above was checked, not asserted:
- Installed Postgres 16 in this sandbox myself (still running from
  COMPONENT #2's verification), and used `pg_stat_activity` directly to
  inspect actual connection state rather than inferring it from test
  pass/fail alone.
- Manually reproduced each leak in isolation before and after each fix
  attempt — including catching that the FIRST fix attempt for bug #2
  didn't actually work, by re-running the exact failure scenario rather
  than trusting the fix on sight.
- For the intermittent flake specifically: ran the full suite **8
  consecutive times** after the real fix, from the same cold
  extraction, 128/128 every time — not a single lucky pass, and not
  stopping at the first two (wrong) explanations that happened to
  coincide with a clean run.
- **Corrected total: 128 tests, all passing**, reproducibly, one
  command (`pytest tests/`), against a real Postgres 16 instance.


### What's still NOT done

- No other API endpoints (`/agents/match-buyers`, `/agents/logistics`,
  `/agents/grievance`) have a `data_source_mode="postgres"` option yet —
  only `/agents/sell-decision`. Same pattern would extend cleanly via
  `build_matching_result()`/`build_logistics_result()`, not done this
  pass.
- No connection pooling tuning for production load — `db/session.py`'s
  defaults are untouched; fine for a demo, not load-tested.
- Caching layer, frontend, security audit, deployment — unchanged, still open.



## Files delivered (across all passes, including COMPONENT #3)

```
tools/forecast_tool.py
tools/finance_tool.py
tools/matching_engine.py
tools/augment_buyer_data.py
tools/decision_pipeline.py         (data_source_mode="direct"|"adapter"|"postgres")
tools/deterministic_crewai_tools.py
tools/logistics_core.py
tools/grievance_core.py
tools/osm_routing_tool.py
tools/precache_osm_district_coords.py
tools/logistics_tools.py           (delegates to logistics_core.py)
tools/grievance_rules_tool.py      (delegates to grievance_core.py, bugfix live)
tools/adapters/                    schema.py, base_adapter.py, fallback_chain.py,
                                    agmarknet_adapter.py, osm_adapter.py, registry.py
agents/orchestrator_v2.py
api_v2.py                          (sell-decision endpoint now supports postgres mode)
db/models.py                       (19 tables, datetime.utcnow() deprecation fixed)
db/session.py
db/seed_from_json.py               (buyer_demands population + connection-leak fixed)
db/repositories.py                 (new: Postgres-backed data access, JSON-shape-compatible)
migrations/                        2 Alembic revisions (schema + state-machine trigger)
alembic.ini
tests/test_forecast.py
tests/test_finance.py
tests/test_matching.py
tests/test_logistics_core.py
tests/test_grievance_core.py
tests/test_osm_routing_tool.py
tests/test_decision_pipeline_extensions.py
tests/test_api_v2.py
tests/test_adapters.py
tests/test_decision_pipeline_adapter_mode.py
tests/test_db_schema.py                       (pytest-style, needs Postgres)
tests/test_repositories.py                    (new: 9 tests, needs Postgres)
tests/test_decision_pipeline_postgres_mode.py (new: 6 tests, needs Postgres)
tests/test_api_v2_postgres_mode.py            (new: 3 tests, needs Postgres)
data/buyers_augmented.json
data/buyers.json, cold_storage.json, logistics_providers.json, mock_mandi_prices.json
DB_REPORT.md                       (superseded by COMPONENT #2/#3 sections;
                                     kept for the full paper trail, not deleted)
```

Run everything with **`pytest tests/`** (not `unittest discover`, which
silently misses every pytest-style file above). 96 tests need no
network/DB at all; the other 32 need a reachable Postgres — see
`README.md` for the exact env var and setup.

**Corrected total: 128 tests, all passing**, verified from this session
against a real, live Postgres 16 instance.

## Still open after this pass

Everything listed as REMAINING above, plus: IMD/Bhuvan/state-mandi-portal
access was not researched this session (only Agmarknet and e-NAM were,
since those were the two named explicitly in your strategy doc). The
source-agnostic adapter interface, caching, DB schema, frontend, and
security audit are unchanged from the previous pass — still not done,
still not being claimed as done.
