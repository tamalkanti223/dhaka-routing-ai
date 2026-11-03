"""Exercise the presentation workflow through real Streamlit controls and OSM data."""
from copy import deepcopy
from pathlib import Path

from streamlit.testing.v1 import AppTest
import math

from cost_function import edge_cost


def test_all_scenarios_weights_and_night_replan_without_a_blockage():
    at = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"),
                           default_timeout=60).run()
    next(r for r in at.radio if r.label == "Network").set_value(
        "Central demo region (small)").run()
    places = at.session_state["places_map"]
    for key, node in [("pick_start", 2432078557), ("pick_goal", 3940938359)]:
        at.selectbox(key=key).select(next(k for k, v in places.items() if v == node)).run()
    assert not at.exception
    from config import Weights

    def assert_recomputed(before, weights=None):
        assert not at.exception, [e.message for e in at.exception]
        results = at.session_state["results"]
        assert set(results) == set(before)
        assert all(results[name] is not before[name] for name in before)
        sc = at.session_state["scenario_obj"]
        for result in results.values():
            if result.found:
                expected = sum(edge_cost(e, sc.get(e.eid), weights or Weights(), sc.night)
                               for e in result.edge_path)
                assert math.isclose(result.weighted_cost, expected)
        assert len(at.session_state["replan_comparison"]) == len(before)

    # Every preset triggers a new search, including presets without closed roads.
    for name in ["rush_hour", "rain_flood", "construction_accident", "night", "manual", "normal_day"]:
        before = dict(at.session_state["results"])
        next(s for s in at.selectbox if s.label == "Scenario").select(name).run()
        assert at.session_state["scenario_obj"].name == name
        assert_recomputed(before)

    before = dict(at.session_state["results"])
    next(s for s in at.slider if s.label == "Traffic  wT").set_value(0.8).run()
    weights = Weights.normalized(0.8, 0.3, 0.2)
    assert_recomputed(before, weights)
    before = dict(at.session_state["results"])
    night_switch = next(t for t in at.toggle if t.label.startswith("Night-time"))
    night_switch.set_value(not night_switch.value).run()
    assert_recomputed(before, weights)

    # Auto-run off leaves stale routes cleared; pressing Run applies the new snapshot.
    next(c for c in at.checkbox if c.label == "Auto-run").uncheck().run()
    before = dict(at.session_state["results"])
    next(s for s in at.selectbox if s.label == "Scenario").select("rush_hour").run()
    assert not at.exception and not at.session_state["results"]
    next(b for b in at.button if b.label == "\u25b6 Run").click().run()
    assert_recomputed(before, weights)


def test_apply_combined_parameters_conditions_and_undo_in_dashboard():
    app = Path(__file__).resolve().parents[1] / "app.py"
    at = AppTest.from_file(str(app), default_timeout=60).run()

    def clean():
        assert not at.exception, [error.message for error in at.exception]

    def button(label):
        return next(item for item in at.button if item.label == label)

    def multi(label):
        return next(item for item in at.multiselect if item.label == label)

    clean()
    next(r for r in at.radio if r.label == "Network").set_value(
        "Central demo region (small)").run()
    places = at.session_state["places_map"]
    start = next(key for key, node in places.items() if node == 2432078557)
    goal = next(key for key, node in places.items() if node == 3940938359)
    at.selectbox(key="pick_start").select(start).run()
    at.selectbox(key="pick_goal").select(goal).run()
    clean()
    original = at.session_state["results"]["A*"]
    original_path = [edge.eid for edge in original.edge_path]
    baseline = deepcopy(at.session_state["scenario_obj"].attrs)

    # A traffic-only edit must not flatten condition and safety across the corridor.
    next(s for s in at.slider if s.label == "Traffic").set_value(5.0)
    button("Apply parameters and replan").click().run()
    clean()
    edited_ids = at.session_state["local_edits"][0]["eids"]
    attrs = at.session_state["scenario_obj"].attrs
    for eid in edited_ids:
        assert attrs[eid].traffic == 5
        assert attrs[eid].condition == baseline[eid].condition
        assert attrs[eid].safety_day == baseline[eid].safety_day
    assert [e.eid for e in at.session_state["results"]["A*"].edge_path] != original_path
    assert at.session_state["edit_reference"]["eids"] == original_path
    assert next(row for row in at.session_state["replan_comparison"]
                if row["algorithm"] == "A*")["path_changed"] is True
    button("Remove").click().run()
    clean()
    assert at.session_state["scenario_obj"].attrs == baseline
    assert [e.eid for e in at.session_state["results"]["A*"].edge_path] == original_path

    multi("Parameters to change").set_value(["traffic", "condition", "safety_day"]).run()
    for label, value in [("Traffic", 5.0), ("Road condition", 5.0), ("Day safety", 4.0)]:
        next(s for s in at.slider if s.label == label).set_value(value)
    button("Apply parameters and replan").click().run()
    clean()
    for eid in edited_ids:
        attr = at.session_state["scenario_obj"].get(eid)
        assert (attr.traffic, attr.condition, attr.safety_day) == (5, 5, 4)

    # Multiple conditions close exactly these roads, and can be unticked again.
    multi("Conditions on this part").set_value(["rain", "construction"]).run()
    button("Apply conditions and replan").click().run()
    clean()
    closed = at.session_state["scenario_obj"]
    assert all(closed.get(eid).closed for eid in edited_ids)
    for result in at.session_state["results"].values():
        assert not any(edge.eid in edited_ids for edge in result.edge_path)
    multi("Conditions on this part").set_value(["rain"]).run()
    button("Apply conditions and replan").click().run()
    clean()
    assert not any(at.session_state["scenario_obj"].get(eid).closed for eid in edited_ids)
    multi("Conditions on this part").set_value([]).run()
    button("Apply conditions and replan").click().run()
    clean()
    assert len(at.session_state["local_edits"]) == 1
    assert all(at.session_state["scenario_obj"].get(eid).traffic == 5 for eid in edited_ids)

    # The sidebar reset also recalculates, restoring the original route.
    next(b for b in at.button if "Reset road edits" in b.label).click().run()
    clean()
    assert not at.session_state["local_edits"]
    assert at.session_state["scenario_obj"].attrs == baseline
    assert [e.eid for e in at.session_state["results"]["A*"].edge_path] == original_path

    # A BFS-only run can edit one specific edge, without requiring UCS or A*.
    multi("Algorithms").set_value(["BFS"]).run()
    button("\u25b6 Run").click().run()
    clean()
    at.selectbox(key="edit_algorithm").select("BFS").run()
    next(r for r in at.radio if r.label == "Pick edge from").set_value("Current route").run()
    multi("Parameters to change").set_value(["traffic"]).run()
    bfs_path = [e.eid for e in at.session_state["results"]["BFS"].edge_path]
    next(s for s in at.slider if s.label == "Traffic").set_value(5.0)
    button("Apply parameters and replan").click().run()
    clean()
    assert list(at.session_state["results"]) == ["BFS"]
    assert [e.eid for e in at.session_state["results"]["BFS"].edge_path] == bfs_path
    assert len(at.session_state["local_edits"][0]["eids"]) <= 2
