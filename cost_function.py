"""
cost_function.py — the unified weighted route-cost function.

    cost(u, v) = D(u, v) * [ 1 + wT*(T-1) + wC*(C-1) + wS*(S-1) ]

where
    D  = physical road length in km (edge.length_km)
    T  = traffic level in [1, 5]
    C  = road-condition penalty in [1, 5]
    S  = safety penalty for the active time-of-day (S_day or S_night) in [1, 4]
    wT + wC + wS = 1, all >= 0

A closed road is not traversable (cost = +inf).

The output is a *generalized weighted route cost*.  It is deliberately in the
same units as distance (km) when all penalties are at their minimum of 1, but
it is NOT travel time, money, or accident probability.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from config import Weights
from graph_data import Edge

INF = math.inf


@dataclass
class EdgeAttr:
    """Dynamic, (mostly) *simulated* attributes attached to one directed edge."""
    traffic: float = 1.0        # T in [1, 5]
    condition: float = 1.0      # C in [1, 5]
    safety_day: float = 1.0     # S_day in [1, 4]
    safety_night: float = 1.0   # S_night in [1, 4]
    closed: bool = False
    incident: str = ""          # "", "flood", "construction", "accident", ...

    def safety(self, night: bool) -> float:
        return self.safety_night if night else self.safety_day


def edge_cost(edge: Edge, attr: EdgeAttr, weights: Weights, night: bool) -> float:
    """Weighted cost of traversing one directed edge under the active scenario."""
    if attr.closed:
        return INF
    s = attr.safety(night)
    multiplier = (1.0
                  + weights.traffic * (attr.traffic - 1.0)
                  + weights.condition * (attr.condition - 1.0)
                  + weights.safety * (s - 1.0))
    # multiplier >= 1 always, because every penalty >= 1 and weights >= 0.
    return edge.length_km * multiplier


def explain_example(weights: Weights | None = None) -> str:
    """Return a worked numerical example of the cost formula (for the report/UI)."""
    w = (weights or Weights()).validate()
    D, T, C, Sd = 2.0, 4.0, 2.0, 3.0
    mult = 1 + w.traffic * (T - 1) + w.condition * (C - 1) + w.safety * (Sd - 1)
    cost = D * mult
    return (
        "Unified cost example (daytime):\n"
        f"  D = {D} km, T = {T}, C = {C}, S_day = {Sd}\n"
        f"  weights: wT={w.traffic}, wC={w.condition}, wS={w.safety}\n"
        f"  multiplier = 1 + {w.traffic}*({T}-1) + {w.condition}*({C}-1) + {w.safety}*({Sd}-1)\n"
        f"             = 1 + {w.traffic*(T-1):.3f} + {w.condition*(C-1):.3f} + {w.safety*(Sd-1):.3f}\n"
        f"             = {mult:.3f}\n"
        f"  cost = D * multiplier = {D} * {mult:.3f} = {cost:.3f} (weighted km)\n"
        f"  Physical length stays {D} km; the weighted cost is {cost:.3f}."
    )


if __name__ == "__main__":
    print(explain_example())
