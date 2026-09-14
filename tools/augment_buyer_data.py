"""
augment_buyer_data.py
------------------------
ONE-TIME DATA FIX, not a runtime tool.

The original buyers.json (synthetic demo data) has no price or quality
fields at all -- it only has commodities_wanted / typical_volume /
reliability. The blueprint's own `buyer_demands` schema (section 8)
requires target_price and minimum_grade per buyer. Without those,
"buyer match scoring" and "net realization by buyer" are IMPOSSIBLE to
compute honestly -- there is no price to net anything against.

This script adds two fields, deterministically seeded (same seed as the
original generate_synthetic_data.py, so re-running is reproducible):

  - target_price_index: a multiplier applied to that day's local mandi
    modal price to derive this buyer's offer price (e.g. 1.04 = pays 4%
    over mandi modal -- processors/exporters typically pay a premium for
    guaranteed volume+grade; institutional buyers pay closer to modal).
  - minimum_grade_accepted: "A", "B", or "C" (a buyer requiring "A" will
    not be matched against a lower-grade lot).

This keeps the augmentation transparent and traceable, exactly like the
rest of the synthetic data in this project -- clearly labeled, not
pretending to be live.
"""

import json
import random
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INPUT_PATH = os.path.join(BASE_DIR, "..", "data", "buyers.json")
OUTPUT_PATH = os.path.join(BASE_DIR, "..", "data", "buyers_augmented.json")

random.seed(42)  # same seed convention as generate_synthetic_data.py

# Premium/discount ranges by buyer type -- documented assumption, not
# hidden magic: exporters/processors typically pay more for guaranteed
# grade+volume; traders/institutional buy closer to the mandi modal.
PRICE_INDEX_RANGE = {
    "exporter": (1.02, 1.15),
    "processor": (0.98, 1.08),
    "institutional": (0.95, 1.03),
    "trader": (0.92, 1.00),
}

GRADE_WEIGHTS = {"A": 0.35, "B": 0.45, "C": 0.20}


def _weighted_grade():
    grades, weights = zip(*GRADE_WEIGHTS.items())
    return random.choices(grades, weights=weights, k=1)[0]


def main():
    with open(INPUT_PATH, "r", encoding="utf-8") as f:
        buyers = json.load(f)

    for b in buyers:
        btype = b.get("type", "trader")
        lo, hi = PRICE_INDEX_RANGE.get(btype, (0.95, 1.05))
        b["target_price_index"] = round(random.uniform(lo, hi), 3)
        b["minimum_grade_accepted"] = _weighted_grade()
        b["data_status"] = "SYNTHETIC"
        b["field_provenance"] = {
            "target_price_index": "synthetic_augmentation_v1",
            "minimum_grade_accepted": "synthetic_augmentation_v1",
        }

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(buyers, f, indent=2, ensure_ascii=False)

    print(f"Wrote {len(buyers)} augmented buyer records -> {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
