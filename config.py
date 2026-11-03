"""
config.py — central configuration for the Dhaka routing project.

Everything that a user might reasonably want to tweak lives here so the rest of
the code never hard-codes magic numbers.  Values can also be overridden on the
command line (see benchmark.py) or from the Streamlit sidebar (see app.py).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #

# Default OSM source.  os.path.expanduser makes "~" resolve correctly on both
# Arch Linux (/home/you/Downloads/...) and Windows (C:\Users\you\Downloads\...).
_HERE = os.path.dirname(os.path.abspath(__file__))
# A copy of the map ships in data/ so the app also runs where ~/Downloads does
# not exist (for example when it is hosted online).
BUNDLED_OSM_FILE = os.path.join(_HERE, "data", "dhaka_central_roads.osm")
_DOWNLOADS_OSM = os.path.expanduser("~/Downloads/dhaka_central_roads.osm")
DEFAULT_OSM_FILE = _DOWNLOADS_OSM if os.path.exists(_DOWNLOADS_OSM) else BUNDLED_OSM_FILE

# Where the pickled, pre-processed graph is cached so later launches are fast.
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache")

# Where benchmark CSVs and plots are written.
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs")


# --------------------------------------------------------------------------- #
# Road classification
# --------------------------------------------------------------------------- #

# highway= values that a private car may drive on.  Anything not in this set
# (footway, path, pedestrian, steps, cycleway, crossing, ...) is excluded.
DRIVABLE_HIGHWAYS = {
    "motorway", "motorway_link",
    "trunk", "trunk_link",
    "primary", "primary_link",
    "secondary", "secondary_link",
    "tertiary", "tertiary_link",
    "unclassified",
    "residential",
    "living_street",
    "service",
    "road",            # generic / unknown classification, still drivable
}

# Highways that OSM marks as oneway by default even without an explicit tag.
DEFAULT_ONEWAY_HIGHWAYS = {"motorway", "motorway_link", "trunk_link"}

# highway= values that we treat as physically impassable (permanently closed).
CLOSED_HIGHWAYS = {"construction", "proposed"}

# A coarse "importance" rank used only to seed *simulated* traffic/condition
# assumptions.  Lower rank == bigger road.  This is an assumption, not a fact.
HIGHWAY_RANK = {
    "motorway": 0, "motorway_link": 0,
    "trunk": 1, "trunk_link": 1,
    "primary": 2, "primary_link": 2,
    "secondary": 3, "secondary_link": 3,
    "tertiary": 4, "tertiary_link": 4,
    "unclassified": 5, "road": 5,
    "residential": 6,
    "living_street": 7,
    "service": 8,
}
DEFAULT_RANK = 5


# --------------------------------------------------------------------------- #
# Cost-function weights
# --------------------------------------------------------------------------- #

@dataclass
class Weights:
    """Weights for the unified route-cost function.  Must be >= 0 and sum to 1."""
    traffic: float = 0.5
    condition: float = 0.3
    safety: float = 0.2

    def validate(self, tol: float = 1e-6) -> "Weights":
        for name, v in (("traffic", self.traffic),
                        ("condition", self.condition),
                        ("safety", self.safety)):
            if v < 0:
                raise ValueError(f"weight '{name}' must be >= 0 (got {v})")
        s = self.traffic + self.condition + self.safety
        if abs(s - 1.0) > tol:
            raise ValueError(f"weights must sum to 1.0 (got {s:.6f})")
        return self

    @classmethod
    def normalized(cls, traffic: float, condition: float, safety: float) -> "Weights":
        """Build a Weights object, normalising so the three sum to 1."""
        for name, v in (("traffic", traffic), ("condition", condition), ("safety", safety)):
            if v < 0:
                raise ValueError(f"weight '{name}' must be >= 0 (got {v})")
        s = traffic + condition + safety
        if s <= 0:
            raise ValueError("at least one weight must be positive")
        return cls(traffic / s, condition / s, safety / s)


DEFAULT_WEIGHTS = Weights()


# --------------------------------------------------------------------------- #
# Resource limits (keep the big graph responsive)
# --------------------------------------------------------------------------- #

@dataclass
class Limits:
    timeout_s: float = 10.0          # wall-clock cap per search
    expansion_budget: int = 400_000  # max node expansions before giving up
    ids_max_depth: int = 400         # deepest IDS iteration
    node_generation_budget: int = 3_000_000


DEFAULT_LIMITS = Limits()


# --------------------------------------------------------------------------- #
# Demonstration region (a small central bounding box for expensive algorithms)
# --------------------------------------------------------------------------- #
# Roughly central Dhaka (Motijheel / Shahbagh / Ramna).  min_lat,min_lon,max_lat,max_lon
DEMO_REGION_BBOX = (23.725, 90.390, 23.760, 90.425)


# --------------------------------------------------------------------------- #
# Landmark search aliases
# --------------------------------------------------------------------------- #
# The OSM extracts contain roads only (no buildings or campuses), so a landmark
# such as the Dhaka University campus has no entry of its own.  Each alias below
# points at a named road or junction that IS in the file; the label says which,
# so nobody is told a snapped road node is the landmark itself.  The first
# anchor present in the loaded map is used; aliases with no anchor are skipped.
LANDMARK_ALIASES = [
    ("Dhaka University / ঢাকা বিশ্ববিদ্যালয় — at TSC Mor", ["TSC Mor"]),
    ("Dhaka University / ঢাকা বিশ্ববিদ্যালয় — at Curzon Hall Road", ["Curzon Hall Road"]),
    ("Dhaka University / ঢাকা বিশ্ববিদ্যালয় — at Teachers Club Gate",
     ["Teachers Club Gate (place)"]),
    ("Jatiya Sangsad / Parliament — Sangsad Bhaban Road", ["Sangsad Bhaban Road"]),
    ("Airport area — Airport Road", ["Airport Road"]),
]


# --------------------------------------------------------------------------- #
# Reproducibility
# --------------------------------------------------------------------------- #
GLOBAL_SEED = 1234


@dataclass
class AppConfig:
    """Bundle passed around the app so nothing reaches for globals directly."""
    osm_file: str = DEFAULT_OSM_FILE
    simplify: bool = True
    use_largest_component: bool = True
    weights: Weights = field(default_factory=Weights)
    limits: Limits = field(default_factory=Limits)
    seed: int = GLOBAL_SEED
