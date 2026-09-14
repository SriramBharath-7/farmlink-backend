"""
district_geo.py
----------------
Approximate lat/lon centroids for Maharashtra districts, used for
haversine distance estimates in the Logistics and Matching agents.
These are approximate (city/district-HQ level) — fine for hackathon-grade
"how far is this buyer" estimates, not survey-grade GIS.
"""

import math

DISTRICT_COORDS = {
    "Mumbai": (19.0760, 72.8777),
    "Thane": (19.2183, 72.9781),
    "Palghar": (19.6970, 72.7648),
    "Raigad": (18.5158, 73.1822),
    "Pune": (18.5204, 73.8567),
    "Nashik": (19.9975, 73.7898),
    "Ahmednagar": (19.0948, 74.7480),
    "Solapur": (17.6599, 75.9064),
    "Kolhapur": (16.7050, 74.2433),
    "Sangli": (16.8524, 74.5815),
    "Satara": (17.6805, 74.0183),
    "Ratnagiri": (16.9902, 73.3120),
    "Sindhudurg": (16.3667, 73.6833),
    "Aurangabad": (19.8762, 75.3433),
    "Jalgaon": (21.0077, 75.5626),
    "Dhule": (20.9042, 74.7749),
    "Nandurbar": (21.3667, 74.2333),
    "Nagpur": (21.1458, 79.0882),
    "Amravati": (20.9374, 77.7796),
    "Akola": (20.7096, 77.0022),
    "Latur": (18.4088, 76.5604),
    "Nanded": (19.1383, 77.3210),
    "Beed": (18.9894, 75.7601),
    "Yavatmal": (20.3897, 78.1204),
    "Wardha": (20.7453, 78.6022),
    "Buldhana": (20.5293, 76.1830),
    "Osmanabad": (18.1860, 76.0419),
    "Parbhani": (19.2704, 76.7734),
    "Hingoli": (19.7150, 77.1490),
    "Washim": (20.1097, 77.1330),
    "Chandrapur": (19.9615, 79.2961),
    "Gondia": (21.4602, 80.1922),
    "Bhandara": (21.1667, 79.6500),
    "Gadchiroli": (20.1809, 80.0021),
}


def haversine_km(coord1, coord2):
    lat1, lon1 = coord1
    lat2, lon2 = coord2
    r = 6371.0  # earth radius km
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
    c = 2 * math.asin(math.sqrt(a))
    return round(r * c, 1)


def distance_between_districts(district_a: str, district_b: str):
    """Returns distance in km between two Maharashtra districts, or None
    if either district isn't in the lookup table."""
    a = DISTRICT_COORDS.get(district_a.strip().title())
    b = DISTRICT_COORDS.get(district_b.strip().title())
    if not a or not b:
        return None
    return haversine_km(a, b)
