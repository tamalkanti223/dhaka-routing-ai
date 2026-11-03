"""Replay removable road edits and explain the effect of replanning."""
from __future__ import annotations

import math
from copy import deepcopy

from cost_function import edge_cost
from scenarios import apply_local_conditions

PARAMETERS = {
    "traffic": "Traffic", "condition": "Road condition",
    "safety_day": "Day safety", "safety_night": "Night safety",
    "closed": "Road closed", "incident": "Incident label",
}


def replay_edits(graph, baseline, edits):
    """Rebuild from the active scenario; later edits win on overlapping fields."""
    scenario = deepcopy(baseline)
    for edit in edits:
        edges = [graph.edges[eid] for eid in edit["eids"]]
        if edit["kind"] == "conditions":
            apply_local_conditions(scenario, edges, set(edit["effects"]),
                                   seed=baseline.seed, baseline=baseline.attrs)
        elif edit["kind"] == "values":
            values = edit["values"]
            for field, value in values.items():
                if field not in PARAMETERS:
                    raise ValueError(f"Unknown road parameter: {field}")
                if field in {"traffic", "condition", "safety_day", "safety_night"}:
                    high = 5 if field in {"traffic", "condition"} else 4
                    if not math.isfinite(value) or not 1 <= value <= high:
                        raise ValueError(f"{field} must be between 1 and {high}")
                elif field == "closed" and not isinstance(value, bool):
                    raise ValueError("closed must be a boolean")
                elif field == "incident" and value not in {"", "flood", "construction", "accident", "other"}:
                    raise ValueError("Unknown incident label")
            for edge in edges:
                scenario.set_attr(edge.eid, **values)
        else:
            raise ValueError(f"Unknown edit kind: {edit['kind']}")
    return scenario


def replace_edit(edits, edit):
    """Reapplying a condition selection replaces it instead of stacking duplicates."""
    if edit["kind"] == "values":
        return [*edits, edit]
    key = (edit["kind"], tuple(sorted(edit["eids"])))
    out = [old for old in edits
           if (old["kind"], tuple(sorted(old["eids"]))) != key]
    # An empty condition selection removes that edit and restores the baseline.
    if edit["kind"] != "conditions" or edit["effects"]:
        out.append(edit)
    return out


def compare_routes(before, after, scenario, weights, night):
    """Compare routes at identical NEW costs, alongside their previous cost."""
    rows = []
    for name, old in before.items():
        new = after.get(name)
        old_now = (sum(edge_cost(e, scenario.get(e.eid), weights, night)
                       for e in old.edge_path) if old.found else None)
        changed = (tuple(e.eid for e in old.edge_path) != tuple(e.eid for e in new.edge_path)
                   if old.found and new and new.found else None)
        rows.append({
            "algorithm": name,
            "before_status": old.status.value,
            "after_status": new.status.value if new else "not_run",
            "path_changed": changed,
            "before_km": round(old.distance_km, 4) if old.found else None,
            "after_km": round(new.distance_km, 4) if new and new.found else None,
            "previous_cost": round(old.weighted_cost, 4) if old.found else None,
            "old_path_at_new_cost": round(old_now, 4) if old_now is not None else None,
            "new_path_cost": round(new.weighted_cost, 4) if new and new.found else None,
        })
    return rows
