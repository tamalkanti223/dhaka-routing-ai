"""
coverage.py — where on the map of Dhaka does the loaded road network reach?

The page built here answers "from where to where is the map?" from the graph
itself, not from a hand-drawn outline:

* a shaded footprint: every ~0.5 km cell that contains at least one road
  junction, darker where there are more;
* the four extreme junctions (northmost, southmost, eastmost, westmost) with
  their road names;
* well-known areas of Dhaka, each coloured by how many road junctions lie within
  1.5 km of its (approximate) centre: covered / thin / not covered.

The area centres are rough positions typed in by hand, used only to count nearby
road junctions; they are not official boundaries.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Dict, List, Optional, Tuple

import visualizer as viz
from graph_data import RoadGraph

CELL_DEG = 0.005                 # footprint cell, about 0.5 km
RADIUS_KM = 1.5                  # junctions counted around an area centre
COVERED_MIN = 150                # junctions within RADIUS_KM to call an area covered
THIN_MIN = 20

# (name, lat, lon): approximate centres of well-known areas, for counting only.
AREAS: List[Tuple[str, float, float]] = [
    ("Uttara (centre)", 23.8759, 90.3995), ("Uttara (south)", 23.8500, 90.4000),
    ("Tongi", 23.8900, 90.4000), ("Savar", 23.8483, 90.2570),
    ("Mirpur 10", 23.8069, 90.3687), ("Mirpur DOHS", 23.7950, 90.3530),
    ("Cantonment / Kuril", 23.8200, 90.4200), ("Airport / Khilkhet", 23.8300, 90.4200),
    ("Gulshan", 23.7925, 90.4143), ("Banani", 23.7937, 90.4066),
    ("Badda", 23.7800, 90.4260), ("Tejgaon", 23.7600, 90.3950),
    ("Farmgate / Mohakhali", 23.7500, 90.3900), ("Mohammadpur", 23.7600, 90.3580),
    ("Dhanmondi", 23.7461, 90.3742), ("Shahbag / Dhaka University", 23.7330, 90.3950),
    ("Motijheel", 23.7330, 90.4175), ("Old Dhaka / Sadarghat", 23.7080, 90.4070),
    ("Rampura / Banasree", 23.7600, 90.4300), ("Khilgaon / Basabo", 23.7500, 90.4300),
    ("Jatrabari", 23.7100, 90.4350), ("Demra", 23.7208, 90.4900),
    ("Keraniganj", 23.6900, 90.3800), ("Narayanganj", 23.6238, 90.5000),
    ("Purbachal", 23.8400, 90.5200), ("Gazipur", 24.0000, 90.4200),
]

STATUS_COLOR = {"covered": "#2b8a3e", "thin": "#f08c00", "none": "#868e96"}
STATUS_TEXT = {"covered": "covered", "thin": "only a little", "none": "not in this map"}


def _km(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return math.hypot((a[0] - b[0]) * 111.0, (a[1] - b[1]) * 102.0)


def analyse(graph: RoadGraph, node_names: Dict[int, str]) -> dict:
    """Extent, extreme junctions, footprint cells and the status of each area."""
    nodes = graph.nodes
    lats = [c[0] for c in nodes.values()]
    lons = [c[1] for c in nodes.values()]
    box = (min(lats), min(lons), max(lats), max(lons))

    def extreme(key, rev):
        n = sorted(nodes, key=key, reverse=rev)[0]
        return {"id": n, "lat": nodes[n][0], "lon": nodes[n][1],
                "road": node_names.get(n, f"node {n}")}

    extremes = {
        "north": extreme(lambda n: nodes[n][0], True),
        "south": extreme(lambda n: nodes[n][0], False),
        "east": extreme(lambda n: nodes[n][1], True),
        "west": extreme(lambda n: nodes[n][1], False),
    }

    cells: Counter = Counter()
    for la, lo in nodes.values():
        cells[(math.floor(la / CELL_DEG), math.floor(lo / CELL_DEG))] += 1

    areas = []
    for name, la, lo in AREAS:
        n = sum(1 for c in nodes.values() if _km(c, (la, lo)) <= RADIUS_KM)
        status = "covered" if n >= COVERED_MIN else ("thin" if n >= THIN_MIN else "none")
        areas.append({"name": name, "lat": la, "lon": lo, "junctions": n, "status": status})
    return {
        "box": box,
        "width_km": (box[3] - box[1]) * 102.0,
        "height_km": (box[2] - box[0]) * 111.0,
        "extremes": extremes,
        "cells": cells,
        "areas": areas,
        "n_nodes": len(nodes),
    }


def build_html(graph: RoadGraph, info: dict, offline: bool = False,
               height: int = 540) -> str:
    """The coverage map as a standalone page."""
    import folium

    min_la, min_lo, max_la, max_lo = info["box"]
    m = viz.base_map(graph, offline=offline, zoom=12, height=f"{height}px")
    pad = 0.02
    m.fit_bounds([[min_la - pad, min_lo - pad], [max_la + pad, max_lo + pad]])

    # footprint: shaded cells that contain road junctions
    peak = max(info["cells"].values()) if info["cells"] else 1
    foot = folium.FeatureGroup(name="road footprint")
    for (i, j), n in info["cells"].items():
        s, w = i * CELL_DEG, j * CELL_DEG
        folium.Rectangle([[s, w], [s + CELL_DEG, w + CELL_DEG]], stroke=False, fill=True,
                         fill_color="#1c7ed6", fill_opacity=0.12 + 0.38 * min(1.0, n / peak),
                         interactive=False).add_to(foot)
    foot.add_to(m)

    folium.Rectangle([[min_la, min_lo], [max_la, max_lo]], color="#c92a2a", weight=2,
                     dash_array="8 6", fill=False, interactive=False).add_to(m)

    for a in info["areas"]:
        folium.CircleMarker(
            [a["lat"], a["lon"]], radius=7, color="#ffffff", weight=2, fill=True,
            fill_color=STATUS_COLOR[a["status"]], fill_opacity=0.95,
            tooltip=f"{a['name']}: {STATUS_TEXT[a['status']]} "
                    f"({a['junctions']:,} junctions within {RADIUS_KM:g} km)").add_to(m)
        folium.Marker(
            [a["lat"], a["lon"]], icon=folium.DivIcon(
                icon_size=(150, 16), icon_anchor=(-6, 8),
                html=f'<div style="font:11px Arial;color:#222;text-shadow:0 0 3px #fff,'
                     f'0 0 3px #fff;white-space:nowrap">{a["name"]}</div>')).add_to(m)

    arrows = {"north": "⬆ North", "south": "⬇ South", "east": "➡ East", "west": "⬅ West"}
    for k, e in info["extremes"].items():
        folium.Marker([e["lat"], e["lon"]], tooltip=f"{arrows[k]} end: {e['road']}",
                      icon=folium.Icon(color="red", icon="flag")).add_to(m)
    return m.get_root().render()
