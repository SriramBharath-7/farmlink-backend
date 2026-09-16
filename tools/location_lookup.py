"""Lookup-only names; never apply these to persisted AGMARKNET records."""

# Upstream spellings documented in db/ingest_agmarknet.py.
_STATE_ALIASES = {
    "keralam": "Kerala",
    "chattisgarh": "Chhattisgarh",
    "pondicherry": "Puducherry",
    "nct of delhi": "Delhi",
}


def normalize_location(state: str, district: str):
    state = " ".join(state.split()).title()
    district = " ".join(district.split()).title()
    state = _STATE_ALIASES.get(state.casefold(), state)
    # Observed in the production Onion discovery request; scoped to Kerala.
    if state == "Kerala" and district == "Palakad":
        district = "Palakkad"
    return state, district
