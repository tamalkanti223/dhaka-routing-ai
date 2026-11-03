"""
app.py — Streamlit dashboard for "Intelligent Vehicle Routing in Dhaka".

Run:
    streamlit run app.py -- --osm ~/Downloads/dhaka_central_roads.osm

Features: start/destination selection (map click or place search), map-file and
network choice, scenario + day/night, weight sliders, incident/closure editing,
run a single algorithm or Compare All Six, browser-side expansion replay with
Play/Pause, comparison table + charts, CSV and map export.
"""

from __future__ import annotations

import argparse
import math
import os
import pickle
from typing import Dict, List

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

import config
from graph_data import (RoadGraph, NodeIndex, junction_index, load_graph,
                        place_index, subgraph_bbox)
from scenarios import (build_scenario, manual_scenario, scenario_stats,
                       SCENARIO_NAMES, Scenario, LOCAL_EFFECTS, CLOSING_EFFECTS,
                       apply_local_conditions, with_reverse)
from cost_function import explain_example
from heuristics import verify_heuristic
from search_algorithms import ALGORITHMS, ProblemInstance, run_algorithm, SearchResult
import map_coverage
import path_view
import simulation
import visualizer as viz

st.set_page_config(page_title="Dhaka AI Routing", layout="wide",
                   initial_sidebar_state="expanded")


# --------------------------------------------------------------------------- #
# CLI args (Streamlit passes extras after `--`)
# --------------------------------------------------------------------------- #
def parse_cli() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--osm", default=config.DEFAULT_OSM_FILE)
    ap.add_argument("--no-simplify", action="store_true")
    known, _ = ap.parse_known_args()
    return known


CLI = parse_cli()


def map_file_options() -> List[str]:
    """The --osm file plus the known Dhaka extracts that exist on disk."""
    candidates = [
        os.path.abspath(os.path.expanduser(CLI.osm)),
        os.path.expanduser("~/Downloads/dhaka_central_roads.osm"),
        config.BUNDLED_OSM_FILE,
    ]
    # The same map can exist in two places (Downloads and the bundled data/ copy);
    # list each file name once, preferring the earlier path.
    seen, out = set(), []
    for p in candidates:
        p = os.path.abspath(p)
        name = os.path.basename(p).lower()
        if name not in seen and os.path.exists(p):
            seen.add(name)
            out.append(p)
    return out or [CLI.osm]


# --------------------------------------------------------------------------- #
# Cached heavy resources (keyed by file + options, shared across sessions)
# --------------------------------------------------------------------------- #
@st.cache_resource(show_spinner="Loading & building road graph …")
def get_full_graph(osm_file: str, simplify: bool) -> RoadGraph:
    cfg = config.AppConfig(osm_file=osm_file, simplify=simplify)
    return load_graph(cfg, use_cache=True, verbose=False)


@st.cache_resource(show_spinner="Extracting demo region …")
def get_region_graph(osm_file: str, simplify: bool) -> RoadGraph:
    g = get_full_graph(osm_file, simplify)
    return subgraph_bbox(g, config.DEMO_REGION_BBOX, verbose=False)


@st.cache_resource(show_spinner="Indexing places …")
def get_indexes(osm_file: str, simplify: bool, network: str):
    g = (get_region_graph(osm_file, simplify) if network == REGION
         else get_full_graph(osm_file, simplify))
    return NodeIndex(g), place_index(g), junction_index(g)


@st.cache_resource
def get_coverage(osm_file: str, simplify: bool, network: str) -> dict:
    g = (get_region_graph(osm_file, simplify) if network == REGION
         else get_full_graph(osm_file, simplify))
    return map_coverage.analyse(g, get_node_names(osm_file, simplify, network))


@st.cache_resource
def get_node_names(osm_file: str, simplify: bool, network: str) -> Dict[int, str]:
    """node -> short road label (the junction label without pin and coordinates)."""
    junctions = get_indexes(osm_file, simplify, network)[2]
    return {n: label[2:].split(" — ")[0] for label, n in junctions.items()}


FULL = "Full network (whole map file)"
REGION = "Central demo region (small)"


@st.cache_resource(show_spinner="Drawing roads …", max_entries=6)
def get_base_map_blob(_graph: RoadGraph, _scenario: Scenario, osm_file: str,
                      network: str, scenario_name: str, rev: int,
                      offline: bool) -> bytes:
    """
    The road layer, drawn once and cached as pickled bytes.  Only the arguments
    without a leading underscore form the cache key; ``rev`` changes whenever
    roads are edited.

    st_folium attaches the trip layer (route, markers, explored nodes) to the
    map object it is given, so every rerun must start from a fresh copy of the
    pristine map.  Because the road layer is identical each time, st_folium sees
    an unchanged map: it keeps the user's pan/zoom and only swaps the trip layer.
    """
    m = viz.base_map(_graph, offline=offline, zoom=13 if network == REGION else 12)
    viz.add_roads(m, _graph, _scenario, max_edges=9000,
                  include_minor=(network == REGION))
    viz.add_legend(m)
    return pickle.dumps((m, m.get_bounds()), protocol=pickle.HIGHEST_PROTOCOL)


def load_base_map(blob: bytes):
    """Fresh copy of the cached map, with its bounds precomputed."""
    m, bounds = pickle.loads(blob)
    m.get_bounds = lambda: bounds      # st_folium calls this on every rerun
    return m


def frame_view(nodes) -> None:
    """Centre and zoom the map on the given nodes (used for a new trip)."""
    pts = [graph.coord(n) for n in nodes if n in graph.nodes]
    if not pts:
        st.session_state.view = None
        return
    if len(set(pts)) == 1:
        st.session_state.view = (pts[0], 15)
        return
    lats, lons = [p[0] for p in pts], [p[1] for p in pts]
    clat, clon = (min(lats) + max(lats)) / 2, (min(lons) + max(lons)) / 2
    span_lat = max(max(lats) - min(lats), 1e-4) * 1.5
    span_lon = max(max(lons) - min(lons), 1e-4) * 1.5
    # Degrees visible at zoom z in a ~700 x 560 px map: 984/2^z (lon), 722/2^z (lat)
    z = min(math.log2(984 / span_lon), math.log2(722 / span_lat))
    st.session_state.view = ((clat, clon), int(max(10, min(17, math.floor(z)))))


# --------------------------------------------------------------------------- #
# Session state
# --------------------------------------------------------------------------- #
def init_state():
    ss = st.session_state
    ss.setdefault("start", None)
    ss.setdefault("goal", None)
    ss.setdefault("results", {})          # name -> SearchResult
    ss.setdefault("scenario_obj", None)
    ss.setdefault("scenario_sig", None)   # signature to detect rebuild need
    ss.setdefault("last_click", None)     # last map click already handled
    ss.setdefault("flash", None)          # message to show after a rerun
    ss.setdefault("scenario_rev", 0)      # bumped on every road edit
    ss.setdefault("local_edits", [])      # stretches changed in Edit roads
    ss.setdefault("view", None)           # ((lat, lon), zoom) to frame, or None


init_state()


# --------------------------------------------------------------------------- #
# Sidebar controls
# --------------------------------------------------------------------------- #
st.sidebar.title("🚗 Dhaka AI Routing")
st.sidebar.caption("Classical AI search on a real OpenStreetMap road network")

osm_file = st.sidebar.selectbox(
    "Map file", map_file_options(), format_func=os.path.basename,
    help="Central Dhaka road extract from OpenStreetMap.")
simplify = not CLI.no_simplify
network = st.sidebar.radio(
    "Network", [FULL, REGION],
    help="DFS and IDS are slow on the full network; the small central region "
         "lets them finish.")
graph = (get_region_graph(osm_file, simplify) if network == REGION
         else get_full_graph(osm_file, simplify))
node_index, named_places, junctions = get_indexes(osm_file, simplify, network)
# Named roads and landmarks first, then every single junction.
places = {**named_places, **junctions}

st.sidebar.markdown(
    f"**Graph:** {graph.num_nodes():,} nodes · {graph.num_edges():,} directed edges  \n"
    f"**Searchable:** {len(named_places):,} roads/places + "
    f"{len(junctions):,} junctions (every node)")

scenario_name = st.sidebar.selectbox("Scenario", SCENARIO_NAMES, index=0)
night = st.sidebar.toggle("Night-time (use S_night)",
                          value=(scenario_name == "night"))

st.sidebar.subheader("Cost weights (must sum to 1)")
wT = st.sidebar.slider("Traffic  wT", 0.0, 1.0, 0.5, 0.05)
wC = st.sidebar.slider("Condition wC", 0.0, 1.0, 0.3, 0.05)
wS = st.sidebar.slider("Safety   wS", 0.0, 1.0, 0.2, 0.05)
if wT + wC + wS <= 0:
    st.sidebar.error("At least one weight must be above 0; using the defaults.")
    weights = config.Weights()
else:
    if abs(wT + wC + wS - 1.0) > 1e-9:
        st.sidebar.warning(f"Weights sum to {wT + wC + wS:.2f}; they are "
                           "normalised to 1.")
    weights = config.Weights.normalized(wT, wC, wS)
st.sidebar.caption(f"Effective: wT={weights.traffic:.2f}, wC={weights.condition:.2f}, "
                   f"wS={weights.safety:.2f}")

st.sidebar.subheader("Resource limits")
timeout_s = st.sidebar.number_input("Timeout per search (s)", 1.0, 120.0, 10.0, 1.0)
exp_budget = st.sidebar.number_input("Expansion budget", 1000, 5_000_000,
                                     400_000, 1000)
ids_depth = st.sidebar.number_input("IDS max depth", 5, 2000, 120, 5)
limits = config.Limits(timeout_s=timeout_s, expansion_budget=int(exp_budget),
                       ids_max_depth=int(ids_depth))

cfg = config.AppConfig(osm_file=osm_file, weights=weights, limits=limits)

st.sidebar.subheader("Display")
st.sidebar.slider("Map height (px)", 380, 1000, 540, 20, key="map_height",
                  help="Make the map taller on a big screen, shorter on a small laptop.")


# --------------------------------------------------------------------------- #
# Scenario (frozen snapshot) — rebuild only when inputs change
# --------------------------------------------------------------------------- #
def rebuild_scenario():
    if scenario_name == "manual":
        sc_ = manual_scenario(graph, night=night, seed=cfg.seed)
    else:
        sc_ = build_scenario(graph, scenario_name, seed=cfg.seed)
    st.session_state.scenario_obj = sc_
    st.session_state.local_edits = []         # stretches the user changed
    st.session_state.results = {}
    st.session_state.scenario_rev += 1


scenario_sig = (osm_file, network, scenario_name, cfg.seed)
if st.session_state.scenario_sig != scenario_sig or st.session_state.scenario_obj is None:
    rebuild_scenario()
    st.session_state.scenario_sig = scenario_sig
    st.session_state.view = None
if st.sidebar.button("↺ Reset road edits", help="Discard closures/edits and "
                     "restore the selected scenario's simulated values."):
    rebuild_scenario()
    st.session_state.flash = "Road edits reset."
sc: Scenario = st.session_state.scenario_obj
sc.night = night

# A start/goal from another map file or network may not exist in this graph.
for key in ("start", "goal"):
    if st.session_state[key] is not None and st.session_state[key] not in graph.nodes:
        st.session_state[key] = None
        st.session_state.results = {}


# --------------------------------------------------------------------------- #
# Start / destination selection helpers
# --------------------------------------------------------------------------- #
# Callbacks run before the next script run, so they read the place list from
# session state rather than from a variable of the previous run.
st.session_state.places_map = places


def set_point(which: str, node: int, label: str = "") -> None:
    st.session_state[which] = node
    st.session_state[f"{which}_label"] = label     # what the user picked, if anything
    st.session_state.results = {}
    st.session_state.auto_run = True


def _picked(which: str) -> None:
    label = st.session_state.get(f"pick_{which}")
    node = st.session_state.get("places_map", {}).get(label) if label else None
    if node is None:
        return
    set_point(which, node, label)
    frame_view([n for n in (st.session_state.start, st.session_state.goal)
                if n is not None])


def pick_start_cb() -> None:
    _picked("start")


def pick_goal_cb() -> None:
    _picked("goal")


# --------------------------------------------------------------------------- #
# Header + tabs (compact, so the map fits a laptop screen)
# --------------------------------------------------------------------------- #
st.markdown("""
<style>
  .block-container { padding-top: 2.6rem; padding-bottom: 1rem; }
  section[data-testid="stSidebar"] { width: 270px !important; min-width: 270px !important; }
  .app-title { font-size: 1.35rem; font-weight: 700; margin: 0; line-height: 1.3; }
  .app-sub { font-size: 0.78rem; color: #6b6b6b; margin: 0 0 0.2rem 0; }
  div[data-testid="stTabs"] button p { font-size: 0.95rem; }
</style>
<p class="app-title">🚗 Intelligent Vehicle Routing in Dhaka</p>
<p class="app-sub">Map data © OpenStreetMap contributors (ODbL). Traffic, condition and
safety values are <b>simulated</b>, not live data.</p>
""", unsafe_allow_html=True)
if st.session_state.flash:
    st.success(st.session_state.flash)
    st.session_state.flash = None

tab_map, tab_compare, tab_edit, tab_about = st.tabs(
    ["🗺️ Map & Run", "📊 Compare", "🛠️ Edit roads", "📄 About / validation"])


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
VIEW_MAP = "🗺️ Map"
VIEW_SIM = "🎬 Simulation"
VIEW_PATH = "🧭 Path"
VIEW_COVER = "📍 Coverage"


def build_problem() -> ProblemInstance:
    return ProblemInstance(graph, sc.get, cfg.weights, sc.night, cfg.limits)


def run_selected(names: List[str], show_simulation: bool = False) -> None:
    s, g = st.session_state.start, st.session_state.goal
    if s is None or g is None:
        st.warning("Select a start and a destination first.")
        return
    prob = build_problem()
    results: Dict[str, SearchResult] = {}
    # Say what is running and what has finished, so a slow IDS does not
    # look like a frozen app.
    slow_note = {
        "IDS": f"IDS repeats DFS for every depth limit, so it is the slowest "
               f"(it stops after {cfg.limits.timeout_s:.0f} s at most).",
    }
    prog = st.progress(0.0, text="Running searches …")
    done: List[str] = []
    for i, name in enumerate(names):
        lines = [f"**Running {name}** ({i + 1} of {len(names)}) …"]
        if name in slow_note:
            lines.append(slow_note[name])
        if done:
            lines.append("Finished: " + " · ".join(done))
        prog.progress(i / len(names), text="  \n".join(lines))
        r = results[name] = run_algorithm(name, prob, s, g, record_trace=True)
        done.append(f"{name} {'✓' if r.found else '✗'} {r.time_ms / 1000:.1f} s")
    prog.empty()
    st.session_state.results = results
    best = next((results[n] for n in ("A*", "UCS", "GBFS", "BFS", "DFS", "IDS")
                 if n in results and results[n].found), None)
    frame_view(best.node_path if best else [s, g])
    if show_simulation:
        # Show the searches as soon as they finish.  (Not after map clicks:
        # the map must stay clickable for the next trip.)  One algorithm on
        # its own opens its node-by-node path; several open the simulation.
        found = [n for n in names if results[n].found]
        if len(names) == 1 and found:
            st.session_state.pending_path_algo = found[0]
            st.session_state.pending_view = VIEW_PATH
        else:
            st.session_state.pending_view = VIEW_SIM
            st.session_state.sim_autoplay = True


def show_html(page: str, height: int) -> None:
    """Embed a self-contained page we generated (never user-supplied HTML)."""
    if hasattr(st, "iframe"):                 # st.components.v1.html is being removed
        st.iframe(page, height=height)
    else:
        components.html(page, height=height, scrolling=False)


def node_text(which: str) -> str:
    n = st.session_state[which]
    if n is None:
        return "not set"
    la, lo = graph.coord(n)
    picked = st.session_state.get(f"{which}_label")
    if picked:
        what = picked.split(" — ")[0] if picked.startswith("📍") else picked
        return f"{what} ({la:.5f}, {lo:.5f})"
    road = graph.node_label(n).rsplit(" [", 1)[0]
    if road.startswith("node "):
        return f"map point ({la:.5f}, {lo:.5f})"
    return f"map point at {road} ({la:.5f}, {lo:.5f})"


# Read by the map-click callback, which runs before the next script run.
st.session_state.node_index_obj = node_index


def map_click_cb() -> None:
    """
    Handle a map click BEFORE the rerun starts, so the new marker is drawn in the
    same rerun and no click is lost while the app is busy.
    1st click = start, 2nd = destination, 3rd starts a new trip.
    """
    out = st.session_state.get("main_map") or {}
    click = out.get("last_clicked")
    if not click:
        return
    click_key = (round(click["lat"], 7), round(click["lng"], 7))
    if click_key == st.session_state.last_click:
        return
    st.session_state.last_click = click_key
    nn, dist_km = st.session_state.node_index_obj.nearest(click["lat"], click["lng"])
    if nn is None or dist_km > 0.5:
        st.session_state.map_note = "That click is more than 500 m from any road in this network."
        return
    st.session_state.map_note = None
    had_picks = bool(st.session_state.get("pick_start") or st.session_state.get("pick_goal"))
    if st.session_state.start is None or st.session_state.goal is not None:
        set_point("start", nn)
        st.session_state.goal = None
        st.session_state.goal_label = ""
        st.session_state.pick_start = None
        st.session_state.pick_goal = None
    else:
        set_point("goal", nn)
        st.session_state.pick_goal = None
    # A finished trip (or stale names in the From/To boxes) needs the whole app
    # refreshed; a lone start point only needs the map.
    if st.session_state.goal is not None or had_picks:
        st.session_state.needs_app_rerun = True


st.session_state.setdefault("map_view", VIEW_MAP)
st.session_state.setdefault("map_note", None)
map_height = int(st.session_state.get("map_height", 540))


@st.fragment
def map_area() -> None:
    """
    The map / simulation panel.  It is a fragment: a map click reruns only this
    panel, not the sidebar, the place lists or the other tabs, so it stays fast.
    """
    if st.session_state.pop("needs_app_rerun", False):
        st.rerun(scope="app")
    pending = st.session_state.pop("pending_view", None)
    if pending:
        st.session_state.map_view = pending
    pending_algo = st.session_state.pop("pending_path_algo", None)
    if pending_algo:
        st.session_state.path_algo = pending_algo

    top_l, top_r = st.columns([3, 2])
    with top_l:
        st.markdown(f"🟢 **Start:** {node_text('start')}  \n"
                    f"🔴 **Destination:** {node_text('goal')}")
    with top_r:
        view = st.radio("View", [VIEW_MAP, VIEW_SIM, VIEW_PATH, VIEW_COVER], key="map_view",
                        horizontal=True, label_visibility="collapsed")
    if st.session_state.map_note:
        st.caption("⚠️ " + st.session_state.map_note)

    import folium

    def trip_layer() -> "folium.FeatureGroup":
        fg = folium.FeatureGroup(name="trip")
        for name, res in st.session_state.results.items():
            if res.found:
                viz.add_route(fg, res.edge_path,
                              color=viz.ROUTE_COLORS.get(name, "#333"), weight=5)
        if st.session_state.start is not None:
            folium.Marker(graph.coord(st.session_state.start),
                          icon=folium.Icon(color="green", icon="play")).add_to(fg)
        if st.session_state.goal is not None:
            folium.Marker(graph.coord(st.session_state.goal),
                          icon=folium.Icon(color="red", icon="flag")).add_to(fg)
        return fg

    offline = bool(st.session_state.get("offline_tiles", False))
    results = st.session_state.results

    if view == VIEW_COVER:
        info = get_coverage(osm_file, simplify, network)
        e = info["extremes"]
        covered = [a["name"] for a in info["areas"] if a["status"] == "covered"]
        thin = [a["name"] for a in info["areas"] if a["status"] == "thin"]
        none = [a["name"] for a in info["areas"] if a["status"] == "none"]
        st.markdown(
            f"**This map spans about {info['width_km']:.0f} km (west–east) × "
            f"{info['height_km']:.0f} km (south–north)** — {info['n_nodes']:,} road "
            f"junctions. Northmost: *{e['north']['road']}*; southmost: "
            f"*{e['south']['road']}*; eastmost: *{e['east']['road']}*; westmost: "
            f"*{e['west']['road']}*.")
        cov_key = (osm_file, network, offline, map_height)
        cached = st.session_state.get("cover_cache")
        if cached and cached[0] == cov_key:
            page = cached[1]
        else:
            page = map_coverage.build_html(graph, info, offline, height=map_height)
            st.session_state.cover_cache = (cov_key, page)
        show_html(page, map_height + 4)
        st.markdown(
            "🟢 **Covered:** " + ", ".join(covered) + "\n\n"
            + ("🟠 **Only a little:** " + ", ".join(thin) + "\n\n" if thin else "")
            + ("⚪ **Not in this map:** " + ", ".join(none) if none else ""))
        st.caption("Blue shading = where road junctions exist (darker = more); red dashed "
                   "box = the extent; red flags = the four extreme junctions. Area centres "
                   "are approximate positions used only to count nearby junctions within "
                   f"{map_coverage.RADIUS_KM:g} km, not official boundaries.")
        return

    if view == VIEW_PATH:
        found = [n for n in simulation.ALGO_ORDER if n in results and results[n].found]
        if not found:
            st.info("Run an algorithm that finds a path; its route will be listed "
                    "here node by node.")
            return
        if st.session_state.get("path_algo") not in found:
            st.session_state.path_algo = found[-1]          # A* if it was run
        algo = st.radio("Path of", found, key="path_algo", horizontal=True,
                        label_visibility="collapsed")
        res = results[algo]
        pv_key = (algo, id(res), offline, map_height)
        cached = st.session_state.get("path_cache")
        if cached and cached[0] == pv_key:
            page = cached[1]
        else:
            page = path_view.build_html(
                graph, res, algo, get_node_names(osm_file, simplify, network),
                offline, height=map_height)
            st.session_state.path_cache = (pv_key, page)
        show_html(page, map_height + 4)
        return

    if view == VIEW_SIM:
        if not results or st.session_state.start is None or st.session_state.goal is None:
            st.info("Choose a start and destination and run some algorithms; they "
                    "will explore the map here together.")
        else:
            autoplay = st.session_state.pop("sim_autoplay", False)
            sim_key = (tuple((n, id(r)) for n, r in results.items()), offline, map_height)
            cached = st.session_state.get("sim_cache")
            if cached and cached[0] == sim_key:
                page = cached[1]
            else:
                with st.spinner("Preparing the simulation …"):
                    page = simulation.build_html(graph, results, st.session_state.start,
                                                 st.session_state.goal, offline,
                                                 map_height=map_height)
                st.session_state.sim_cache = (sim_key, page)
            if page:
                if autoplay:
                    page = page.replace("var AUTOPLAY = false;", "var AUTOPLAY = true;", 1)
                show_html(page, simulation.page_height(len(results), map_height))
            else:
                st.info("Nothing to replay (start equals destination).")
            if st.toggle("Offline replay (plain chart, one algorithm, no internet needed)",
                         value=False):
                a1, a2 = st.columns([1, 2])
                with a1:
                    anim_algo = st.selectbox("Algorithm to replay", list(results))
                with a2:
                    frame_ms = st.slider("Speed (ms per frame; lower is faster)",
                                         20, 600, 120, 20)
                res = results[anim_algo]
                replay_key = (anim_algo, frame_ms, id(res))
                cached = st.session_state.get("replay")
                if cached and cached[0] == replay_key:
                    fig = cached[1]
                else:
                    fig = viz.expansion_animation(graph, res, frame_ms=frame_ms)
                    st.session_state.replay = (replay_key, fig)
                if fig is not None:
                    st.plotly_chart(fig, width="stretch")
        return

    base = load_base_map(get_base_map_blob(
        graph, sc, osm_file, network, scenario_name,
        st.session_state.scenario_rev, offline))
    view_pos = st.session_state.view
    try:
        from streamlit_folium import st_folium
    except Exception:  # noqa: BLE001
        st.info("Install `streamlit-folium` for an interactive map.")
        return
    st_folium(base, key="main_map", height=map_height, use_container_width=True,
              feature_group_to_add=trip_layer(),
              center=view_pos[0] if view_pos else None,
              zoom=view_pos[1] if view_pos else None,
              returned_objects=["last_clicked"], on_change=map_click_cb)

    if st.button("Prepare map for download (HTML)"):
        full = viz.base_map(graph, offline=offline,
                            zoom=13 if network == REGION else 12)
        viz.add_roads(full, graph, sc, max_edges=9000,
                      include_minor=(network == REGION))
        trip_layer().add_to(full)
        viz.add_legend(full)
        st.download_button("⬇ Download route_map.html",
                           full.get_root().render().encode("utf-8"),
                           file_name="route_map.html", mime="text/html")


# =========================================================================== #
# TAB: MAP & RUN
# =========================================================================== #
with tab_map:
    left, right = st.columns([2.6, 1])

    with right:
        # A map click or swap clears the boxes so they never show a stale choice.
        for key in st.session_state.pop("clear_picks", ()):
            st.session_state[key] = None
        st.selectbox("From (start)", list(places), index=None, key="pick_start",
                     placeholder="Type to search, e.g. Dhaka University",
                     on_change=pick_start_cb)
        st.selectbox("To (destination)", list(places), index=None, key="pick_goal",
                     placeholder="Type to search, e.g. Motijheel",
                     on_change=pick_goal_cb)
        st.caption("Or click the map: 1st click = start, 2nd = destination, "
                   "3rd starts a new trip.")

        rc1, rc2 = st.columns(2)
        with rc1:
            run_clicked = st.button("▶ Run", type="primary", width="stretch",
                                    help="Run the algorithms selected below")
        with rc2:
            all_clicked = st.button("⚖ All six", width="stretch",
                                    help="Run all six algorithms")
        algo_choice = st.multiselect("Algorithms", list(ALGORITHMS.keys()),
                                     default=["UCS", "A*", "GBFS"])
        auto_on = st.checkbox("Auto-run", value=True,
                              help="Run the selected algorithms as soon as both points are set.")

        pending_auto = st.session_state.pop("auto_run", False)
        names_to_run = None
        if run_clicked:
            names_to_run = algo_choice or list(ALGORITHMS.keys())
        elif all_clicked:
            names_to_run = list(ALGORITHMS.keys())
        elif (pending_auto and auto_on and st.session_state.start is not None
              and st.session_state.goal is not None):
            if st.session_state.start == st.session_state.goal:
                st.info("Start and destination are the same place.")
            else:
                names_to_run = algo_choice or list(ALGORITHMS.keys())
        if names_to_run:
            run_selected(names_to_run, show_simulation=run_clicked or all_clicked)

        c1, c2 = st.columns(2)
        with c1:
            if st.button("⇄ Swap", width="stretch",
                         disabled=st.session_state.start is None
                         and st.session_state.goal is None):
                st.session_state.start, st.session_state.goal = (
                    st.session_state.goal, st.session_state.start)
                st.session_state.start_label, st.session_state.goal_label = (
                    st.session_state.get("goal_label"), st.session_state.get("start_label"))
                st.session_state.results = {}
                st.session_state.auto_run = True
                st.session_state.clear_picks = ["pick_start", "pick_goal"]
                st.rerun()
        with c2:
            if st.button("🔄 Reset", width="stretch"):
                st.session_state.start = st.session_state.goal = None
                st.session_state.start_label = st.session_state.goal_label = ""
                st.session_state.results = {}
                st.session_state.view = None
                st.session_state.map_view = VIEW_MAP
                st.session_state.clear_picks = ["pick_start", "pick_goal"]
                st.rerun()

        if st.session_state.results:
            st.markdown("**Results**")
            for name, r in st.session_state.results.items():
                st.caption(f"{name}: " + (f"✅ {r.distance_km:.2f} km, cost "
                                         f"{r.weighted_cost:.2f}, {r.nodes_expanded:,} "
                                         f"expanded, {r.time_ms:.1f} ms"
                                         if r.found else f"⚠️ {r.status.value} "
                                         f"({r.nodes_expanded:,} expanded)"))
        st.toggle("Offline map (no tiles)", key="offline_tiles",
                  help="Draw roads without downloading map tiles.")

    with left:
        map_area()


# =========================================================================== #
# TAB: COMPARE
# =========================================================================== #
with tab_compare:
    st.subheader("Comparison table")
    if not st.session_state.results:
        st.info("Run some algorithms on the Map & Run tab first.")
    else:
        results = st.session_state.results
        ucs = results.get("UCS")
        ucs_cost = ucs.weighted_cost if (ucs and ucs.found) else None
        rows = []
        for name, r in results.items():
            rows.append({
                "algorithm": name,
                "status": r.status.value,
                "distance_km": round(r.distance_km, 3) if r.found else None,
                "weighted_cost": round(r.weighted_cost, 3) if r.found else None,
                "hops": r.hops if r.found else None,
                "nodes_expanded": r.nodes_expanded,
                "unique_expanded": r.unique_states_expanded,
                "nodes_generated": r.nodes_generated,
                "peak_frontier": r.peak_frontier,
                "time_ms": round(r.time_ms, 3),
                "cost_ratio_vs_UCS": (round(r.weighted_cost / ucs_cost, 3)
                                      if (r.found and ucs_cost) else None),
                "limit_hit": r.extra.get("limit", r.extra.get("reason", "")),
            })
        df = pd.DataFrame(rows)
        st.dataframe(df, width="stretch", hide_index=True)

        csv = df.to_csv(index=False).encode("utf-8")
        st.download_button("⬇ Download comparison CSV", csv,
                           file_name="comparison.csv", mime="text/csv")

        st.subheader("Charts")
        st.caption("Use the camera icon on each chart to save it as PNG.")
        cc = st.columns(2)
        with cc[0]:
            st.plotly_chart(viz.comparison_bar(results, "weighted_cost",
                            "Weighted path cost"), width="stretch")
            st.plotly_chart(viz.comparison_bar(results, "nodes_expanded",
                            "Nodes expanded"), width="stretch")
        with cc[1]:
            st.plotly_chart(viz.comparison_bar(results, "time_ms",
                            "Search runtime (ms)"), width="stretch")
            st.plotly_chart(viz.comparison_bar(results, "distance_km",
                            "Physical path length (km)"), width="stretch")

        a = results.get("A*")
        if ucs and a and ucs.found and a.found:
            agree = abs(ucs.weighted_cost - a.weighted_cost) < 1e-6
            st.success(f"UCS and A* optimal-cost agreement: {agree} "
                       f"(UCS={ucs.weighted_cost:.4f}, A*={a.weighted_cost:.4f})")


# =========================================================================== #
# TAB: EDIT ROADS
# =========================================================================== #
with tab_edit:
    st.subheader("Edit road attributes (simulated scenario)")
    st.caption("Close a road or change its traffic/condition/safety, then re-run "
               "to see routes replan. A closed road is excluded by all six algorithms. "
               "Use 'Reset road edits' in the sidebar to undo.")

    candidate_edges = []
    if st.session_state.results:
        for name in ("A*", "UCS"):
            r = st.session_state.results.get(name)
            if r and r.found:
                candidate_edges = r.edge_path
                break
    source = st.radio("Pick edge from", ["Part of current route", "Current route",
                                         "Search by road name"],
                      horizontal=True)
    chosen = None
    if source == "Part of current route":
        if candidate_edges:
            # Pick any stretch of the route (default: all of it) by distance
            # from the start; segments are labelled with their road names.
            cum, pos = [], 0.0
            for e in candidate_edges:
                cum.append(pos)
                pos += e.length_km
            marks = [f"{i + 1}. {e.name if not e.name.startswith('(') else 'lane'} "
                     f"@ {cum[i]:.2f} km" for i, e in enumerate(candidate_edges)]
            lo_m, hi_m = st.select_slider(
                "Part of the route to change (start → destination)", options=marks,
                value=(marks[0], marks[-1]), key="route_range")
            lo, hi = marks.index(lo_m), marks.index(hi_m)
            part = candidate_edges[lo:hi + 1]
            km = sum(e.length_km for e in part)
            attrs = [sc.get(e.eid) for e in part]
            avg = lambda f: sum(getattr(a, f) for a in attrs) / len(attrs)
            roads = sorted({e.name for e in part if not e.name.startswith("(")})
            st.write(f"**Selected:** segments {lo + 1}–{hi + 1} of "
                     f"{len(candidate_edges)}, {km:.2f} km"
                     + (f" ({', '.join(roads[:4])}{' …' if len(roads) > 4 else ''})"
                        if roads else "")
                     + ". The values below are set on every selected segment "
                       "(current averages shown).")

            # ---- Conditions: add or remove each one on this part only ----
            st.markdown("**Conditions on this part** (tick to add, untick to "
                        "remove; applied in both directions of the road)")
            effects = st.pills("Conditions", list(LOCAL_EFFECTS),
                               format_func=LOCAL_EFFECTS.get,
                               selection_mode="multi", key="route_effects",
                               label_visibility="collapsed") or []
            closes = CLOSING_EFFECTS & set(effects)
            st.caption("Values = this road's normal-day values + the ticked "
                       "conditions. Nothing ticked = plain normal day."
                       + (" ⛔ The part will be **closed**: every algorithm "
                          "must go around it." if closes else ""))
            if st.button("Apply conditions to this part", type="primary"):
                both = with_reverse(graph, part)
                apply_local_conditions(sc, both, set(effects), seed=cfg.seed)
                st.session_state.local_edits.append({
                    "label": (f"segments {lo + 1}–{hi + 1}, {km:.2f} km"
                              + (f" ({', '.join(roads[:2])})" if roads else "")),
                    "eids": [e.eid for e in both],
                    "effects": list(effects),
                })
                st.session_state.scenario_rev += 1
                st.session_state.results = {}
                what = ", ".join(LOCAL_EFFECTS[x] for x in effects) or "normal day"
                st.session_state.flash = (f"{what} → {len(part)} segment(s), "
                                          f"{km:.2f} km. Press ▶ Run to see the new route.")
                st.rerun()

            st.markdown("**Or set the numbers by hand**")
            w1, w2 = st.columns(2)
            with w1:
                rt = st.slider("Traffic T (1 = free, 5 = jam)", 1.0, 5.0,
                               round(avg("traffic"), 1), 0.1, key="route_t")
                rc = st.slider("Condition C (1 = good, 5 = broken)", 1.0, 5.0,
                               round(avg("condition"), 1), 0.1, key="route_c")
            with w2:
                rsd = st.slider("Safety day (1 = safe, 4 = risky)", 1.0, 4.0,
                                round(avg("safety_day"), 1), 0.1, key="route_sd")
                rsn = st.slider("Safety night (1 = safe, 4 = risky)", 1.0, 4.0,
                                round(avg("safety_night"), 1), 0.1, key="route_sn")
            if st.button("Apply to the selected part"):
                for e in part:
                    sc.set_attr(e.eid, traffic=rt, condition=rc,
                                safety_day=rsd, safety_night=rsn)
                st.session_state.scenario_rev += 1
                st.session_state.results = {}
                st.session_state.flash = (
                    f"Applied to {len(part)} segment(s), {km:.2f} km of the route. "
                    f"Run again: if the route got worse, the search may now pick "
                    f"another way.")
                st.rerun()
        else:
            st.info("Run UCS or A* to a destination first.")

        # Every stretch changed with conditions stays listed here, even after
        # the route moves away from it, so it can be removed again.
        edits = st.session_state.get("local_edits") or []
        if edits:
            st.markdown("**Your changed stretches**")
            for i, ed in enumerate(edits):
                c1, c2 = st.columns([4, 1])
                with c1:
                    what = ", ".join(LOCAL_EFFECTS[x] for x in ed["effects"]) or "normal day"
                    st.write(f"{i + 1}. {ed['label']}: {what}")
                with c2:
                    if st.button("Remove", key=f"rm_edit_{i}"):
                        with st.spinner("Restoring the scenario's values …"):
                            orig = (manual_scenario(graph, night=night, seed=cfg.seed)
                                    if scenario_name == "manual" else
                                    build_scenario(graph, scenario_name, seed=cfg.seed))
                        for eid in ed["eids"]:
                            sc.attrs[eid] = orig.attrs[eid]
                        edits.pop(i)
                        # Re-apply the remaining edits in order, in case
                        # one of them overlaps the stretch just restored.
                        for other in edits:
                            apply_local_conditions(
                                sc, [graph.edges[x] for x in other["eids"]],
                                set(other["effects"]), seed=cfg.seed)
                        st.session_state.scenario_rev += 1
                        st.session_state.results = {}
                        st.session_state.flash = (f"Removed. Those roads are back to "
                                                  f"the '{scenario_name}' values. "
                                                  f"Press ▶ Run again.")
                        st.rerun()
    elif source == "Current route":
        if candidate_edges:
            opts = {f"{e.name} [{e.u}->{e.v}] {e.length_km:.3f} km (eid {e.eid})": e
                    for e in candidate_edges}
            key = st.selectbox("Route edge", list(opts))
            chosen = opts.get(key)
        else:
            st.info("Run UCS or A* to a destination first, or search by road name.")
    else:
        q = st.text_input("Road name contains", "")
        matches = [e for e in graph.edges.values()
                   if q and q.lower() in e.name.lower()][:200]
        if matches:
            opts = {f"{e.name} [{e.u}->{e.v}] (eid {e.eid})": e for e in matches}
            key = st.selectbox("Matching edge", list(opts))
            chosen = opts.get(key)
        elif q:
            st.info("No matching named roads.")

    if chosen is not None:
        a = sc.get(chosen.eid)
        st.write(f"**{chosen.name}** · {chosen.highway} · {chosen.length_km:.3f} km")
        e1, e2, e3 = st.columns(3)
        with e1:
            new_t = st.slider("Traffic T", 1.0, 5.0, float(a.traffic), 0.1,
                              key=f"t_{chosen.eid}")
            new_c = st.slider("Condition C", 1.0, 5.0, float(a.condition), 0.1,
                              key=f"c_{chosen.eid}")
        with e2:
            new_sd = st.slider("Safety day", 1.0, 4.0, float(a.safety_day), 0.1,
                               key=f"sd_{chosen.eid}")
            new_sn = st.slider("Safety night", 1.0, 4.0, float(a.safety_night), 0.1,
                               key=f"sn_{chosen.eid}")
        with e3:
            new_closed = st.checkbox("Closed", value=a.closed, key=f"cl_{chosen.eid}")
            new_inc = st.selectbox("Incident", ["", "flood", "construction",
                                                "accident", "other"],
                                   index=(["", "flood", "construction", "accident",
                                           "other"].index(a.incident)
                                          if a.incident in ("", "flood", "construction",
                                                            "accident", "other")
                                          else 4),
                                   key=f"inc_{chosen.eid}")
        if st.button("Apply to this edge"):
            sc.set_attr(chosen.eid, traffic=new_t, condition=new_c,
                        safety_day=new_sd, safety_night=new_sn,
                        closed=new_closed, incident=new_inc)
            st.session_state.scenario_rev += 1
            st.session_state.results = {}
            st.session_state.flash = "Applied. Run the searches again to see the effect."
            st.rerun()
        if st.button("Close this road (both directions)"):
            n = sc.close_road_both_directions(graph, chosen.u, chosen.v)
            st.session_state.scenario_rev += 1
            st.session_state.results = {}
            st.session_state.flash = f"Closed {n} directed edge(s). Run again to replan."
            st.rerun()

    st.divider()
    st.subheader("Incident → replan from current position (demo)")
    if candidate_edges:
        # Use the route edge nearest the middle whose start junction has another
        # open exit; blocking the only exit of a one-way road just traps the car.
        half = len(candidate_edges) // 2
        by_closeness = sorted(range(len(candidate_edges)), key=lambda i: abs(i - half))
        prob_now = build_problem()
        mid = next((candidate_edges[i] for i in by_closeness
                    if len(prob_now.successors(candidate_edges[i].u)) >= 2),
                   candidate_edges[half])
        st.write(f"The vehicle is assumed to be at junction **{mid.u}**, about halfway "
                 f"along the current route, and the road ahead "
                 f"(**{mid.name}**) gets blocked.")
        if st.button("Block the road ahead and replan from here"):
            sc.set_closed(mid.eid, True)
            st.session_state.scenario_rev += 1
            st.session_state.start = mid.u
            run_selected(["UCS", "A*"], show_simulation=True)
            st.session_state.flash = ("Blocked the road ahead and replanned UCS and "
                                      "A* from the vehicle's current position.")
            st.rerun()
    else:
        st.info("Run UCS or A* to a destination first.")

    st.divider()
    st.write("Scenario snapshot stats:")
    st.json(scenario_stats(graph, sc))


# =========================================================================== #
# TAB: ABOUT / VALIDATION
# =========================================================================== #
with tab_about:
    st.subheader("Cost function")
    st.code(explain_example(cfg.weights), language="text")
    st.subheader("Heuristic validation (on the active graph)")
    if st.session_state.goal is None:
        st.info("Select a destination to validate the heuristic toward it.")
    elif st.button("Check heuristic admissibility & consistency"):
        with st.spinner("Checking every edge …"):
            rep = verify_heuristic(graph, st.session_state.goal)
        st.code(rep.summary(), language="text")
    st.subheader("Graph provenance & stats")
    st.json({k: v for k, v in graph.meta.items()
             if k not in ("named_points", "alt_names")})
    st.caption("Source: local OSM XML pulled from OpenStreetMap via the Overpass API.")
