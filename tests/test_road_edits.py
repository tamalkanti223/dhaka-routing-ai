"""Road edits must change real search decisions and be independently reversible."""
from copy import deepcopy

import pytest

from config import Limits, Weights
from cost_function import EdgeAttr
from graph_data import Edge, RoadGraph
from road_edits import compare_routes, replay_edits, replace_edit
from scenarios import Scenario, build_scenario, with_reverse
from search_algorithms import ProblemInstance, run_algorithm


def roads():
    # Two routes: 1-2-4 costs 2 km; 1-3-4 costs 2.8 km at neutral conditions.
    graph = RoadGraph(nodes={1: (23.74, 90.40), 2: (23.7401, 90.401),
                             3: (23.741, 90.401), 4: (23.74, 90.402)})
    for eid, (u, v, length) in enumerate([(1, 2, 1), (2, 4, 1),
                                         (1, 3, 1.4), (3, 4, 1.4)]):
        edge = Edge(eid, u, v, length, eid, "residential", f"Road {eid}",
                    [graph.coord(u), graph.coord(v)])
        graph.edges[eid] = edge
        graph.adj[u].append(edge)
    baseline = Scenario("manual", False, 1234,
                        {eid: EdgeAttr() for eid in graph.edges})
    return graph, baseline


def solve(graph, scenario, algorithm="A*", weights=None, night=False):
    problem = ProblemInstance(graph, scenario.get, weights or Weights(), night, Limits())
    return run_algorithm(algorithm, problem, 1, 4)


def value_edit(**values):
    return {"kind": "values", "label": "First road", "eids": [0], "values": values}


@pytest.mark.parametrize("values,night", [
    ({"traffic": 5.0}, False), ({"condition": 5.0}, False),
    ({"safety_day": 4.0, "safety_night": 4.0}, False),
    ({"safety_night": 4.0}, True),
    ({"traffic": 5.0, "condition": 5.0, "safety_day": 4.0}, False),
    ({"closed": True}, False),
])
def test_selected_parameter_can_make_ucs_and_astar_detour(values, night):
    graph, base = roads()
    # A safety-only test uses a nonzero safety weight large enough to favour the detour.
    weights = Weights(0.2, 0.3, 0.5) if "safety_day" in values or "safety_night" in values else Weights()
    changed = replay_edits(graph, base, [value_edit(**values)])
    for algorithm in ("UCS", "A*"):
        before = solve(graph, base, algorithm, weights, night)
        after = solve(graph, changed, algorithm, weights, night)
        assert before.node_path == [1, 2, 4]
        assert after.node_path == [1, 3, 4]
        assert compare_routes({algorithm: before}, {algorithm: after}, changed,
                              weights, night)[0]["path_changed"] is True
    assert base.attrs[0] == EdgeAttr()  # editing never changes the reset snapshot
    assert changed.attrs[1] == base.attrs[1]  # no unrelated roads changed


def test_small_change_zero_weight_and_inactive_night_do_not_force_a_fake_detour():
    graph, base = roads()
    for values, weights in [({"traffic": 1.1}, Weights()),
                            ({"traffic": 5.0}, Weights(0, 0.5, 0.5)),
                            ({"safety_night": 4.0}, Weights())]:
        scenario = replay_edits(graph, base, [value_edit(**values)])
        assert solve(graph, scenario, weights=weights).node_path == [1, 2, 4]


def test_bfs_ignores_penalties_but_obeys_closures():
    graph, base = roads()
    changed = replay_edits(graph, base, [value_edit(traffic=5.0, condition=5.0)])
    assert solve(graph, changed, "BFS").node_path == [1, 2, 4]
    blocked = replay_edits(graph, base, [value_edit(closed=True)])
    assert solve(graph, blocked, "BFS").node_path == [1, 3, 4]


def test_one_parameter_does_not_overwrite_other_values_and_undo_preserves_overlap():
    graph, base = roads()
    base.attrs[0].safety_day = 2.7
    edits = [value_edit(traffic=5.0), value_edit(condition=4.0)]
    scenario = replay_edits(graph, base, edits)
    assert (scenario.get(0).traffic, scenario.get(0).condition, scenario.get(0).safety_day) == (5, 4, 2.7)
    undo_first = replay_edits(graph, base, edits[1:])
    assert (undo_first.get(0).traffic, undo_first.get(0).condition) == (1, 4)
    assert replay_edits(graph, base, []).attrs == base.attrs


def test_condition_replacement_and_unticking_use_active_scenario_not_normal_day():
    graph, _ = roads()
    base = build_scenario(graph, "rush_hour")
    original = deepcopy(base.attrs)
    edit = {"kind": "conditions", "label": "First road", "eids": [0],
            "effects": ["rain", "construction"]}
    edits = replace_edit([], edit)
    blocked = replay_edits(graph, base, edits)
    assert blocked.get(0).closed
    edit = {**edit, "effects": ["rain"]}
    edits = replace_edit(edits, edit)
    rainy = replay_edits(graph, base, edits)
    assert len(edits) == 1 and not rainy.get(0).closed
    assert rainy.get(0).traffic == original[0].traffic
    assert rainy.get(0).condition == round(min(5, original[0].condition + 1), 3)
    assert rainy.get(1) == original[1]
    edits = replace_edit(edits, {**edit, "effects": []})
    assert replay_edits(graph, base, edits).attrs == original


def test_closing_all_exits_reports_no_path_then_removal_restores_route():
    graph, base = roads()
    edit = {**value_edit(closed=True), "eids": [0, 2]}
    assert not solve(graph, replay_edits(graph, base, [edit])).found
    assert solve(graph, replay_edits(graph, base, [])).node_path == [1, 2, 4]


def test_reverse_direction_does_not_include_a_different_parallel_road():
    graph, _ = roads()
    original = graph.edges[0]
    for eid, way in [(4, original.way_id), (5, 999)]:
        back = Edge(eid, 2, 1, 1, way, "residential", "Reverse",
                    [graph.coord(2), graph.coord(1)])
        graph.edges[eid] = back
        graph.adj[2].append(back)
    assert {e.eid for e in with_reverse(graph, [original])} == {0, 4}


def test_comparison_evaluates_old_route_using_new_costs():
    graph, base = roads()
    before = solve(graph, base)
    changed = replay_edits(graph, base, [value_edit(traffic=5.0)])
    after = solve(graph, changed)
    row = compare_routes({"A*": before}, {"A*": after}, changed, Weights(), False)[0]
    assert row["previous_cost"] == 2
    assert row["old_path_at_new_cost"] == 4
    assert row["new_path_cost"] == 2.8
