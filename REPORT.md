# Intelligent Vehicle Routing in Dhaka Using Classical AI Search Under Dynamic Road Conditions

**AI Lab term project — technical report**

---

## 1. Problem

Given a real Dhaka road network and a time-varying set of road conditions
(traffic, surface quality, safety, closures), find a good route from an origin
to a destination, and compare how six classical search algorithms behave on the
same problem. The project is a study of **classical search and heuristic
design**; no machine learning is used.

The six algorithms are **BFS, DFS, IDS** (uninformed) and **UCS, Greedy
Best-First (GBFS), A\*** (cost- or heuristic-driven).

---

## 2. Road graph from OpenStreetMap

The map is a local OSM XML extract of central Dhaka
(`dhaka_central_roads.osm`, latitude 23.67–23.86, longitude 90.29–90.49),
exported from OpenStreetMap through the Overpass API (ODbL licence). It is
parsed with the Python standard library (`xml.etree.iterparse`), so loading
works fully offline. A larger extract, `dhaka_greater_metro_roads.osm`, which
also covers the north (Uttara, the airport), can be selected in the dashboard.

**Construction pipeline and measured counts** (central extract):

| stage | nodes | directed edges |
|------|------:|---------------:|
| raw OSM file | 106,637 | (20,969 ways) |
| drivable directed graph | 96,222 | 191,415 |
| after geometry-preserving simplification | 30,848 | 74,030 |
| largest strongly connected component (used) | **30,444** | **73,443** |

18,978 ways are kept. 1,974 are excluded as not drivable (footway, path,
pedestrian, steps) or closed to private cars by `access`, `vehicle`,
`motor_vehicle` or `motorcar` tags. 17 `highway=construction` ways are treated
as closed.

Routing uses the largest **strongly** connected component, where every
junction can reach every other one by legal driving. Weak connectivity is not
enough: one-way streets cut off at the edge of the extract create junctions
you can enter but never leave. The strong component keeps 98.7% of the
simplified nodes. A smaller **central demo region** (3,125 nodes / 6,925
edges) is provided so that DFS and IDS can finish.

Construction choices:
* **Directed** edges honour `oneway=yes/-1` and OSM's default one-way rules
  for motorways, ramps and roundabouts.
* Edge length is the sum of haversine distances along the road **geometry**,
  not the straight line between endpoints.
* **Simplification** folds a node into its road only if it is a pure shape
  point: exactly two neighbours, one OSM way, and straight-through traffic.
  Each original segment is absorbed exactly once, so connectivity (79 weak
  components before and after), direction, geometry and road names are
  preserved. Total length is preserved except for 67 loops (11.7 km) that
  leave and return to the same junction; they cannot be part of a shortest
  route and are dropped. A unit test checks this.
* Parallel edges are kept, and each edge has a unique id, so every route
  names the exact edges it uses.
* The processed graph is cached on disk, keyed by file size, modification
  time and a code version number.

**Limitations.** OSM turn restrictions (`type=restriction` relations) are not
modelled, so a route may include a turn that is banned in reality. Unnamed
roads get a label such as `(unnamed residential 8656088)` instead of an
invented name.

---

## 3. Simulated dynamic attributes

OSM does not provide live congestion, accidents, flooding or crime data. Every
such value is **simulated** by a seeded generator, reproducible for a given
scenario, seed and edge. Road class only shapes the assumptions: arterials get
more traffic, small roads rougher surfaces, and night raises safety penalties.
The scenarios are `normal_day`, `rush_hour`, `rain_flood`,
`construction_accident`, `night`, and a blank `manual` scenario. A scenario is
**frozen** before a comparison, so all six algorithms see identical costs.

---

## 4. Unified route-cost function

For a directed edge `(u,v)`:

```
cost(u,v) = D(u,v) × [ 1 + wT·(T−1) + wC·(C−1) + wS·(S−1) ]
```

`D` is length in km, `T∈[1,5]` traffic, `C∈[1,5]` road condition, `S∈[1,4]`
the day or night safety penalty. The weights satisfy `wT+wC+wS=1`, all ≥ 0. A
closed road costs `+∞`. Default weights: `wT=0.5, wC=0.3, wS=0.2`.

**Worked example (daytime):** `D=2 km, T=4, C=2, S_day=3`
```
multiplier = 1 + 0.5·3 + 0.3·1 + 0.2·2 = 1 + 1.5 + 0.3 + 0.4 = 3.2
cost       = 2 × 3.2 = 6.4
```
The physical length stays 2 km; the **weighted cost** is 6.4. The output is a
*generalized weighted route cost*, not travel time, money or accident
probability.

---

## 5. Algorithms, heuristic and results

All six algorithms share one definition: a node is **expanded** when it is
removed from the frontier and processed. The goal counts when it is removed.
Stale priority-queue entries are discarded without counting. BFS, DFS and IDS
search by structure and do **not** minimise weighted cost. Their routes are
still scored with the same cost function, and those routes usually do not
change when only penalties change; only closures affect them.

**Heuristic.** `h(n) = haversine_km(n, goal) × 1`. Every edge's cost is at
least its length, its length is at least the straight-line distance between
its endpoints, and haversine distance obeys the triangle inequality. So
`h(u) ≤ cost(u,v) + h(v)` (consistent) and `h` never overestimates
(admissible). Checked on all 73,443 edges of the real graph: 0 violations of
either property, and `h(goal) = 0`.

### Measured results

The data is in `outputs/benchmark_region.csv` (all six algorithms, 3 trips × 3
scenarios on the demo region) and `outputs/benchmark_full.csv` (BFS, UCS, GBFS
and A\*, 4 trips × 4 scenarios on the full network, median of 3 timings).

Example: demo region, `normal_day`, trip `2432078557 → 3940938359`.

| algo | status | km | weighted cost | hops | expanded | ms | cost ÷ UCS |
|------|--------|---:|--------------:|-----:|---------:|---:|-----------:|
| BFS  | success | 2.459 | 4.753 | **43** | 2,084 | 7.6 | 1.17 |
| DFS  | success | 26.331 | 49.586 | 422 | 655 | 2.0 | 12.22 |
| IDS  | **resource_limit** | — | — | — | 1,500,000 | 5,808 | — |
| UCS  | success | **2.217** | **4.059** | 50 | 952 | 4.9 | 1.00 |
| GBFS | success | 2.223 | 4.148 | 48 | **59** | **0.6** | 1.02 |
| A\*  | success | **2.217** | **4.059** | 50 | 563 | 3.9 | 1.00 |

Across all runs:

| | demo region (9 runs) | full network (16 runs) |
|---|---|---|
| UCS and A\* same optimal cost | 9 / 9 | 16 / 16 |
| A\* expansions ÷ UCS expansions (median) | 0.62 | 0.64 |
| median search time, A\* vs UCS | 3.9 ms vs 4.9 ms | 192 ms vs 205 ms |
| GBFS cost ÷ optimal (median) | 1.16 | 1.37 |
| BFS cost ÷ optimal (median) | 1.14 | 1.21 |
| DFS cost ÷ optimal (median) | 12.2 | not run |
| IDS finished | 3 / 9 | not run |

Observations, all from these runs:
* **UCS and A\* always found the same optimal cost**, as the consistent
  heuristic guarantees. A\* expanded 21–57% fewer nodes in every run (median
  about 37% fewer).
* **Fewer expansions gave a smaller time saving.** A\* was faster at the
  median, but by much less than its expansion saving (192 ms vs 205 ms on the
  full network), because each heuristic evaluation costs time. Single-run
  timings vary on a laptop, so only the median is meaningful here.
* **GBFS is by far the cheapest search** (median 59 expansions on the region
  and 239 on the full network, a few milliseconds), but its routes cost more
  than optimal: median 16% more on the region and 37% more on the full
  network.
* **BFS finds the fewest hops, not the cheapest route.** In the example it
  uses 43 edges against UCS's 50 but costs 17% more. Fewest edges ≠ fewest
  kilometres ≠ lowest cost. Fewest edges also does not mean fewest turns,
  because a graph edge can include several bends and a turn can happen with
  no node in between.
* **DFS returns valid but very poor routes**: 26 km for a 2.2 km trip.
* **IDS is correct but very expensive.** It finished on 3 runs, all for the
  same 19-hop trip, and found a minimum-hop route like BFS's. That took
  41,656–152,746 expansions, where BFS needed 299–365. (It needed fewer in
  `rain_flood`, because flooded roads were closed and removed paths.) On the
  other 6 runs it hit the 1.5 million expansion budget and is reported as
  `resource_limit`, **not** as a success.

### Longer physical route, lower weighted cost (reproducible)

Demo region, `normal_day`, trip `2432078557 → 3940938359`, with a simulated jam
(traffic 5, condition 4) on the physically shortest route. That route is
**2.208 km** and, once jammed, costs **8.897**. The minimum-cost route found by
UCS is **2.774 km (0.566 km longer)** but costs **5.391**, about 40% less.
Under congestion, the shortest road is not the best one. The
`Scenario.jam_edges` method reproduces this.

---

## 6. "Dynamic" means repeated replanning

Each comparison freezes one scenario. When the user changes congestion,
weather, safety mode, weights or closures, the selected searches run again on
the new snapshot. The dashboard can block the road ahead of a vehicle halfway
along its route and replan from that position. This is **repeated classical
search**, not an incremental algorithm such as D\*.

---

## 7. Large graphs and complexity

Classical search-tree bounds such as `O(b^d)` describe an idealised tree. The
implementations here are **graph searches**: BFS, DFS, UCS, GBFS and A\* keep
a visited or closed set, so each node is expanded at most once and the work is
bounded by the graph's size plus priority-queue costs (`O(E log V)` for UCS and
A\*). IDS is the exception: it keeps only the current path, so it re-expands
shallow nodes in every iteration and explores every simple path up to the
depth limit. On a cyclic road network that grows exponentially with depth,
which is why IDS ran out of budget. DFS finishes quickly but follows long
detours, because nothing steers it toward the goal.

Every search is therefore bounded by a timeout, an expansion budget and (for
IDS) a maximum depth. A search that hits a limit is reported as
`resource_limit`, which is distinct from `no_path`.

---

## 8. Limitations

* Traffic, condition, safety and incident values are **simulated**; they are
  reproducible but are not facts about real locations.
* OSM turn restrictions are not modelled.
* Single-vehicle routing only; no interaction between vehicles.
* Cancelling: Streamlit's Stop button stops between algorithms. A single
  search is bounded by its timeout and budget rather than interrupted midway.
* No number in this report was invented. All come from runs reproducible with
  the commands in the README.

---

## 9. Conclusion

On a real 30,444-node Dhaka road graph, A\* with a consistent haversine
heuristic matched UCS's optimal weighted cost in every run while expanding
about 37% fewer nodes (median), though its time saving was smaller than that.
GBFS is fastest but suboptimal. BFS and IDS optimise hops rather than cost,
and IDS does not scale. DFS is simple but produces long, costly routes. The
weighted cost function shows the practical point: under congestion, a longer
road can be the better route.
