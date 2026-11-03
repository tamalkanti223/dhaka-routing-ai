"""
benchmark.py — run all six algorithms over reproducible origin/destination
pairs and scenarios, collect metrics, write CSV, and render comparison plots.

Timing honesty:
* We time ONLY the search call (record_trace=False so trace building never
  contaminates timing).  Graph loading, scenario building, rendering and
  export all happen outside the timed region.
* Each (algorithm, OD, scenario) is timed `repeats` times and we keep the
  median millisecond value, while correctness metrics come from the first run
  (they are deterministic across runs).

Nothing here invents results — every number comes from an actual run.
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import random
import statistics
import time
from dataclasses import asdict
from typing import Dict, List, Optional, Tuple

import config
from graph_data import RoadGraph, load_graph, haversine_km
from scenarios import build_scenario, Scenario, SCENARIO_NAMES
from search_algorithms import (ALGORITHMS, ProblemInstance, Status, SearchResult,
                               run_algorithm)


def nearest_node(graph: RoadGraph, lat: float, lon: float) -> int:
    best, best_d = None, float("inf")
    for n, (la, lo) in graph.nodes.items():
        d = haversine_km((la, lo), (lat, lon))
        if d < best_d:
            best, best_d = n, d
    return best


def pick_od_pairs(graph: RoadGraph, k: int, seed: int,
                  min_km: float = 1.5) -> List[Tuple[int, int]]:
    """Pick k reproducible, reasonably-separated origin/destination pairs."""
    rng = random.Random(seed)
    nodes = list(graph.nodes.keys())
    pairs: List[Tuple[int, int]] = []
    attempts = 0
    while len(pairs) < k and attempts < k * 200:
        attempts += 1
        a, b = rng.choice(nodes), rng.choice(nodes)
        if a == b:
            continue
        if haversine_km(graph.coord(a), graph.coord(b)) < min_km:
            continue
        pairs.append((a, b))
    return pairs


def make_problem(graph: RoadGraph, sc: Scenario, cfg: config.AppConfig,
                 night: Optional[bool] = None) -> ProblemInstance:
    return ProblemInstance(
        graph=graph,
        attr_of=sc.get,
        weights=cfg.weights,
        night=sc.night if night is None else night,
        limits=cfg.limits,
    )


def time_search(name: str, prob: ProblemInstance, s: int, g: int,
                repeats: int) -> Tuple[SearchResult, float]:
    """Run once for metrics, then `repeats` times for a median timing (ms)."""
    base = run_algorithm(name, prob, s, g, record_trace=False)
    times = [base.time_ms]
    for _ in range(max(0, repeats - 1)):
        r = run_algorithm(name, prob, s, g, record_trace=False)
        times.append(r.time_ms)
    return base, statistics.median(times)


def benchmark(graph: RoadGraph, cfg: config.AppConfig,
              od_pairs: List[Tuple[int, int]], scenario_names: List[str],
              algorithms: List[str], repeats: int = 3,
              verbose: bool = True) -> List[dict]:
    rows: List[dict] = []
    for sname in scenario_names:
        sc = build_scenario(graph, sname, seed=cfg.seed)
        prob = make_problem(graph, sc, cfg)
        for (s, g) in od_pairs:
            # UCS reference cost for this OD+scenario (if it completes).
            ucs_res = run_algorithm("UCS", prob, s, g, record_trace=False)
            ucs_cost = ucs_res.weighted_cost if ucs_res.found else None

            for name in algorithms:
                res, med_ms = time_search(name, prob, s, g, repeats)
                cost_ratio = (res.weighted_cost / ucs_cost
                              if (res.found and ucs_cost) else None)
                row = {
                    "scenario": sname,
                    "night": sc.night,
                    "algorithm": name,
                    "start": s,
                    "goal": g,
                    "status": res.status.value,
                    "distance_km": round(res.distance_km, 4) if res.found else "",
                    "weighted_cost": round(res.weighted_cost, 4) if res.found else "",
                    "hops": res.hops if res.found else "",
                    "nodes_expanded": res.nodes_expanded,
                    "unique_states_expanded": res.unique_states_expanded,
                    "nodes_generated": res.nodes_generated,
                    "peak_frontier": res.peak_frontier,
                    "time_ms_median": round(med_ms, 4),
                    "cost_ratio_vs_ucs": round(cost_ratio, 4) if cost_ratio else "",
                }
                rows.append(row)
                if verbose:
                    print(f"[{sname:22s}] {res.one_line()}")
    return rows


def write_csv(rows: List[dict], path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not rows:
        return
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"[benchmark] wrote {len(rows)} rows -> {path}")


def make_plots(rows: List[dict], out_dir: str) -> List[str]:
    """Render comparison bar charts (averaged over OD pairs per scenario)."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:  # noqa: BLE001
        print(f"[benchmark] matplotlib unavailable ({exc}); skipping plots")
        return []

    os.makedirs(out_dir, exist_ok=True)
    algos = list(ALGORITHMS.keys())
    scenarios = sorted({r["scenario"] for r in rows})

    def agg(metric: str, only_success: bool) -> Dict[str, List[float]]:
        data = {a: [] for a in algos}
        for sname in scenarios:
            for a in algos:
                vals = [r[metric] for r in rows
                        if r["scenario"] == sname and r["algorithm"] == a
                        and r[metric] != ""
                        and (not only_success or r["status"] == "success")]
                vals = [float(v) for v in vals]
                # NaN draws no bar: a search that never succeeded has no cost
                # or distance, and plotting 0 would look like a perfect score.
                data[a].append(statistics.mean(vals) if vals else math.nan)
        return data

    specs = [
        ("weighted_cost", True, "Average weighted path cost", "cost"),
        ("nodes_expanded", False, "Average nodes expanded", "expanded"),
        ("time_ms_median", False, "Average search runtime (ms)", "runtime"),
        ("distance_km", True, "Average physical path length (km)", "distance"),
    ]
    written = []
    for metric, succ, title, slug in specs:
        data = agg(metric, succ)
        fig, ax = plt.subplots(figsize=(10, 5))
        x = range(len(scenarios))
        width = 0.13
        for i, a in enumerate(algos):
            ax.bar([xi + i * width for xi in x], data[a], width, label=a)
        ax.set_xticks([xi + width * (len(algos) - 1) / 2 for xi in x])
        ax.set_xticklabels(scenarios, rotation=20, ha="right", fontsize=8)
        ax.set_title(title)
        ax.set_ylabel(metric)
        ax.legend(fontsize=8, ncol=6)
        fig.tight_layout()
        p = os.path.join(out_dir, f"chart_{slug}.png")
        fig.savefig(p, dpi=120)
        plt.close(fig)
        written.append(p)
        print(f"[benchmark] plot -> {p}")
    return written


def main() -> None:
    ap = argparse.ArgumentParser(description="Benchmark the six search algorithms.")
    ap.add_argument("--osm", default=config.DEFAULT_OSM_FILE)
    ap.add_argument("--no-simplify", action="store_true")
    ap.add_argument("--region", action="store_true",
                    help="restrict to the central demo bounding box")
    ap.add_argument("--pairs", type=int, default=5)
    ap.add_argument("--min-km", type=float, default=1.5,
                    help="minimum straight-line distance between origin and destination")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--timeout", type=float, default=config.DEFAULT_LIMITS.timeout_s)
    ap.add_argument("--budget", type=int, default=config.DEFAULT_LIMITS.expansion_budget,
                    help="expansion budget per search")
    ap.add_argument("--ids-depth", type=int, default=config.DEFAULT_LIMITS.ids_max_depth)
    ap.add_argument("--scenarios", nargs="*", default=["normal_day", "rush_hour",
                                                       "rain_flood", "night"])
    ap.add_argument("--algorithms", nargs="*", default=list(ALGORITHMS.keys()))
    ap.add_argument("--seed", type=int, default=config.GLOBAL_SEED)
    ap.add_argument("--out", default=os.path.join(config.OUTPUT_DIR, "benchmark.csv"))
    ap.add_argument("--no-plots", action="store_true")
    args = ap.parse_args()

    cfg = config.AppConfig(osm_file=args.osm, simplify=not args.no_simplify, seed=args.seed)
    cfg.limits.timeout_s = args.timeout
    cfg.limits.expansion_budget = args.budget
    cfg.limits.ids_max_depth = args.ids_depth

    graph = load_graph(cfg)
    if args.region:
        from graph_data import subgraph_bbox
        graph = subgraph_bbox(graph, config.DEMO_REGION_BBOX)

    od = pick_od_pairs(graph, args.pairs, seed=args.seed, min_km=args.min_km)
    print(f"[benchmark] {len(od)} OD pairs, scenarios={args.scenarios}, "
          f"algorithms={args.algorithms}, repeats={args.repeats}")

    rows = benchmark(graph, cfg, od, args.scenarios, args.algorithms,
                     repeats=args.repeats)
    write_csv(rows, args.out)
    if not args.no_plots:
        make_plots(rows, os.path.dirname(os.path.abspath(args.out)))


if __name__ == "__main__":
    main()
