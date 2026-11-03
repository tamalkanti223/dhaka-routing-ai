# Intelligent Vehicle Routing in Dhaka — Classical AI Search

An AI Lab term project that compares **six classical search algorithms**
(BFS, DFS, IDS, UCS, Greedy Best-First and A\*) on a **real Dhaka road
network** loaded from a local OpenStreetMap extract, under dynamic (simulated)
traffic, road-condition, safety and closure scenarios.

> **Data honesty.** The road network is real (OpenStreetMap, exported via the
> Overpass API, ODbL licence). Traffic, road condition, safety, flooding,
> construction and accident values are **simulated** with a reproducible
> seeded generator. They are *not* live data or facts about any neighbourhood.

---

## 1. What it does

* Loads `~/Downloads/dhaka_central_roads.osm` (or any `.osm` file) into a
  **directed, weighted** road graph that respects one-way streets, preserves
  road geometry and is cached on disk. The central extract gives
  30,444 junctions and 73,443 road edges.
* Scores every edge with a **unified weighted cost function** combining
  distance, traffic, road condition and day/night safety.
* Runs all six algorithms with one **result format**: path, cost, distance,
  hop count, nodes expanded and generated, peak frontier, runtime and status.
* Includes an interactive **Streamlit + Folium dashboard**. You can pick a
  start and destination by clicking the map or searching 34,000+ entries, one
  for every road, landmark and junction (nothing is left out),
  places, choose a scenario, close roads, run one algorithm or *Compare All
  Six*, watch every algorithm explore the map at once in the **Simulation**
  tab (or replay one on its own), and export a CSV or the map.
* Ships a **benchmark script**, **charts** and a **pytest** suite.

---

## 2. Project layout

```
dhaka-routing-ai/
├── app.py                 # Streamlit dashboard
├── config.py              # paths, weights, limits, demo region, seed
├── graph_data.py          # OSM XML loader, simplification, cache, place search
├── cost_function.py       # unified weighted route-cost function
├── search_algorithms.py   # BFS, DFS, IDS, UCS, GBFS, A* (explicit)
├── heuristics.py          # haversine heuristic + admissibility/consistency check
├── scenarios.py           # reproducible seeded dynamic edge attributes
├── benchmark.py           # multi-trip / multi-scenario benchmarking + charts
├── visualizer.py          # Folium map, Plotly charts and offline chart replay
├── simulation.py          # on-map simulation page: all algorithms exploring together
├── path_view.py           # one algorithm's path, node by node, beside the map
├── requirements.txt
├── README.md / REPORT.md / DEMO.md / VIVA.md
├── tests/test_core.py     # 23 correctness tests on tiny synthetic graphs
└── outputs/               # benchmark CSVs, charts, example map and replay
```

---

## 3. Setup — Arch Linux (Python virtual environment)

```bash
sudo pacman -S --needed python

cd ~/dhaka-routing-ai            # wherever you put this project
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

ls ~/Downloads/dhaka_central_roads.osm   # or pass --osm PATH
```

> **Windows note (this laptop):** the default `python` here is a 32-bit build
> that cannot install pandas. Use the 64-bit launcher: put `py -3.12 -m` in
> front of each command (for example `py -3.12 -m streamlit run app.py ...`),
> or create the venv with `py -3.12 -m venv .venv`.

---

## 4. Launch the dashboard

```bash
streamlit run app.py -- --osm ~/Downloads/dhaka_central_roads.osm
# open the printed Local URL (http://localhost:8501)
```

The `--` separates Streamlit's flags from the app's own flags:

| flag | meaning |
|------|---------|
| `--osm PATH` | use a different `.osm` file |
| `--no-simplify` | keep every OSM shape node (bigger, slower graph) |

The map is the central-Dhaka extract (`data/dhaka_central_roads.osm`), about
21 km x 21 km: from Mirpur and the southern edge of Uttara down to Old Dhaka
and Jatrabari, and from Mohammadpur east to Badda, Rampura and Khilgaon.
Outer areas such as Savar, Tongi, Gazipur and Narayanganj are not included.
The **📍 Coverage** view (next to Map / Simulation / Path) shows this on the map:
the road footprint, the four extreme junctions and which well-known areas are
covered, only partly covered or absent, all counted from the loaded graph.

Using the app:
1. **Network:** *Full network* (whole map file) or the small *Central demo
   region*, where DFS and IDS can finish.
2. **Choose the trip:** type in the **From** and **To** boxes (they search every
   road, landmark such as *Dhaka University*, Bangla names, and every junction
   by its coordinates), or **click the map**: 1st click = start, 2nd =
   destination, 3rd starts a new trip. Routes appear automatically.
3. Choose a **scenario**, toggle **night**, and adjust the **weights**.
4. **▶ Run** (the algorithms ticked below it) or **⚖ All six**.
5. The view switches to **🎬 Simulation** and starts playing by itself (the
   Map / Simulation switch is above the map). Every algorithm that ran
   explores the real map in its own colour. *Race* mode advances all of them
   by the same number of expansions; *Normalized* makes them finish together.
   Tick or untick an algorithm, or press **only** to watch just one. The
   Compare tab has the table and charts.
6. **🧭 Path** (above the map) shows one algorithm's route with every node of
   it listed in order beside the map (road name, km from the start). Point at
   a node in the list to see it on the map; click to zoom. Running a single
   algorithm with **▶ Run** opens this view by itself.
7. In **Edit roads**, pick any stretch of the current route (**Part of current
   route**, by default all of it) and raise or lower its traffic, condition and
   safety at once, or tick conditions for that stretch only (road closed, rush hour, rain,
   flood, deep flood = closed, construction = closed, accident, night) and
   remove any of them later from **Your changed stretches**; or close a road or add an incident and run again to see the
   routes replan. **Reset road edits** in the sidebar undoes edits.

---

## 5. Reproduce the benchmarks

These are the exact commands that produced `outputs/`:

```bash
# all six on the central demo region (DFS/IDS tractable)
python benchmark.py --region --pairs 3 --min-km 0.6 --seed 11 --repeats 1 \
    --ids-depth 80 --budget 1500000 --timeout 20 \
    --scenarios normal_day rush_hour rain_flood --out outputs/benchmark_region.csv

# the four scalable algorithms on the full network, median of 3 timings
python benchmark.py --algorithms BFS UCS GBFS "A*" --pairs 4 --min-km 4 --seed 5 \
    --repeats 3 --scenarios normal_day rush_hour rain_flood night \
    --out outputs/benchmark_full.csv --no-plots
```

The first command also writes `chart_cost.png`, `chart_expanded.png`,
`chart_runtime.png` and `chart_distance.png`. Search time excludes file
loading, rendering and export.

---

## 6. Run the tests

```bash
pytest -q          # 23 tests on tiny synthetic graphs
```

---

## 7. Inspect the graph only

```bash
python graph_data.py --osm ~/Downloads/dhaka_central_roads.osm
```

Prints node and edge counts before and after simplification, components,
dropped loops and build time.

---

## 8. Offline use

Graph loading, all six algorithms, the benchmarks and the charts use only the
Python standard library and bundled components, so they need no internet.
**The two map views do need internet**: the Leaflet script they use is loaded
from a CDN, and the tiles come from OpenStreetMap. The **Offline map** toggle
only skips the tiles. Without internet, use **Offline replay** in the
Simulation view, a plain chart of one algorithm's search that needs neither.

---

## 9. Known limitations

* Dynamic road values are simulated.
* OSM turn restrictions are not modelled.
* Streamlit's Stop button cancels between algorithms. A single running search
  is bounded by its timeout and expansion budget rather than interrupted.
* ⚖ All six takes a few seconds because IDS runs until it finishes or
  reaches its expansion budget (sidebar). That is IDS's nature on a big map.
* The simulation draws expanded nodes, not frontier nodes, and thins very
  large searches to 2,500 dots per algorithm (step counts stay exact).
* Clicking, searching, auto-run, the simulation controls and map zoom
  behaviour were tested in a real Chrome browser (Playwright), including a
  1366×768 laptop-sized window.
* On a small screen, lower **Display → Map height** in the sidebar; on a big
  screen, raise it.
