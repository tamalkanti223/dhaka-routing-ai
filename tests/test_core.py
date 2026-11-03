"""
Focused correctness tests on tiny synthetic graphs (never the OSM map).

Run from the project root:
    pytest -q
"""

from __future__ import annotations

import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Weights, Limits
from cost_function import EdgeAttr, edge_cost
from graph_data import RoadGraph, Edge, haversine_km
from heuristics import verify_heuristic, make_heuristic
from search_algorithms import (ProblemInstance, bfs, dfs, ids, ucs, gbfs, astar,
                               run_algorithm, Status)

W = Weights(0.5, 0.3, 0.2)
LIM = Limits(timeout_s=30, expansion_budget=1_000_000, ids_max_depth=50)


def make_graph(node_coords, edges):
    """edges: list of (u, v, length_km, oneway_bool). Returns RoadGraph."""
    g = RoadGraph()
    g.nodes = dict(node_coords)
    eid = 0
    for (u, v, length, oneway) in edges:
        cu, cv = node_coords[u], node_coords[v]
        g.edges[eid] = Edge(eid, u, v, length, 1, "residential", "R",
                            [cu, cv]); g.adj[u].append(g.edges[eid]); eid += 1
        if not oneway:
            g.edges[eid] = Edge(eid, v, u, length, 1, "residential", "R",
                                [cv, cu]); g.adj[v].append(g.edges[eid]); eid += 1
    return g


def problem(g, attrs=None, night=False):
    attrs = attrs or {}
    return ProblemInstance(g, lambda eid: attrs.get(eid, EdgeAttr()), W, night, LIM)


# simple square-ish coordinate layout (lat, lon)
COORDS = {
    1: (23.740, 90.400),
    2: (23.740, 90.410),
    3: (23.750, 90.410),
    4: (23.750, 90.400),
    5: (23.745, 90.420),
}


def test_start_is_goal():
    g = make_graph(COORDS, [(1, 2, 1.0, False)])
    for algo in (bfs, dfs, ids, ucs, gbfs, astar):
        r = algo(problem(g), 1, 1)
        assert r.status == Status.START_IS_GOAL
        assert r.hops == 0 and r.distance_km == 0.0


def test_oneway_blocks_reverse():
    # 1->2 one-way only; going 2->1 must be impossible.
    g = make_graph(COORDS, [(1, 2, 1.0, True)])
    r = ucs(problem(g), 2, 1)
    assert r.status == Status.NO_PATH
    r2 = ucs(problem(g), 1, 2)
    assert r2.found and r2.hops == 1


def test_closed_road_excluded_by_all():
    # Only path 1->2 exists; close it; nobody may traverse.
    g = make_graph(COORDS, [(1, 2, 1.0, False)])
    attrs = {0: EdgeAttr(closed=True)}  # eid 0 is 1->2
    for algo in (bfs, dfs, ids, ucs, gbfs, astar):
        r = algo(problem(g, attrs), 1, 2)
        assert r.status == Status.NO_PATH, algo.__name__


def test_unreachable_destination():
    # node 5 is isolated.
    g = make_graph(COORDS, [(1, 2, 1.0, False), (2, 3, 1.0, False)])
    g.nodes[5] = COORDS[5]
    r = astar(problem(g), 1, 5)
    assert r.status == Status.NO_PATH


def test_parallel_edges_reconstruction():
    # Two parallel edges 1->2 with different lengths; UCS must pick the cheaper
    # and the returned edge must be the exact one used.
    g = RoadGraph()
    g.nodes = {1: COORDS[1], 2: COORDS[2]}
    g.edges[0] = Edge(0, 1, 2, 5.0, 1, "residential", "long", [COORDS[1], COORDS[2]])
    g.edges[1] = Edge(1, 1, 2, 1.0, 1, "residential", "short", [COORDS[1], COORDS[2]])
    g.adj[1] = [g.edges[0], g.edges[1]]
    r = ucs(problem(g), 1, 2)
    assert r.found and len(r.edge_path) == 1
    assert r.edge_path[0].eid == 1  # the short one
    assert math.isclose(r.distance_km, 1.0)


def test_weight_validation():
    with pytest.raises(ValueError):
        Weights(0.5, 0.3, 0.1).validate()      # sums to 0.9
    with pytest.raises(ValueError):
        Weights(-0.1, 0.6, 0.5).validate()     # negative
    Weights(0.5, 0.3, 0.2).validate()          # ok
    w = Weights.normalized(2, 1, 1)
    assert math.isclose(w.traffic + w.condition + w.safety, 1.0)


def test_cost_calculation():
    e = Edge(0, 1, 2, 2.0, 1, "residential", "R", [COORDS[1], COORDS[2]])
    a = EdgeAttr(traffic=4, condition=2, safety_day=3)
    # mult = 1 + 0.5*3 + 0.3*1 + 0.2*2 = 1+1.5+0.3+0.4 = 3.2 ; cost = 6.4
    assert math.isclose(edge_cost(e, a, W, night=False), 6.4)
    assert edge_cost(e, EdgeAttr(closed=True), W, False) == math.inf


def test_cycle_handling():
    # triangle with a cycle; search must terminate and find the goal.
    g = make_graph(COORDS, [(1, 2, 1.0, False), (2, 3, 1.0, False),
                            (3, 1, 1.0, False)])
    for algo in (bfs, dfs, ids, ucs, gbfs, astar):
        r = algo(problem(g), 1, 3)
        assert r.found, algo.__name__


def test_heuristic_consistency_and_goal_zero():
    g = make_graph(COORDS, [(1, 2, None, False), (2, 3, None, False),
                            (1, 4, None, False), (4, 3, None, False)])
    # set lengths exactly to straight-line so length>=haversine holds w/ equality
    for e in g.edges.values():
        e.length_km = haversine_km(g.coord(e.u), g.coord(e.v))
    rep = verify_heuristic(g, goal=3)
    assert rep.goal_zero_ok and rep.geometry_ok and rep.consistency_ok
    h = make_heuristic(g, 3)
    assert math.isclose(h(3), 0.0, abs_tol=1e-9)


def test_ucs_astar_agree_optimal():
    g = make_graph(COORDS, [(1, 2, None, False), (2, 3, None, False),
                            (1, 4, None, False), (4, 3, None, False),
                            (2, 5, None, False), (5, 3, None, False)])
    for e in g.edges.values():
        e.length_km = haversine_km(g.coord(e.u), g.coord(e.v))
    attrs = {}
    prob = problem(g, attrs)
    ru, ra = ucs(prob, 1, 3), astar(prob, 1, 3)
    assert ru.found and ra.found
    assert math.isclose(ru.weighted_cost, ra.weighted_cost, rel_tol=1e-9)


def test_min_hop_differs_from_min_cost():
    """
    Direct 2-hop route is physically short but heavily penalised; a longer
    (3-hop) detour has lower weighted cost.  BFS (min-hop) and UCS (min-cost)
    must therefore disagree.
    """
    coords = {1: (23.74, 90.40), 2: (23.74, 90.41), 3: (23.74, 90.42),
              4: (23.745, 90.41)}
    g = RoadGraph()
    g.nodes = dict(coords)
    # direct short edges 1-2-3 (2 hops) but we will make them very congested
    def add(eid, u, v, length):
        g.edges[eid] = Edge(eid, u, v, length, 1, "residential", "R",
                            [coords[u], coords[v]])
        g.adj[u].append(g.edges[eid])
    add(0, 1, 2, 1.0)
    add(1, 2, 3, 1.0)
    # detour 1-4-...-3 via node 4 (2 edges too) -> make a true 3-hop detour
    add(2, 1, 4, 1.0)
    add(3, 4, 2, 1.0)   # rejoins at 2 ... still short
    # Build so min-hop = 2 (1->2->3), alt = 3 hops (1->4->2->3)
    attrs = {
        0: EdgeAttr(traffic=5, condition=5, safety_day=4),   # 1->2 awful
        1: EdgeAttr(traffic=1),                               # 2->3 fine
        2: EdgeAttr(traffic=1),                               # 1->4 fine
        3: EdgeAttr(traffic=1),                               # 4->2 fine
    }
    prob = problem(g, attrs)
    b = bfs(prob, 1, 3)
    u = ucs(prob, 1, 3)
    assert b.found and u.found
    assert b.hops == 2
    assert u.weighted_cost <= b.weighted_cost + 1e-9
    # UCS avoids the awful direct edge -> more hops, lower cost
    assert u.hops >= b.hops
    assert u.weighted_cost < b.weighted_cost


def test_resource_limit_status():
    g = make_graph(COORDS, [(1, 2, 1.0, False), (2, 3, 1.0, False)])
    tiny = Limits(timeout_s=30, expansion_budget=1, ids_max_depth=50)
    prob = ProblemInstance(g, lambda eid: EdgeAttr(), W, False, tiny)
    r = bfs(prob, 1, 3)
    assert r.status == Status.RESOURCE_LIMIT


def _chain_graph():
    """
    Two-way road 1-a-b-2 (a, b are pure shape points of way 1) with a branch
    2-3 on way 2 and a one-way shape chain 3-c-4 on way 3.
    """
    coords = {1: (23.740, 90.400), 10: (23.741, 90.401), 11: (23.742, 90.402),
              2: (23.743, 90.403), 3: (23.744, 90.405), 12: (23.745, 90.406),
              4: (23.746, 90.407)}
    g = RoadGraph()
    g.nodes = dict(coords)
    eid = 0

    def seg(u, v, way, both):
        nonlocal eid
        for a, b in ([(u, v), (v, u)] if both else [(u, v)]):
            e = Edge(eid, a, b, haversine_km(coords[a], coords[b]), way,
                     "residential", f"way{way}", [coords[a], coords[b]])
            g.edges[eid] = e
            g.adj[a].append(e)
            eid += 1

    seg(1, 10, 1, True); seg(10, 11, 1, True); seg(11, 2, 1, True)
    seg(2, 3, 2, True)
    seg(3, 12, 3, False); seg(12, 4, 3, False)
    return g


def test_simplify_preserves_length_connectivity_direction():
    from graph_data import simplify_graph
    g = _chain_graph()
    s = simplify_graph(g, verbose=False)
    total = lambda gr: sum(e.length_km for e in gr.edges.values())
    assert math.isclose(total(s), total(g), rel_tol=1e-12)
    assert len(s.weakly_connected_components()) == len(g.weakly_connected_components())
    assert not any(e.u == e.v for e in s.edges.values())
    # shape points 10, 11, 12 are folded away; junctions/endpoints remain
    assert set(s.nodes) == {1, 2, 3, 4}
    # the one-way chain stays one-way
    pairs = {(e.u, e.v) for e in s.edges.values()}
    assert (3, 4) in pairs and (4, 3) not in pairs
    assert (1, 2) in pairs and (2, 1) in pairs
    # geometry keeps every shape point
    e12 = next(e for e in s.edges.values() if (e.u, e.v) == (1, 2))
    assert len(e12.geometry) == 4


def test_dfs_path_is_valid_walk():
    g = make_graph(COORDS, [(1, 2, 1.0, False), (2, 3, 1.0, False),
                            (1, 4, 1.0, False), (4, 3, 1.0, False),
                            (3, 5, 1.0, False)])
    r = dfs(problem(g), 1, 5)
    assert r.found
    assert r.node_path[0] == 1 and r.node_path[-1] == 5
    for e, (a, b) in zip(r.edge_path, zip(r.node_path, r.node_path[1:])):
        assert (e.u, e.v) == (a, b)
    assert len(set(r.node_path)) == len(r.node_path)   # no repeated node


def test_ids_respects_expansion_budget():
    # A long line: IDS needs many iterations; a small budget must stop it.
    coords = {i: (23.74, 90.40 + i * 0.001) for i in range(30)}
    edges = [(i, i + 1, 0.1, False) for i in range(29)]
    g = make_graph(coords, edges)
    small = Limits(timeout_s=30, expansion_budget=50, ids_max_depth=100)
    prob = ProblemInstance(g, lambda eid: EdgeAttr(), W, False, small)
    r = ids(prob, 0, 29)
    assert r.status == Status.RESOURCE_LIMIT
    assert r.extra.get("limit") == "expansion_budget"
    assert r.nodes_expanded <= 51


def test_ids_reports_no_path_not_limit():
    # Goal unreachable in a small graph: IDS must say NO_PATH, not RESOURCE_LIMIT.
    g = make_graph(COORDS, [(1, 2, 1.0, False), (2, 3, 1.0, False)])
    g.nodes[5] = COORDS[5]
    r = ids(problem(g), 1, 5)
    assert r.status == Status.NO_PATH


def test_largest_component_is_strongly_connected():
    # 1<->2<->3 two-way, plus a one-way trap 3->4 (4 has no way out).
    from graph_data import keep_largest_component
    from search_algorithms import ucs as _ucs
    g = make_graph(COORDS, [(1, 2, 1.0, False), (2, 3, 1.0, False),
                            (3, 4, 1.0, True)])
    k = keep_largest_component(g, verbose=False)
    assert set(k.nodes) == {1, 2, 3}
    for a in k.nodes:
        for b in k.nodes:
            if a != b:
                assert _ucs(problem(k), a, b).found


def test_node_index_nearest():
    from graph_data import NodeIndex
    g = make_graph(COORDS, [(1, 2, 1.0, False), (3, 4, 1.0, False)])
    idx = NodeIndex(g)
    for n, (la, lo) in COORDS.items():
        if n in g.nodes:
            assert idx.nearest(la + 1e-5, lo + 1e-5)[0] == n


def _named_graph():
    """1 -TSC Mor- 2 -Curzon Hall Road- 3, plus an unnamed edge 3-4 and a Bangla name."""
    coords = {1: (23.730, 90.395), 2: (23.731, 90.396), 3: (23.732, 90.397),
              4: (23.733, 90.398)}
    g = RoadGraph()
    g.nodes = dict(coords)
    spec = [(1, 2, "TSC Mor", 7), (2, 3, "Curzon Hall Road", 8),
            (3, 4, "(unnamed residential 9)", 9)]
    eid = 0
    for u, v, name, way in spec:
        for a, b in ((u, v), (v, u)):
            e = Edge(eid, a, b, haversine_km(coords[a], coords[b]), way,
                     "residential", name, [coords[a], coords[b]])
            g.edges[eid] = e
            g.adj[a].append(e)
            eid += 1
    g.meta["alt_names"] = {8: "কার্জন হল রোড"}
    return g


def test_junction_index_lists_every_node_without_inventing_names():
    from graph_data import junction_index
    g = _named_graph()
    idx = junction_index(g)
    assert set(idx.values()) == set(g.nodes)            # nothing left out
    assert len(idx) == len(g.nodes)                     # one unique label per node
    label3 = next(k for k, v in idx.items() if v == 3)
    assert "Curzon Hall Road" in label3 and "unnamed residential" not in label3
    label4 = next(k for k, v in idx.items() if v == 4)
    # 4 is only on an unnamed lane: labelled by the nearest real road, not invented
    assert "near Curzon Hall Road" in label4 and "unnamed" not in label4
    assert all("—" in k for k in idx)                   # coordinates always present


def test_place_index_aliases_and_bangla_names():
    from graph_data import place_index
    g = _named_graph()
    p = place_index(g)
    du = [k for k in p if k.startswith("Dhaka University")]
    assert any("TSC Mor" in k for k in du) and any("Curzon Hall" in k for k in du)
    assert not any("Teachers Club" in k for k in du)    # anchor absent -> alias skipped
    tsc = next(k for k in du if "TSC Mor" in k)
    assert p[tsc] == p["TSC Mor"]                       # same node as the real road
    assert p["কার্জন হল রোড"] == p["Curzon Hall Road"]   # Bangla name is searchable


def test_simulation_page_has_every_algorithm_and_handles_no_trace():
    import json, re
    import simulation
    g = make_graph(COORDS, [(1, 2, 1.0, False), (2, 3, 1.0, False),
                            (1, 4, 1.0, False), (4, 3, 1.0, False)])
    for e in g.edges.values():
        e.length_km = haversine_km(g.coord(e.u), g.coord(e.v))
    prob = problem(g)
    results = {n: run_algorithm(n, prob, 1, 3, record_trace=True)
               for n in ("BFS", "UCS", "A*")}
    html = simulation.build_html(g, results, 1, 3)
    data = json.loads(re.search(r"var DATA = (\{.*?\});\n", html, re.S).group(1))
    assert [a["name"] for a in data["algos"]] == ["BFS", "UCS", "A*"]
    for a in data["algos"]:
        assert a["found"] and a["pts"] and a["route"]
        assert a["total"] == results[a["name"]].nodes_expanded
        assert a["pts"][-1][2] <= a["total"]            # steps never exceed the real count
    # no recorded trace -> nothing to replay
    assert simulation.build_html(g, {"UCS": run_algorithm("UCS", prob, 1, 3)}, 1, 3) is None


def test_path_view_lists_every_route_node_in_order():
    import json, re
    import path_view
    g = make_graph(COORDS, [(1, 2, 1.0, False), (2, 3, 1.0, False)])
    for e in g.edges.values():
        e.length_km = haversine_km(g.coord(e.u), g.coord(e.v))
    r = run_algorithm("A*", problem(g), 1, 3)
    html = path_view.build_html(g, r, "A*", {2: "Middle Road"})
    data = json.loads(re.search(r"var D = (\{.*?\});\n", html, re.S).group(1))
    assert [n["id"] for n in data["nodes"]] == r.node_path == [1, 2, 3]
    assert data["nodes"][0]["km"] == 0 and data["nodes"][1]["name"] == "Middle Road"
    assert abs(data["nodes"][-1]["km"] - r.distance_km) < 1e-3
    # no path -> no page
    from search_algorithms import SearchResult, Status
    assert path_view.build_html(g, SearchResult("A*", Status.NO_PATH), "A*", {}) is None



def test_local_conditions_add_and_remove_on_one_stretch():
    from scenarios import (apply_local_conditions, build_scenario,
                           normal_day_attr, with_reverse)
    g = make_graph(COORDS, [(1, 2, 1.0, False), (2, 3, 1.0, False),
                            (1, 4, 1.0, False), (4, 3, 1.0, False)])
    sc = build_scenario(g, "normal_day")
    e12 = next(e for e in g.edges.values() if (e.u, e.v) == (1, 2))
    both = with_reverse(g, [e12])
    assert {(e.u, e.v) for e in both} == {(1, 2), (2, 1)}
    apply_local_conditions(sc, both, {"rush_hour", "rain"})
    base = normal_day_attr(e12)
    a = sc.get(e12.eid)
    assert a.traffic == round(min(5.0, base.traffic + 1.4), 3)
    assert a.condition == round(min(5.0, base.condition + 1.0), 3) and not a.closed
    apply_local_conditions(sc, both, {"construction"})
    assert all(sc.get(e.eid).closed for e in both)          # closed both ways
    apply_local_conditions(sc, both, {"closed"})             # plain closure
    assert all(sc.get(e.eid).closed and not sc.get(e.eid).incident for e in both)
    apply_local_conditions(sc, both, set())                  # nothing ticked
    assert sc.get(e12.eid) == base                           # = normal day again
    with pytest.raises(ValueError):
        apply_local_conditions(sc, both, {"tornado"})


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
