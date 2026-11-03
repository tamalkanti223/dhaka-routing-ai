"""
scenarios.py — build reproducible, seeded *simulated* dynamic edge attributes.

IMPORTANT HONESTY NOTE
----------------------
OpenStreetMap does NOT provide verified live congestion, accidents, flooding,
or street-crime data.  Every traffic / condition / safety value produced here
is SIMULATED.  Road class only informs simulation assumptions; these numbers
are not factual claims about any specific neighbourhood of Dhaka.

A Scenario freezes a full snapshot: for every directed edge it fixes
T, C, S_day, S_night, closed and incident.  The same seed + scenario name
reproduces the same snapshot exactly, so all six algorithms can be compared on
identical costs.
"""

from __future__ import annotations

import random
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

import config
from cost_function import EdgeAttr
from graph_data import RoadGraph, Edge

# Scenario presets: human-readable, each maps to a generator configuration.
SCENARIO_NAMES = [
    "normal_day",
    "rush_hour",
    "rain_flood",
    "construction_accident",
    "night",
    "manual",
]


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


@dataclass
class Scenario:
    """A frozen snapshot of dynamic attributes for one scenario."""
    name: str
    night: bool
    seed: int
    attrs: Dict[int, EdgeAttr] = field(default_factory=dict)   # eid -> EdgeAttr
    description: str = ""

    def get(self, eid: int) -> EdgeAttr:
        return self.attrs.get(eid, EdgeAttr())

    # -- user edits ------------------------------------------------------- #
    def set_closed(self, eid: int, closed: bool = True) -> None:
        self.attrs.setdefault(eid, EdgeAttr()).closed = closed

    def set_attr(self, eid: int, **kwargs) -> None:
        a = self.attrs.setdefault(eid, EdgeAttr())
        for k, v in kwargs.items():
            setattr(a, k, v)

    def close_road_both_directions(self, graph: RoadGraph, u: int, v: int) -> int:
        """Close every directed edge between u and v (either direction). Returns count."""
        n = 0
        for e in graph.edges.values():
            if (e.u, e.v) in {(u, v), (v, u)}:
                self.set_closed(e.eid, True)
                n += 1
        return n

    def num_closed(self) -> int:
        return sum(1 for a in self.attrs.values() if a.closed)

    def jam_edges(self, edges, traffic: float = 5.0, condition: float = 4.0) -> None:
        """Simulate a corridor jam: inflate traffic/condition on the given edges.

        Used to reproduce the 'longer physical route, lower weighted cost'
        demonstration — congest the direct corridor and the optimal weighted
        route detours onto a longer but cheaper backroad.
        """
        for e in edges:
            self.set_attr(e.eid, traffic=traffic, condition=condition)


def _base_ranges(highway: str) -> Tuple[Tuple[float, float], Tuple[float, float]]:
    """
    Return ((traffic_lo, traffic_hi), (condition_lo, condition_hi)) base ranges
    informed by road class.  Bigger roads tend to carry more traffic; smaller
    roads tend to have rougher surfaces.  These are modelling assumptions.
    """
    rank = config.HIGHWAY_RANK.get(highway, config.DEFAULT_RANK)
    # traffic grows for arterials (low rank), condition worsens for small roads
    traffic = (1.5, 3.5) if rank <= 3 else (1.0, 2.5)
    condition = (1.0, 2.0) if rank <= 2 else (1.0, 3.0)
    return traffic, condition


def _base_values(e: Edge, r: random.Random) -> Tuple[float, float, float, float]:
    """Baseline (traffic, condition, safety_day, safety_night) for one edge."""
    (t_lo, t_hi), (c_lo, c_hi) = _base_ranges(e.highway)
    traffic = r.uniform(t_lo, t_hi)
    condition = r.uniform(c_lo, c_hi)
    # Safety: smaller / unlit roads a bit less safe, worse at night.
    rank = config.HIGHWAY_RANK.get(e.highway, config.DEFAULT_RANK)
    base_safety = 1.0 + (rank / 8.0)            # 1.0 .. ~2.0
    safety_day = _clamp(base_safety + r.uniform(-0.3, 0.3), 1.0, 4.0)
    safety_night = _clamp(safety_day + r.uniform(0.5, 1.8), 1.0, 4.0)
    return traffic, condition, safety_day, safety_night


def build_scenario(graph: RoadGraph, name: str, seed: int = config.GLOBAL_SEED) -> Scenario:
    """Create a reproducible scenario snapshot for the whole graph."""
    if name not in SCENARIO_NAMES:
        raise ValueError(f"unknown scenario '{name}', choose from {SCENARIO_NAMES}")

    night = (name == "night")
    sc = Scenario(name=name, night=night, seed=seed)
    sc.description = _DESCRIPTIONS[name]

    # Per-edge deterministic RNG: seed combines global seed, scenario, edge id.
    name_hash = sum(ord(c) for c in name)
    flood_ways: set = set()
    construction_ways: set = set()
    accident_edges: set = set()

    # First pass: pick some ways to flood / block, deterministically.
    way_ids = sorted({e.way_id for e in graph.edges.values()})
    rng_global = random.Random(seed * 1000003 + name_hash)
    if name == "rain_flood":
        k = max(1, len(way_ids) // 12)   # ~8% of ways flooded
        flood_ways = set(rng_global.sample(way_ids, k))
    if name == "construction_accident":
        k = max(1, len(way_ids) // 25)   # ~4% under construction (closed)
        construction_ways = set(rng_global.sample(way_ids, k))
        # a handful of accident edges (not closed, but high penalty)
        all_eids = sorted(graph.edges.keys())
        accident_edges = set(rng_global.sample(all_eids, min(len(all_eids), 30)))

    for e in graph.edges.values():
        r = random.Random((seed << 20) ^ (name_hash << 10) ^ e.eid)
        traffic, condition, safety_day, safety_night = _base_values(e, r)

        closed = False
        incident = ""

        if name == "rush_hour":
            traffic = _clamp(traffic + 1.4, 1.0, 5.0)
        elif name == "night":
            traffic = _clamp(traffic - 0.8, 1.0, 5.0)   # lighter traffic
        elif name == "rain_flood":
            condition = _clamp(condition + 1.0, 1.0, 5.0)
            if e.way_id in flood_ways:
                condition = _clamp(condition + 1.8, 1.0, 5.0)
                incident = "flood"
                # deep flooding closes some segments
                if r.random() < 0.35:
                    closed = True
        elif name == "construction_accident":
            if e.way_id in construction_ways:
                closed = True
                incident = "construction"
            elif e.eid in accident_edges:
                traffic = _clamp(traffic + 2.0, 1.0, 5.0)
                condition = _clamp(condition + 1.0, 1.0, 5.0)
                incident = "accident"

        sc.attrs[e.eid] = EdgeAttr(
            traffic=round(traffic, 3),
            condition=round(condition, 3),
            safety_day=round(safety_day, 3),
            safety_night=round(safety_night, 3),
            closed=closed,
            incident=incident,
        )

    return sc


_DESCRIPTIONS = {
    "normal_day": "Baseline daytime conditions. Moderate traffic, roads mostly fine.",
    "rush_hour": "Peak-hour daytime congestion added to every road.",
    "rain_flood": "Heavy rain: degraded surfaces everywhere, some roads flooded/closed.",
    "construction_accident": "Scattered construction closures plus a few accident hotspots.",
    "night": "Nighttime: lighter traffic but higher safety penalties (uses S_night).",
    "manual": "Blank editable scenario — set every attribute yourself.",
}


# --------------------------------------------------------------------------- #
# Conditions on one stretch of road (user's choice, not random)
# --------------------------------------------------------------------------- #
# The same effects the whole-map scenarios use, applied only where the user
# asks.  Each is added on top of a normal-day baseline, so ticking and
# unticking an effect always gives the same result.
LOCAL_EFFECTS = {
    "closed": "🚫 Road closed (no reason given)",
    "rush_hour": "🚗 Rush hour (traffic +1.4)",
    "rain": "🌧️ Rain (condition +1.0)",
    "flood": "🌊 Flood (condition +2.8)",
    "deep_flood": "⛔ Deep flood: road closed",
    "construction": "🚧 Construction: road closed",
    "accident": "💥 Accident (traffic +2, condition +1)",
    "night": "🌙 Night (traffic −0.8, night-time safety)",
}


# Effects that block the road completely.
CLOSING_EFFECTS = {"closed", "construction", "deep_flood"}


def normal_day_attr(e: Edge, seed: int = config.GLOBAL_SEED) -> EdgeAttr:
    """The normal_day values of one edge (identical to build_scenario's)."""
    name_hash = sum(ord(c) for c in "normal_day")
    r = random.Random((seed << 20) ^ (name_hash << 10) ^ e.eid)
    t, c, sd, sn = _base_values(e, r)
    return EdgeAttr(traffic=round(t, 3), condition=round(c, 3),
                    safety_day=round(sd, 3), safety_night=round(sn, 3))


def with_reverse(graph: RoadGraph, edges: List[Edge]) -> List[Edge]:
    """The given edges plus the opposite direction of each, if it exists."""
    out = {e.eid: e for e in edges}
    for e in edges:
        for back in graph.neighbors(e.v):
            if back.v == e.u and back.way_id == e.way_id:
                out[back.eid] = back
    return list(out.values())


def apply_local_conditions(sc: Scenario, edges: List[Edge], effects: set,
                           seed: int = config.GLOBAL_SEED,
                           baseline: Dict[int, EdgeAttr] | None = None) -> None:
    """Apply effects to the selected scenario baseline (normal day by default)."""
    unknown = set(effects) - set(LOCAL_EFFECTS)
    if unknown:
        raise ValueError(f"unknown effects {sorted(unknown)}")
    for e in edges:
        a = deepcopy(baseline[e.eid]) if baseline is not None else normal_day_attr(e, seed)
        if "rush_hour" in effects:
            a.traffic += 1.4
        if "night" in effects:
            a.traffic -= 0.8
            a.safety_day = a.safety_night       # dark road even if the map is in day mode
        if "rain" in effects:
            a.condition += 1.0
        if "flood" in effects or "deep_flood" in effects:
            a.condition += 2.8
            a.incident = "flood"
        if "accident" in effects:
            a.traffic += 2.0
            a.condition += 1.0
            a.incident = "accident"
        if "construction" in effects:
            a.incident = "construction"
        a.closed = a.closed or bool(CLOSING_EFFECTS & set(effects))
        a.traffic = round(_clamp(a.traffic, 1.0, 5.0), 3)
        a.condition = round(_clamp(a.condition, 1.0, 5.0), 3)
        sc.attrs[e.eid] = a


def manual_scenario(graph: RoadGraph, night: bool = False,
                    seed: int = config.GLOBAL_SEED) -> Scenario:
    """A neutral scenario (all penalties = 1) the user edits by hand."""
    sc = Scenario(name="manual", night=night, seed=seed,
                  description=_DESCRIPTIONS["manual"])
    for e in graph.edges.values():
        sc.attrs[e.eid] = EdgeAttr()  # all defaults = 1, not closed
    return sc


def scenario_stats(graph: RoadGraph, sc: Scenario) -> dict:
    """Aggregate stats for display/report."""
    n = len(sc.attrs) or 1
    closed = sc.num_closed()
    incidents: Dict[str, int] = {}
    t_sum = c_sum = s_sum = 0.0
    for a in sc.attrs.values():
        t_sum += a.traffic
        c_sum += a.condition
        s_sum += a.safety(sc.night)
        if a.incident:
            incidents[a.incident] = incidents.get(a.incident, 0) + 1
    return {
        "scenario": sc.name,
        "night": sc.night,
        "edges": len(sc.attrs),
        "closed_edges": closed,
        "avg_traffic": round(t_sum / n, 3),
        "avg_condition": round(c_sum / n, 3),
        "avg_safety": round(s_sum / n, 3),
        "incidents": incidents,
    }
