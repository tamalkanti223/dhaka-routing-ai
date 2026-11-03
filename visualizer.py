"""
visualizer.py — Folium map building and Plotly comparison charts for the
Streamlit dashboard.

Design choices
--------------
* The full largest component has ~42k directed edges; drawing every one in
  Folium is too heavy for a browser.  ``add_roads`` therefore draws a bounded
  set: closed/incident edges are always shown, plus major roads as context,
  capped by ``max_edges``.  For the small region subgraph everything is drawn.
* Offline fallback: pass ``offline=True`` to build a map with no online tile
  layer (``tiles=None``); road polylines still render, so the map is usable
  without internet.  Online tiles (CartoDB positron) are otherwise used.
* Explored / frontier nodes are down-sampled for responsiveness.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

from graph_data import RoadGraph, Edge
from scenarios import Scenario

# Styling -------------------------------------------------------------------- #
ROUTE_COLORS = {
    "BFS": "#1f77b4", "DFS": "#8c564b", "IDS": "#9467bd",
    "UCS": "#2ca02c", "GBFS": "#ff7f0e", "A*": "#d62728",
}
MAJOR_HIGHWAYS = {"motorway", "motorway_link", "trunk", "trunk_link",
                  "primary", "primary_link", "secondary", "secondary_link"}


def _center(graph: RoadGraph, nodes: Optional[Sequence[int]] = None) -> Tuple[float, float]:
    ns = list(nodes) if nodes else list(graph.nodes.keys())
    if not ns:
        return (23.74, 90.40)
    lat = sum(graph.coord(n)[0] for n in ns) / len(ns)
    lon = sum(graph.coord(n)[1] for n in ns) / len(ns)
    return (lat, lon)


def base_map(graph: RoadGraph, offline: bool = False, zoom: int = 13,
             focus: Optional[Sequence[int]] = None, height: str = "100%"):
    import folium
    center = _center(graph, focus)
    # prefer_canvas draws vector layers on one canvas: much faster than SVG.
    if offline:
        m = folium.Map(location=center, zoom_start=zoom, tiles=None,
                       control_scale=True, prefer_canvas=True, height=height)
        folium.Rectangle(  # subtle backdrop so the blank offline canvas reads
            bounds=[[-90, -180], [90, 180]], color=None, interactive=False,
            fill=True, fill_color="#f2f2f2", fill_opacity=1).add_to(m)
    else:
        # OpenStreetMap tiles need no API key (CartoDB now does). Attribution
        # is required and folium adds it automatically.
        m = folium.Map(location=center, zoom_start=zoom,
                       tiles="OpenStreetMap", control_scale=True,
                       prefer_canvas=True, height=height)
    return m


def _edge_style(e: Edge, attr) -> Tuple[str, float, float]:
    """Return (color, weight, opacity) for a road edge given its attributes."""
    if attr.closed:
        return "#000000", 4, 0.9
    if attr.incident == "flood":
        return "#1f77b4", 3, 0.7
    if attr.incident == "construction":
        return "#7f7f7f", 3, 0.7
    if attr.incident == "accident":
        return "#e377c2", 3, 0.8
    # colour by traffic level
    t = attr.traffic
    if t >= 4.0:
        return "#d62728", 2.5, 0.6
    if t >= 3.0:
        return "#ff7f0e", 2, 0.5
    if t >= 2.0:
        return "#bcbd22", 1.6, 0.45
    return "#2ca02c", 1.2, 0.4


def _simplify(pts, tol: float) -> list:
    """Ramer–Douglas–Peucker on (lat, lon) degrees; keeps both endpoints."""
    if len(pts) < 3:
        return list(pts)
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        i, j = stack.pop()
        (y1, x1), (y2, x2) = pts[i], pts[j]
        dy, dx = y2 - y1, x2 - x1
        norm = (dx * dx + dy * dy) ** 0.5 or 1e-12
        best, idx = -1.0, -1
        for k in range(i + 1, j):
            y0, x0 = pts[k]
            d = abs(dy * (x0 - x1) - dx * (y0 - y1)) / norm
            if d > best:
                best, idx = d, k
        if best > tol:
            keep[idx] = True
            stack.extend(((i, idx), (idx, j)))
    return [p for p, k in zip(pts, keep) if k]


def _line(geometry, tol: float = 0.0) -> List[List[float]]:
    """
    Polyline coordinates rounded to ~1 m.  With ``tol`` > 0 the shape is also
    thinned (display only; routing always uses the full geometry), which makes
    the map much lighter to send and draw.
    """
    pts = _simplify(geometry, tol) if tol > 0 else geometry
    return [[round(la, 5), round(lo, 5)] for la, lo in pts]


DISPLAY_TOL_DEG = 0.00005   # ~5 m: invisible at street zoom, halves the points


def add_roads(m, graph: RoadGraph, scenario: Scenario, max_edges: int = 9000,
              include_minor: bool = True):
    """
    Draw roads coloured by their simulated condition.

    Roads that share a style are sent to the browser as ONE multi-line layer
    (about ten layers in total, not thousands), with no hover tooltips and not
    reacting to the mouse, so panning stays smooth and map clicks always reach
    the map.  Closed and incident roads are always drawn; major roads next;
    minor roads only if ``include_minor`` and the ``max_edges`` budget allows.
    """
    import folium
    priority, major, minor = [], [], []
    for e in graph.edges.values():
        a = scenario.get(e.eid)
        if a.closed or a.incident:
            priority.append(e)
        elif e.highway in MAJOR_HIGHWAYS:
            major.append(e)
        elif include_minor:
            minor.append(e)

    buckets: Dict[Tuple[str, float, float], List[List[List[float]]]] = {}
    drawn = 0
    for group in (priority, major, minor):
        for e in group:
            if drawn >= max_edges:
                break
            color, weight, opacity = _edge_style(e, scenario.get(e.eid))
            buckets.setdefault((color, weight, opacity), []).append(
                _line(e.geometry, DISPLAY_TOL_DEG))
            drawn += 1

    for (color, weight, opacity), lines in buckets.items():
        folium.PolyLine(lines, color=color, weight=weight, opacity=opacity,
                        interactive=False).add_to(m)
    return drawn


def add_route(m, edge_path: List[Edge], color: str = "#d62728",
              label: str = "route", weight: float = 5):
    """Draw one route.  ``label`` is kept for callers; there is no hover text."""
    import folium
    if not edge_path:
        return
    coords: List[List[float]] = []
    for e in edge_path:
        coords.extend(_line(e.geometry))
    folium.PolyLine(coords, color=color, weight=weight, opacity=0.9,
                    interactive=False).add_to(m)


def add_explored(m, graph: RoadGraph, expanded: Sequence[int],
                 frontier: Sequence[int] = (), max_points: int = 1500):
    import folium
    def sample(seq):
        seq = list(seq)
        if len(seq) <= max_points:
            return seq
        step = len(seq) / max_points
        return [seq[int(i * step)] for i in range(max_points)]
    for n in sample(expanded):
        la, lo = graph.coord(n)
        folium.CircleMarker((la, lo), radius=1.6, color="#9e9e9e", fill=True,
                            fill_opacity=0.5, weight=0, interactive=False).add_to(m)
    for n in sample(frontier):
        la, lo = graph.coord(n)
        folium.CircleMarker((la, lo), radius=2.2, color="#17becf", fill=True,
                            fill_opacity=0.7, weight=0, interactive=False).add_to(m)


def add_markers(m, graph: RoadGraph, start: int, goal: int):
    import folium
    folium.Marker(graph.coord(start),
                  icon=folium.Icon(color="green", icon="play")).add_to(m)
    folium.Marker(graph.coord(goal),
                  icon=folium.Icon(color="red", icon="flag")).add_to(m)


LEGEND_HTML = """
<div style="position: fixed; bottom: 24px; left: 24px; z-index: 9999;
     background: #ffffff; color: #222222; padding: 10px 12px;
     border: 1px solid #ccc; border-radius: 6px; font-size: 12px;
     line-height: 1.5; font-family: Arial, sans-serif;
     box-shadow: 0 1px 4px rgba(0,0,0,.2);">
<b>Legend</b><br>
<span style="color:#2ca02c;">&#9644;</span> low traffic&nbsp;
<span style="color:#bcbd22;">&#9644;</span> moderate<br>
<span style="color:#ff7f0e;">&#9644;</span> heavy&nbsp;
<span style="color:#d62728;">&#9644;</span> severe<br>
<span style="color:#1f77b4;">&#9644;</span> flood&nbsp;
<span style="color:#7f7f7f;">&#9644;</span> construction&nbsp;
<span style="color:#e377c2;">&#9644;</span> accident<br>
<span style="color:#000;">&#9644;</span> closed&nbsp;
<span style="color:#9e9e9e;">&#9679;</span> explored&nbsp;
<span style="color:#17becf;">&#9679;</span> frontier<br>
<span style="color:green;">&#9873;</span> start&nbsp;
<span style="color:red;">&#9873;</span> destination
</div>
"""


def add_legend(m):
    import folium
    m.get_root().html.add_child(folium.Element(LEGEND_HTML))


# --------------------------------------------------------------------------- #
# Expansion animation (Plotly, replayed in the browser)
# --------------------------------------------------------------------------- #

def _downsample(seq: List[int], cap: int) -> List[int]:
    if len(seq) <= cap:
        return seq
    step = len(seq) / cap
    return [seq[int(i * step)] for i in range(cap)]


def expansion_animation(graph: RoadGraph, result, frame_ms: int = 120,
                        n_frames: int = 40, max_points: int = 2000,
                        max_bg_edges: int = 6000):
    """
    Replay a finished search from its recorded trace.  Plays entirely in the
    browser (Play / Pause / scrub slider), so it never re-runs the search and
    never touches the measured search time.  Works offline (no map tiles).
    """
    import math as _m
    import plotly.graph_objects as go

    events = result.trace
    nodes_seen = {n for _, n in events} | set(result.node_path)
    if not nodes_seen:
        return None
    lats = [graph.coord(n)[0] for n in nodes_seen]
    lons = [graph.coord(n)[1] for n in nodes_seen]
    pad = 0.003
    box = (min(lats) - pad, min(lons) - pad, max(lats) + pad, max(lons) + pad)

    # Static background: roads inside the explored area.
    bx, by = [], []
    drawn = 0
    for e in graph.edges.values():
        la, lo = graph.coord(e.u)
        if box[0] <= la <= box[2] and box[1] <= lo <= box[3]:
            for (pla, plo) in e.geometry:
                bx.append(plo)
                by.append(pla)
            bx.append(None)
            by.append(None)
            drawn += 1
            if drawn >= max_bg_edges:
                break

    # Snapshots of explored / frontier sets at evenly spaced points in the trace.
    cuts = sorted({max(1, round(len(events) * (i + 1) / n_frames))
                   for i in range(n_frames)}) if events else []
    expanded_order: List[int] = []
    expanded_set: set = set()
    frontier: set = set()
    snaps = []
    pos = 0
    for cut in cuts:
        while pos < cut:
            ev, n = events[pos]
            if ev == "expand":
                if n not in expanded_set:
                    expanded_set.add(n)
                    expanded_order.append(n)
                frontier.discard(n)
            elif n not in expanded_set:
                frontier.add(n)
            pos += 1
        snaps.append((_downsample(list(expanded_order), max_points),
                      _downsample(sorted(frontier), max_points // 2),
                      len(expanded_order)))

    path_x, path_y = [], []
    for e in result.edge_path:
        for (pla, plo) in e.geometry:
            path_x.append(plo)
            path_y.append(pla)

    def pts(ns):
        return ([graph.coord(n)[1] for n in ns], [graph.coord(n)[0] for n in ns])

    def frame_data(snap, final):
        ex, ey = pts(snap[0])
        fx, fy = pts(snap[1])
        return [
            go.Scatter(x=ex, y=ey, mode="markers", name="explored", hoverinfo="skip",
                       marker=dict(size=4, color="#9e9e9e")),
            go.Scatter(x=fx, y=fy, mode="markers", name="frontier", hoverinfo="skip",
                       marker=dict(size=5, color="#17becf")),
            go.Scatter(x=path_x if final else [], y=path_y if final else [],
                       mode="lines", name="final route", hoverinfo="skip",
                       line=dict(width=4, color=ROUTE_COLORS.get(result.algorithm, "#d62728"))),
        ]

    s_la, s_lo = graph.coord(result.node_path[0]) if result.node_path else (None, None)
    g_la, g_lo = graph.coord(result.node_path[-1]) if result.node_path else (None, None)
    background = go.Scatter(x=bx, y=by, mode="lines", name="roads", hoverinfo="skip",
                            line=dict(width=1, color="#d0d0d0"))
    markers = go.Scatter(x=[s_lo, g_lo], y=[s_la, g_la], mode="markers+text",
                         text=["start", "goal"], textposition="top center",
                         name="start / goal", hoverinfo="skip",
                         marker=dict(size=11, color=["green", "red"], symbol="star"))

    empty = ([], [], 0)
    first = snaps[0] if snaps else empty
    frames = []
    for i, snap in enumerate(snaps):
        final = (i == len(snaps) - 1)
        frames.append(go.Frame(data=frame_data(snap, final), traces=[1, 2, 3],
                               name=str(i)))

    fig = go.Figure(data=[background] + frame_data(first, len(snaps) <= 1) + [markers],
                    frames=frames)
    mid_lat = (box[0] + box[2]) / 2
    play_args = dict(frame=dict(duration=frame_ms, redraw=True),
                     transition=dict(duration=0), fromcurrent=True, mode="immediate")
    fig.update_layout(
        height=520, margin=dict(l=10, r=10, t=40, b=10), hovermode=False,
        title=f"{result.algorithm} expansion replay — {result.nodes_expanded:,} expanded",
        xaxis=dict(visible=False),
        yaxis=dict(visible=False, scaleanchor="x",
                   scaleratio=1 / _m.cos(_m.radians(mid_lat))),
        plot_bgcolor="#ffffff", paper_bgcolor="#ffffff", font=dict(color="#222222"),
        legend=dict(orientation="h", y=-0.02),
        updatemenus=[dict(type="buttons", direction="left", x=0, y=1.08,
                          buttons=[dict(label="▶ Play", method="animate",
                                        args=[None, play_args]),
                                   dict(label="⏸ Pause", method="animate",
                                        args=[[None], dict(frame=dict(duration=0, redraw=False),
                                                           mode="immediate")])])],
        sliders=[dict(active=0, y=0, len=0.9, x=0.1,
                      currentvalue=dict(prefix="expanded: "),
                      steps=[dict(label=f"{snap[2]:,}", method="animate",
                                  args=[[str(i)], dict(frame=dict(duration=0, redraw=True),
                                                       mode="immediate")])
                             for i, snap in enumerate(snaps)])],
    )
    return fig


# --------------------------------------------------------------------------- #
# Plotly comparison charts (for the dashboard)
# --------------------------------------------------------------------------- #

def comparison_bar(results: Dict[str, "object"], metric: str, title: str):
    """Build a Plotly bar chart from a dict {algo_name: SearchResult}."""
    import plotly.graph_objects as go
    names, vals, colors = [], [], []
    for name, r in results.items():
        v = getattr(r, metric, None)
        if r.found or metric in ("nodes_expanded", "nodes_generated",
                                 "time_ms", "peak_frontier"):
            names.append(name)
            vals.append(v if v is not None else 0)
            colors.append(ROUTE_COLORS.get(name, "#888"))
    fig = go.Figure(go.Bar(x=names, y=vals, marker_color=colors,
                           text=[f"{v:.2f}" if isinstance(v, float) else v for v in vals],
                           textposition="outside"))
    fig.update_traces(hoverinfo="skip")      # values are printed on the bars
    fig.update_layout(title=title, height=320, margin=dict(l=10, r=10, t=40, b=10),
                      yaxis_title=metric, hovermode=False)
    return fig
