"""
path_view.py — one algorithm's final path on the real map, node by node.

The page built here shows the whole route of a finished search and, beside the
map, every node of that route in order (start first, destination last) with
the road it continues on and the distance from the start.  Pointing at a node
in the list marks it on the map (and pans there if it is out of view); pointing
at a node on the map highlights it in the list.  Clicking a list entry zooms in.

Nothing is re-run: the page only displays the stored result.  Map tiles and the
Leaflet script come from the internet, like the main map.
"""

from __future__ import annotations

import json
from typing import Dict, Optional

import visualizer as viz
from graph_data import RoadGraph
from search_algorithms import SearchResult

LIST_PX = 235                  # width of the node list beside the map


def build_html(graph: RoadGraph, result: SearchResult, algo: str,
               node_names: Dict[int, str], offline: bool = False,
               height: int = 540) -> Optional[str]:
    """Return the path page as HTML, or None if the search found no path."""
    if not result.found or not result.node_path:
        return None
    path = result.node_path
    nodes, km = [], 0.0
    for i, n in enumerate(path):
        la, lo = graph.coord(n)
        nxt = result.edge_path[i] if i < len(result.edge_path) else None
        road = None
        if nxt is not None:
            road = "unnamed lane" if nxt.name.startswith("(") else nxt.name
        nodes.append({"id": n, "lat": round(la, 6), "lon": round(lo, 6),
                      "km": round(km, 3), "name": node_names.get(n, f"node {n}"),
                      "next": road})
        if nxt is not None:
            km += nxt.length_km
    route = [[round(la, 6), round(lo, 6)] for e in result.edge_path
             for la, lo in e.geometry]

    m = viz.base_map(graph, offline=offline, zoom=15, height=f"{height}px",
                     focus=[path[0], path[-1]])
    html = m.get_root().render()
    data = {
        "map": m.get_name(),
        "algo": algo,
        "color": viz.ROUTE_COLORS.get(algo, "#d62728"),
        "nodes": nodes,
        "route": route,
        "km": round(result.distance_km, 3),
        "cost": round(result.weighted_cost, 3),
    }
    page = (_PANEL_HTML.replace("__LIST_PX__", str(LIST_PX))
            .replace("__H__", str(height))
            + "<script>\n" + _SCRIPT.replace(
                "/*__DATA__*/null", json.dumps(data, separators=(",", ":")))
            + "\n</script>\n")
    # Folium creates the map in a script after </body>; ours must run later.
    head, marker, tail = html.rpartition("</html>")
    return head + page + marker + tail


_PANEL_HTML = """
<style>
  body { margin: 0; }
  .folium-map { width: calc(100% - __LIST_PX__px) !important; }
  #pv-list { position: absolute; top: 0; right: 0; width: __LIST_PX__px; height: __H__px;
             overflow-y: auto; font-family: Arial, sans-serif; font-size: 12px;
             color: #222; background: #fff; border-left: 1px solid #ccc;
             box-sizing: border-box; }
  #pv-head { position: sticky; top: 0; background: #f6f6f6; padding: 6px 8px;
             border-bottom: 1px solid #ccc; z-index: 2; }
  .pv-row { display: flex; gap: 5px; padding: 3px 6px; border-bottom: 1px solid #eee;
            cursor: pointer; line-height: 1.3; }
  .pv-row:hover, .pv-row.on { background: #fff3c4; }
  .pv-num { min-width: 24px; font-weight: bold; color: #555; }
  .pv-sub { color: #666; font-size: 11.5px; }
  .pv-start .pv-num { color: #1a8f2e; }
  .pv-goal .pv-num { color: #c62828; }
</style>
<div id="pv-list"><div id="pv-head"></div><div id="pv-rows"></div></div>
"""

_SCRIPT = r"""
(function () {
  var D = /*__DATA__*/null;
  var map = window[D.map];
  if (!map) { return; }
  var N = D.nodes, last = N.length - 1;
  map.invalidateSize();

  L.polyline(D.route, {color: D.color, weight: 6, opacity: 0.85}).addTo(map);
  var dots = N.map(function (n, i) {
    var end = (i === 0 || i === last);
    var c = L.circleMarker([n.lat, n.lon], {
      radius: end ? 7 : 4, color: '#fff', weight: 1.5, fillOpacity: 1,
      fillColor: i === 0 ? '#1a8f2e' : (i === last ? '#c62828' : D.color)
    }).addTo(map);
    c.bindTooltip((i + 1) + '. ' + n.name);
    c.on('mouseover', function () { mark(i, false, true); });
    c.on('click', function () { mark(i, true, true); });
    return c;
  });
  var halo = L.circleMarker([0, 0], {radius: 16, color: '#ff00aa', weight: 4,
                                     fill: false, interactive: false});
  function frame() { map.invalidateSize(); map.fitBounds(L.latLngBounds(D.route), {padding: [25, 25]}); }
  frame();
  setTimeout(frame, 300);

  document.getElementById('pv-head').innerHTML =
    '<b style="color:' + D.color + '">' + D.algo + '</b> path · ' + N.length +
    ' nodes · ' + D.km.toFixed(2) + ' km · cost ' + D.cost.toFixed(2) +
    '<div class="pv-sub">Point at a node to see it on the map; click to zoom in.</div>';

  var rows = document.getElementById('pv-rows');
  var els = N.map(function (n, i) {
    var r = document.createElement('div');
    r.className = 'pv-row' + (i === 0 ? ' pv-start' : '') + (i === last ? ' pv-goal' : '');
    var tag = i === 0 ? ' 🟢 start' : (i === last ? ' 🔴 destination' : '');
    r.innerHTML = '<div class="pv-num">' + (i + 1) + '</div><div>' + esc(n.name) + tag +
      '<div class="pv-sub">' + n.km.toFixed(2) + ' km from start' +
      (n.next ? ' · then ' + esc(n.next) : '') + '<br>' +
      n.lat.toFixed(5) + ', ' + n.lon.toFixed(5) + ' · node ' + n.id + '</div></div>';
    r.addEventListener('mouseenter', function () { mark(i, false, false); });
    r.addEventListener('click', function () { mark(i, true, false); });
    rows.appendChild(r);
    return r;
  });
  rows.addEventListener('mouseleave', function () { halo.remove(); });

  var cur = -1;
  function mark(i, zoom, fromMap) {
    var n = N[i], ll = L.latLng(n.lat, n.lon);
    if (cur >= 0) { els[cur].classList.remove('on'); }
    cur = i;
    els[i].classList.add('on');
    halo.setLatLng(ll).addTo(map);
    dots[i].bringToFront();
    if (fromMap) {
      els[i].scrollIntoView({block: 'nearest'});
    } else {
      dots[i].openTooltip();
      if (zoom) { map.setView(ll, Math.max(map.getZoom(), 17)); }
      else if (!map.getBounds().pad(-0.1).contains(ll)) { map.panTo(ll); }
    }
  }
  function esc(s) {
    return String(s).replace(/[&<>"]/g, function (c) {
      return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c];
    });
  }
})();
"""
