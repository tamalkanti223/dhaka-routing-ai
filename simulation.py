"""
simulation.py — replay finished searches on the real map, all algorithms at once.

The HTML page built here is self-contained.  It replays the expansion events
that were RECORDED while each search ran, so playing, pausing or scrubbing never
re-runs a search and cannot affect any measured search time.

What it shows
-------------
* Every algorithm that was run, each in its own colour, exploring the road
  network step by step.  The most recently expanded node of each is drawn
  larger, so you can see where the search is heading.
* "Race" sync: every algorithm advances the same number of expansions per step,
  so cheap searches (GBFS, A*) visibly finish before expensive ones (UCS, IDS).
  "Normalized" sync: every algorithm finishes at the same moment, which makes
  the *shape* of each search easier to compare.
* A finished algorithm draws its final route; one that hit a resource limit or
  found no path says so instead.  Nothing is shown as a success unless it was.
* A checkbox and an "only" button per algorithm, to watch one on its own.
* The view opens on the trip; "Zoom to everything explored" shows wanderers such
  as DFS.

Notes
-----
Each algorithm's explored nodes are the nodes in the order first expanded, thinned
evenly if there are very many; the step counts shown are the true expansion
counts.  IDS re-expands nodes in every iteration; repeats add no new dot but do
count as steps.  Frontier nodes are not drawn.  Map tiles and the Leaflet script
come from the internet; the plain-chart replay in the dashboard works offline.
"""

from __future__ import annotations

import json
from typing import Dict, List, Optional

import folium

import visualizer as viz
from graph_data import RoadGraph
from search_algorithms import SearchResult

ALGO_ORDER = ["BFS", "DFS", "IDS", "UCS", "GBFS", "A*"]
MAP_HEIGHT_PX = 560            # default total height of the map area in the app
CONTROLS_PX = 90               # play bar + scrub + ranking line, below the map


def _prep_algo(graph: RoadGraph, name: str, r: SearchResult, cap: int) -> dict:
    """Explored points [lat, lon, step] in first-expanded order, plus the route."""
    seen = set()
    pts: List[list] = []
    step = 0
    for ev, n in r.trace:
        if ev != "expand":
            continue
        step += 1
        if n in seen:
            continue
        seen.add(n)
        la, lo = graph.coord(n)
        pts.append([round(la, 5), round(lo, 5), step])
    if len(pts) > cap:
        stride = len(pts) / cap
        pts = [pts[int(i * stride)] for i in range(cap)]
    route: List[list] = []
    for e in r.edge_path:
        route.extend([round(la, 5), round(lo, 5)] for la, lo in e.geometry)
    return {
        "name": name,
        "color": viz.ROUTE_COLORS.get(name, "#333333"),
        "status": r.status.value,
        "found": bool(r.found),
        "total": int(r.nodes_expanded),
        "dist": round(r.distance_km, 3) if r.found else None,
        "cost": round(r.weighted_cost, 3) if r.found else None,
        "capped": bool(r.extra.get("trace_truncated")),
        "pts": pts,
        "route": route,
    }


def _add_context_roads(m: folium.Map, graph: RoadGraph, box) -> None:
    """Faint grey roads (all road classes) inside ``box``, as one light layer."""
    min_lat, min_lon, max_lat, max_lon = box
    lines = []
    for e in graph.edges.values():
        la, lo = graph.coord(e.u)
        if min_lat <= la <= max_lat and min_lon <= lo <= max_lon:
            lines.append([[round(a, 5), round(b, 5)] for a, b in e.geometry])
            if len(lines) >= 16000:
                break
    if lines:
        folium.PolyLine(lines, color="#8a8f98", weight=1, opacity=0.55,
                        interactive=False).add_to(m)


def build_html(graph: RoadGraph, results: Dict[str, SearchResult], start: int,
               goal: int, offline: bool = False, cap: int = 2500,
               map_height: int = MAP_HEIGHT_PX) -> Optional[str]:
    """Return the simulation page as HTML, or None if there is nothing to replay."""
    algos = [_prep_algo(graph, n, results[n], cap) for n in ALGO_ORDER
             if n in results and results[n].trace]
    if not algos:
        return None

    lats = [p[0] for a in algos for p in a["pts"]] + [graph.coord(start)[0], graph.coord(goal)[0]]
    lons = [p[1] for a in algos for p in a["pts"]] + [graph.coord(start)[1], graph.coord(goal)[1]]
    pad = 0.004
    box = (min(lats) - pad, min(lons) - pad, max(lats) + pad, max(lons) + pad)

    # The trip itself, with generous margins.
    # Frame it with the shortest route found: a wandering route (DFS can return
    # hundreds of km) must not zoom the view out.
    found = [a for a in algos if a["found"] and a["route"]]
    best = min(found, key=lambda a: a["dist"]) if found else None
    trip_pts = [graph.coord(start), graph.coord(goal)] + (
        [(pt[0], pt[1]) for pt in best["route"]] if best else
        [(pt[0], pt[1]) for a in algos for pt in a["pts"][:300]])
    t_lat = [p[0] for p in trip_pts]
    t_lon = [p[1] for p in trip_pts]
    margin_lat = max(0.5 * (max(t_lat) - min(t_lat)), 0.004)
    margin_lon = max(0.5 * (max(t_lon) - min(t_lon)), 0.004)
    trip_box = (min(t_lat) - margin_lat, min(t_lon) - margin_lon,
                max(t_lat) + margin_lat, max(t_lon) + margin_lon)

    # The map shrinks by the controls' height so the whole page matches the
    # map size the user chose in the sidebar.
    sim_map_px = max(300, map_height - CONTROLS_PX)
    m = viz.base_map(graph, offline=offline, zoom=14, height=f"{sim_map_px}px",
                     focus=[start, goal])
    _add_context_roads(m, graph, trip_box)
    html = m.get_root().render()

    data = {
        "algos": algos,
        "start": list(graph.coord(start)),
        "goal": list(graph.coord(goal)),
        "bounds": [[box[0], box[1]], [box[2], box[3]]],
        "trip_bounds": [[trip_box[0], trip_box[1]], [trip_box[2], trip_box[3]]],
    }
    page = _CONTROLS_HTML + "<script>\n" + _SCRIPT.replace(
        "/*__DATA__*/null", json.dumps(data, separators=(",", ":"))) + "\n</script>\n"
    # Folium creates the map in a script placed AFTER </body>, so our code must go
    # at the very end of the page, after the map exists.
    head, tail_marker, tail = html.rpartition("</html>")
    return head + page + tail_marker + tail


def page_height(n_algos: int, map_height: int = MAP_HEIGHT_PX) -> int:
    """Iframe height: map + controls + algorithm chips (two per line)."""
    return max(300, map_height - CONTROLS_PX) + CONTROLS_PX + 30 * ((n_algos + 1) // 2) + 24


_CONTROLS_HTML = """
<style>
  body { margin: 0; }
  #sim-ui { font-family: Arial, sans-serif; font-size: 12.5px; color: #222; background: #fff;
            padding: 6px 4px 2px 4px; }
  #sim-ui button { font-size: 12.5px; padding: 3px 9px; cursor: pointer;
                   border: 1px solid #bbb; border-radius: 4px; background: #f6f6f6; color: #222; }
  #sim-ui button:hover { background: #ececec; }
  #sim-ui .ctl { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 8px; margin-bottom: 2px; }
  #sim-ui select, #sim-ui input[type=range] { vertical-align: middle; }
  #s-scrub { width: 100%; margin: 2px 0 2px 0; }
  #s-algos { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 2px 14px; }
  .algo-row { display: flex; align-items: center; gap: 6px; padding: 1px 0; white-space: nowrap;
              overflow: hidden; text-overflow: ellipsis; }
  .algo-row .sw { display: inline-block; width: 14px; height: 14px; border-radius: 50%; flex: none; }
  .algo-row .nm { width: 48px; font-weight: bold; flex: none; }
  .algo-row .only { padding: 1px 7px; font-size: 11px; flex: none; }
  .algo-row .st { color: #444; }
  .algo-row.done .st { color: #1b5e20; }
  .algo-row.fail .st { color: #b71c1c; }
  .pin { width: 26px; height: 26px; border-radius: 50%; color: #fff; font: bold 14px/26px Arial;
         text-align: center; border: 2px solid #fff; box-shadow: 0 1px 4px rgba(0,0,0,.5); }
  #s-rank { color: #444; margin: 0 0 3px 0; }
</style>
<div id="sim-ui">
  <div class="ctl">
    <button id="s-play">&#9654; Play</button>
    <button id="s-restart">&#10226; Restart</button>
    <label>Speed <input id="s-speed" type="range" min="1" max="40" value="10"></label>
    <select id="s-mode" title="Race: same expansions per step. Normalized: all finish together.">
      <option value="race">Race (same steps)</option>
      <option value="norm">Normalized (finish together)</option>
    </select>
    <button id="s-all">Show all</button>
    <button id="s-fit-trip">Zoom: trip</button>
    <button id="s-fit-all">Zoom: all explored</button>
    <span id="s-clock" style="color:#444"></span>
  </div>
  <input id="s-scrub" type="range" min="0" max="1000" value="0">
  <div id="s-rank"></div>
  <div id="s-algos"></div>
</div>
"""

_SCRIPT = r"""
(function () {
  var DATA = /*__DATA__*/null;
  var AUTOPLAY = false;
  var mapName = Object.keys(window).filter(function (k) { return /^map_/.test(k); })[0];
  var map = window[mapName];
  var ALGOS = DATA.algos;
  var canvas = L.canvas({ padding: 0.5 });
  function $(id) { return document.getElementById(id); }
  function fmt(x) { return Math.round(x).toLocaleString(); }

  // Race length: until the slowest algorithm that actually finds a route is done.
  var finishers = ALGOS.filter(function (a) { return a.found; }).map(function (a) { return a.total; });
  var TMAX = Math.max.apply(null, (finishers.length ? finishers : ALGOS.map(function (a) { return a.total; })).concat([1]));

  var f = 0, playing = false, last = null, mode = "race";

  // start / destination pins
  function pin(ll, text, color) {
    L.marker(ll, { interactive: false, keyboard: false,
      icon: L.divIcon({ className: "", iconSize: [26, 26], iconAnchor: [13, 13],
        html: '<div class="pin" style="background:' + color + '">' + text + '</div>' }) }).addTo(map);
  }
  pin(DATA.start, "S", "#2e7d32");
  pin(DATA.goal, "D", "#c62828");
  // The page may load while its Streamlit tab is hidden (zero size), where a
  // fit would pick a nonsense zoom.  Frame the trip once the map is really visible.
  var fitted = false;
  function fitOnce() {
    var sz = map.getSize();
    if (!fitted && sz.x > 50 && sz.y > 50) {
      map.invalidateSize();
      map.fitBounds(DATA.trip_bounds, { padding: [10, 10] });
      fitted = true;
    }
  }
  fitOnce();
  if (window.ResizeObserver) {
    new ResizeObserver(function () { map.invalidateSize(); fitOnce(); }).observe(map.getContainer());
  }

  // per-algorithm layers and rows
  ALGOS.forEach(function (a) {
    a.group = L.layerGroup().addTo(map);
    a.markers = [];
    a.head = L.circleMarker([0, 0], { renderer: canvas, radius: 7, color: "#fff", weight: 2,
      fillColor: a.color, fillOpacity: 1, interactive: false });
    a.line = a.route.length ? L.polyline(a.route, { color: a.color, weight: 5, opacity: 0.95,
      interactive: false }) : null;
    a.visible = true;
    var row = document.createElement("div");
    row.className = "algo-row";
    row.innerHTML = '<input type="checkbox" checked data-a="' + a.name + '">' +
      '<span class="sw" style="background:' + a.color + '"></span>' +
      '<span class="nm">' + a.name + '</span>' +
      '<button class="only" data-only="' + a.name + '">only</button>' +
      '<span class="st"></span>';
    $("s-algos").appendChild(row);
    a.row = row;
    a.stEl = row.querySelector(".st");
    a.cb = row.querySelector("input");
  });

  function setVisible(a, on) {
    a.visible = on;
    a.cb.checked = on;
    if (on) { a.group.addTo(map); } else { map.removeLayer(a.group); }
  }

  function stepsOf(a) { return mode === "race" ? f * TMAX : f * a.total; }

  function render() {
    var order = [];
    ALGOS.forEach(function (a) {
      var s = stepsOf(a);
      // how many explored points have step <= s (binary search)
      var lo = 0, hi = a.pts.length;
      while (lo < hi) { var mid = (lo + hi) >> 1; if (a.pts[mid][2] <= s) { lo = mid + 1; } else { hi = mid; } }
      while (a.markers.length < lo) {
        var p = a.pts[a.markers.length];
        var mk = L.circleMarker([p[0], p[1]], { renderer: canvas, radius: 2.6, stroke: false,
          fillColor: a.color, fillOpacity: 0.6, interactive: false });
        a.group.addLayer(mk);
        a.markers.push(mk);
      }
      while (a.markers.length > lo) { a.group.removeLayer(a.markers.pop()); }
      if (lo > 0) {
        var q = a.pts[lo - 1];
        a.head.setLatLng([q[0], q[1]]);
        if (!a.group.hasLayer(a.head)) { a.group.addLayer(a.head); }
      } else if (a.group.hasLayer(a.head)) { a.group.removeLayer(a.head); }

      var done = s >= a.total - 1e-9;
      if (a.line) {
        if (done && a.found) { if (!a.group.hasLayer(a.line)) { a.group.addLayer(a.line); } }
        else if (a.group.hasLayer(a.line)) { a.group.removeLayer(a.line); }
      }
      var txt;
      a.row.className = "algo-row";
      if (done && a.found) {
        txt = "found " + a.dist.toFixed(2) + " km, cost " + a.cost.toFixed(2) + " (" + fmt(a.total) + " expanded)";
        a.row.className = "algo-row done";
      } else if (done) {
        txt = (a.status === "resource_limit" ? "stopped at its resource limit" : "no path found") +
              " (" + fmt(a.total) + " expanded)";
        a.row.className = "algo-row fail";
      } else {
        txt = "searching... " + fmt(Math.min(s, a.total)) + " / " + fmt(a.total) + " expanded";
      }
      if (a.capped) { txt += " [recording capped]"; }
      a.stEl.textContent = txt;
    });
    $("s-clock").textContent = mode === "race"
      ? "step " + fmt(f * TMAX) + " / " + fmt(TMAX)
      : Math.round(f * 100) + "% of each search";
    var fin = ALGOS.filter(function (a) { return a.found; })
      .sort(function (x, y) { return x.total - y.total; })
      .map(function (a) { return a.name + " (" + fmt(a.total) + ")"; });
    $("s-rank").textContent = fin.length
      ? "Fewest expansions first: " + fin.join(" < ") : "No algorithm found a route.";
  }

  function tick(ts) {
    if (!playing) { return; }
    if (last === null) { last = ts; }
    var dt = (ts - last) / 1000; last = ts;
    var dur = 150 / Number($("s-speed").value);      // seconds for the whole run
    f = Math.min(1, f + dt / dur);
    $("s-scrub").value = Math.round(f * 1000);
    render();
    if (f >= 1) { playing = false; $("s-play").innerHTML = "&#9654; Replay"; return; }
    requestAnimationFrame(tick);
  }

  $("s-play").onclick = function () {
    if (playing) { playing = false; $("s-play").innerHTML = "&#9654; Play"; return; }
    if (f >= 1) { f = 0; }
    playing = true; last = null; $("s-play").innerHTML = "&#10074;&#10074; Pause";
    requestAnimationFrame(tick);
  };
  $("s-restart").onclick = function () {
    f = 0; $("s-scrub").value = 0; render();
    if (!playing) { $("s-play").innerHTML = "&#9654; Play"; }
  };
  $("s-scrub").oninput = function () {
    playing = false; $("s-play").innerHTML = "&#9654; Play";
    f = Number(this.value) / 1000; render();
  };
  $("s-mode").onchange = function () { mode = this.value; render(); };
  $("s-fit-trip").onclick = function () { map.fitBounds(DATA.trip_bounds, { padding: [10, 10] }); };
  $("s-fit-all").onclick = function () { map.fitBounds(DATA.bounds, { padding: [10, 10] }); };
  $("s-all").onclick = function () { ALGOS.forEach(function (a) { setVisible(a, true); }); };
  $("s-algos").onclick = function (ev) {
    var t = ev.target;
    if (t.dataset && t.dataset.only) {
      ALGOS.forEach(function (a) { setVisible(a, a.name === t.dataset.only); });
    } else if (t.dataset && t.dataset.a) {
      var a = ALGOS.filter(function (x) { return x.name === t.dataset.a; })[0];
      setVisible(a, t.checked);
    }
  };
  render();
  if (AUTOPLAY) { setTimeout(function () { $("s-play").click(); }, 400); }
})();
"""
