# Likely viva questions — with simple answers

**Q1. Why is A\* optimal here?**
Because the heuristic is **admissible** (never overestimates the true remaining
cost) and **consistent** (`h(u) ≤ cost(u,v) + h(v)`). With a consistent
heuristic and goal-test on pop, A\* returns a least-cost path. The validator in
`heuristics.py` checks this on the real graph (0 violations).

**Q2. Why do BFS and UCS sometimes return different routes?**
BFS minimises the **number of hops/edges**; UCS minimises **weighted cost**. The
fewest-edges path can be physically longer and more congested. They only agree
when the min-hop path also happens to be min-cost.

**Q3. Isn't BFS optimal for weighted graphs?**
No. BFS is optimal only when every step has equal cost. Our edges have different
lengths and penalties, so BFS is optimal in *hops*, not in cost. UCS/A\* handle
weighted costs.

**Q4. Why does IDS expand so many nodes?**
Iterative deepening re-runs a depth-limited DFS for depth 0, 1, 2, … and throws
away everything between iterations. Shallow nodes are regenerated at every
iteration, so on a graph with long solutions the work grows very fast. It uses
little memory, which is its advantage.

**Q5. How is the heuristic computed — why not Euclidean on lat/lon?**
Latitude/longitude are **degrees**, not metres; subtracting them and calling it
distance is wrong (a degree of longitude in Dhaka ≈ 102 km, a degree of latitude
≈ 111 km, and they are not interchangeable). We use the **haversine** great-
circle distance in km.

**Q6. What does "cost" mean — is it minutes?**
No. It is a **generalized weighted route cost** in the same units as distance
when all penalties are 1. It is not time, money or accident probability; it is a
single comparable number blending distance, traffic, condition and safety.

**Q7. How do you handle one-way streets?**
During graph construction we read the `oneway` tag (`yes`, `-1`/reverse, `no`)
and the default-oneway convention for motorways/ramps/roundabouts, and only add
the directed edges that are legally traversable.

**Q8. What happens when a road is closed?**
Its edge cost is `+∞`. Every one of the six algorithms skips it, so no returned
route uses a closed road. Closing a road and re-running demonstrates replanning.

**Q9. Is the traffic/flood data real?**
No — it is **simulated** with a seeded, reproducible generator. Only the road
geometry is real (OpenStreetMap). This is stated throughout the UI and report.

**Q10. What is "expanded" vs "generated"?**
*Generated* = a successor pushed onto the frontier. *Expanded* = a node popped
and processed (its successors generated). We count the goal as expanded when
popped, and ignore stale priority-queue entries.

**Q11. Why simplify the graph? Do you lose accuracy?**
We merge pure shape points (nodes with exactly two neighbours on a single OSM
way, with traffic flowing straight through) into one edge, keeping the full
geometry, length and direction. Intersections and points where two ways meet
are kept. Connectivity is unchanged (79 components before and after), and the
only length removed is 67 loops (11.7 km) that return to their own junction,
which no shortest route can use. Search gets much faster: 96,222 → 30,848
nodes. A unit test checks that length, connectivity and direction are
preserved.

**Q12. How is "dynamic" handled — is this D\*?**
No. We freeze a scenario, run the searches, and when conditions change we re-run
(replan) on the new snapshot. It is repeated classical search, which we state
explicitly rather than claiming an incremental algorithm.

**Q13. Does A\* always beat UCS in speed/expansions?**
Not necessarily. In our runs A\* always expanded fewer nodes than UCS (21–57%
fewer, median about 37%). But each heuristic evaluation costs time, so its time
saving was much smaller: 192 ms vs 205 ms median on the full network. In an
earlier run, before a graph fix, A\* was even slightly slower on the small
region. We report the measurements rather than assume a ranking.

**Q14. How do you prove you didn't fabricate results?**
Every number comes from `benchmark.py` / the dashboard and is reproducible with
the README commands; CSVs and plots are in `outputs/`. Unsuccessful and
resource-limited searches are reported as such (e.g. IDS `resource_limit`).

**Q15. What are the main limitations?**
Simulated dynamic data; no OSM turn restrictions; single-vehicle routing;
DFS/IDS impractical on the full network (bounded by resource limits);
cancelling stops between algorithms, not inside one search.
