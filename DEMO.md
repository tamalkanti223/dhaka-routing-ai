# Demo / presentation script (~6–8 minutes)

A step-by-step script for demonstrating the project to your teacher.

## 0. Before class
```bash
source .venv/bin/activate              # Arch Linux
streamlit run app.py -- --osm ~/Downloads/dhaka_central_roads.osm
```
Open `http://localhost:8501`. On this Windows laptop:
`py -3.12 -m streamlit run app.py -- --osm "$HOME/Downloads/dhaka_central_roads.osm"`.
Load the page once before class so the graph cache is warm.

## 1. The data (30 s)
> "The road network is **real** OpenStreetMap data for central Dhaka. About
> 106,000 raw OSM nodes become a directed drivable graph of 30,444 junctions
> and 73,443 road edges, with one-way streets respected."

Open **About / validation** and point at the graph statistics.

## 2. The cost model (45 s)
> "Each road has its physical length plus **simulated** traffic, condition and
> safety penalties. One cost function combines them."

Show the worked example on the About tab. Say clearly that these values are
simulated, not live data.

## 3. Pick a trip and run all six (90 s)
1. In the sidebar choose **Central demo region (small)** so DFS and IDS can
   finish.
2. In **From** type *Dhaka University* and press Enter, then in **To** type
   *Motijheel*. (Or click the map: 1st click = start, 2nd = destination.)
   Routes appear automatically.
3. Press **⚖ All six**. The simulation starts by itself (section 5); the
   numbers are in the **Compare** tab.

> "A\* and UCS find the **same optimal weighted cost**; the green banner
> confirms it. A\* expands fewer nodes. GBFS is far faster but about 16% more
> expensive. BFS finds the fewest road segments, not the cheapest route. DFS
> wanders tens of kilometres. IDS either finishes after tens of thousands of
> expansions (BFS needs a few hundred) or stops at its budget and says so; it
> is never reported as a success when it isn't."

Point at `weighted_cost`, `nodes_expanded`, `time_ms`, `cost_ratio_vs_UCS` and
`limit_hit`.

## 4. The heuristic is provably correct (30 s)
On **About**, press **Check heuristic admissibility & consistency**.
> "The heuristic is straight-line (haversine) distance. This checks every edge
> of the real graph: zero violations, so A\* is guaranteed optimal here."

## 5. Watch the searches on the map (60 s)
After **⚖ All six** the map switches to **🎬 Simulation** and plays by itself
(switch above the map: 🗺️ Map / 🎬 Simulation). Press **▶ Replay** to watch again.
> "Every algorithm explores the real map at once, each in its own colour. In
> *Race* mode they all advance the same number of expansions per step, so GBFS
> and A\* finish long before UCS and BFS, and DFS wanders far away. IDS never
> finishes within its budget and the panel says so. UCS spreads in all
> directions, A\* is pulled toward the goal, GBFS dives straight at it."

Press **only** next to A\*, then Play again, to show one algorithm alone. Switch
*Sync* to *Normalized* to make every search finish together. The replay uses
events recorded during the search, so it doesn't affect any timing.

## 6. Dynamic replanning — an incident (90 s)
1. Open **Edit roads** and press **Block the road ahead and replan from here**.
2. Return to **Map & Run**.
> "The vehicle is halfway along its route and the road ahead closes. A closed
> road has infinite cost, so every algorithm avoids it. We replan from the
> vehicle's position. This is repeated classical search, not D\*."

**Reset road edits** in the sidebar undoes it.

## 7. Longer road, lower cost (45 s)
> "In the report's experiment, the shortest route is 2.2 km. With a jam on it,
> the best route is 2.8 km, over half a kilometre longer, but costs about 40% less.
> The shortest road is not always the best one."

## 8. Export (20 s)
On **Compare**, click **Download comparison CSV**. Under the map, use
**Download this map (HTML)**. Each chart's camera icon saves a PNG.

## 9. Close
> "A\* is optimal and efficient, GBFS fast but greedy, UCS the optimal
> baseline, BFS and IDS optimise hops rather than cost, and DFS is a poor
> router. All results are reproducible, and the simulated data is labelled."
