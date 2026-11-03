"""
heuristics.py — geographic heuristic for GBFS / A*, plus admissibility and
consistency verification against the loaded graph.

Heuristic
---------
    h(n) = HaversineDistanceKm(n, goal) * minimum_possible_cost_multiplier

For our cost function the minimum possible multiplier is 1 (every penalty is
>= 1 and all weights >= 0), so:

    h(n) = HaversineDistanceKm(n, goal)

Why this is admissible and consistent
-------------------------------------
* The weighted cost of any edge (u, v) is  length_km(u,v) * mult, mult >= 1,
  so  edge_cost(u, v) >= length_km(u, v).
* Road length along geometry >= straight-line (haversine) distance between its
  endpoints:  length_km(u, v) >= haversine(u, v).
* Haversine satisfies the triangle inequality on the sphere:
  haversine(u, goal) <= haversine(u, v) + haversine(v, goal).

Therefore
    h(u) = haversine(u, goal)
         <= haversine(u, v) + haversine(v, goal)
         <= length_km(u, v) + h(v)
         <= edge_cost(u, v) + h(v)             (consistency)
and h(goal) = 0, h never overestimates the true remaining weighted cost
(admissibility).  Admissibility follows from consistency for a goal with
h(goal)=0.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from graph_data import RoadGraph, haversine_km, Coord

MIN_COST_MULTIPLIER = 1.0  # for this cost function


def make_heuristic(graph: RoadGraph, goal: int):
    """Return h(n) = haversine(n, goal) * MIN_COST_MULTIPLIER (km)."""
    goal_coord: Coord = graph.coord(goal)

    def h(n: int) -> float:
        return haversine_km(graph.coord(n), goal_coord) * MIN_COST_MULTIPLIER

    return h


@dataclass
class HeuristicReport:
    goal_zero_ok: bool
    geometry_ok: bool               # length_km >= haversine(endpoints) within tol
    geometry_violations: int
    worst_geometry_gap_km: float
    consistency_ok: bool            # h(u) <= cost_min(u,v) + h(v) within tol
    consistency_violations: int
    worst_consistency_gap: float
    checked_edges: int
    tolerance_km: float

    def summary(self) -> str:
        return (
            f"Heuristic validation (tol={self.tolerance_km} km):\n"
            f"  h(goal) == 0                : {'OK' if self.goal_zero_ok else 'FAIL'}\n"
            f"  length >= straight-line     : {'OK' if self.geometry_ok else 'FAIL'} "
            f"({self.geometry_violations} violations, worst {self.worst_geometry_gap_km:.4f} km)\n"
            f"  consistency h(u)<=c+h(v)    : {'OK' if self.consistency_ok else 'FAIL'} "
            f"({self.consistency_violations} violations, worst {self.worst_consistency_gap:.4f})\n"
            f"  edges checked               : {self.checked_edges:,}"
        )


def verify_heuristic(graph: RoadGraph, goal: int, tol_km: float = 1e-6,
                     max_edges: int | None = None) -> HeuristicReport:
    """
    Verify admissibility prerequisites and consistency on the real graph.

    We use the *minimum* edge cost (= length_km, multiplier 1) for the
    consistency test, because the heuristic must stay consistent for every
    scenario, and every scenario has edge_cost >= length_km.
    """
    h = make_heuristic(graph, goal)
    geo_viol = 0
    worst_geo = 0.0
    con_viol = 0
    worst_con = 0.0
    checked = 0

    for e in graph.edges.values():
        checked += 1
        straight = haversine_km(graph.coord(e.u), graph.coord(e.v))
        if e.length_km + tol_km < straight:
            geo_viol += 1
            worst_geo = max(worst_geo, straight - e.length_km)
        # consistency with minimal edge cost (length_km)
        lhs = h(e.u)
        rhs = e.length_km + h(e.v)
        if lhs > rhs + tol_km:
            con_viol += 1
            worst_con = max(worst_con, lhs - rhs)
        if max_edges and checked >= max_edges:
            break

    return HeuristicReport(
        goal_zero_ok=abs(h(goal)) <= tol_km,
        geometry_ok=geo_viol == 0,
        geometry_violations=geo_viol,
        worst_geometry_gap_km=worst_geo,
        consistency_ok=con_viol == 0,
        consistency_violations=con_viol,
        worst_consistency_gap=worst_con,
        checked_edges=checked,
        tolerance_km=tol_km,
    )
