"""
search_algorithms.py — explicit implementations of the six classical search
algorithms on the directed, weighted road graph.

All six share a single result structure and a single notion of "expanded":

    A node is EXPANDED when it is removed from the frontier for processing.
    The goal counts as an expansion when it is popped (we goal-test on pop so
    that UCS/A* optimality holds and the definition is uniform across all six).
    Stale priority-queue entries (a node already finalised) are popped and
    discarded WITHOUT counting as an expansion.

Counters returned:
    nodes_expanded          total pops that were actually expanded (incl. goal)
    unique_states_expanded  number of distinct nodes expanded
    nodes_generated         successors pushed onto the frontier (with dups)
    peak_frontier           max frontier size observed (where meaningful)

BFS, DFS and IDS are *uninformed*: they search over graph structure and do NOT
optimise the weighted cost.  Their returned path is still evaluated with the
same unified cost function so every algorithm is scored identically.  Changing
penalty values therefore often leaves their chosen route unchanged.

Closed roads (edge_cost == inf) are never traversable by any of the six.
"""

from __future__ import annotations

import heapq
import math
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional, Tuple

from config import Limits, Weights
from cost_function import EdgeAttr, edge_cost
from graph_data import Edge, RoadGraph
from heuristics import make_heuristic

INF = math.inf

# Max recorded animation events per search. IDS on the full network can expand
# millions of nodes; recording stops here (search continues) to bound memory.
TRACE_CAP = 200_000


class Status(str, Enum):
    SUCCESS = "success"
    NO_PATH = "no_path"                 # search exhausted, destination unreachable
    RESOURCE_LIMIT = "resource_limit"   # hit timeout / expansion / depth budget
    START_IS_GOAL = "start_is_goal"


@dataclass
class SearchResult:
    algorithm: str
    status: Status
    node_path: List[int] = field(default_factory=list)
    edge_path: List[Edge] = field(default_factory=list)
    distance_km: float = 0.0            # physical length of the chosen path
    weighted_cost: float = 0.0          # unified weighted cost of the chosen path
    hops: int = 0
    nodes_expanded: int = 0
    unique_states_expanded: int = 0
    nodes_generated: int = 0
    peak_frontier: int = 0
    time_ms: float = 0.0
    trace: List[Tuple[str, int]] = field(default_factory=list)  # ("expand"|"generate", node)
    extra: dict = field(default_factory=dict)

    @property
    def found(self) -> bool:
        return self.status == Status.SUCCESS

    def record(self, event: Tuple[str, int]) -> None:
        """Append an animation event, up to TRACE_CAP."""
        if len(self.trace) < TRACE_CAP:
            self.trace.append(event)
        else:
            self.extra["trace_truncated"] = True

    def one_line(self) -> str:
        if self.found:
            return (f"{self.algorithm:5s} OK   "
                    f"dist={self.distance_km:7.3f}km  cost={self.weighted_cost:8.3f}  "
                    f"hops={self.hops:4d}  exp={self.nodes_expanded:7d}  "
                    f"gen={self.nodes_generated:8d}  t={self.time_ms:7.2f}ms")
        return (f"{self.algorithm:5s} {self.status.value.upper():14s}  "
                f"exp={self.nodes_expanded:7d}  t={self.time_ms:7.2f}ms")


# --------------------------------------------------------------------------- #
# Problem instance: a frozen snapshot all algorithms share
# --------------------------------------------------------------------------- #

class ProblemInstance:
    """Bundles the graph with a frozen scenario, weights and time-of-day."""

    def __init__(self, graph: RoadGraph, attr_of: Callable[[int], EdgeAttr],
                 weights: Weights, night: bool, limits: Limits):
        self.graph = graph
        self.attr_of = attr_of
        self.weights = weights
        self.night = night
        self.limits = limits
        # Deterministic neighbour ordering: sort by (target node, edge id).
        self._sorted_adj: Dict[int, List[Edge]] = {}

    def successors(self, u: int) -> List[Edge]:
        """Open (non-closed) outgoing edges in deterministic order."""
        cached = self._sorted_adj.get(u)
        if cached is None:
            cached = sorted(self.graph.neighbors(u), key=lambda e: (e.v, e.eid))
            self._sorted_adj[u] = cached
        return [e for e in cached if not self.attr_of(e.eid).closed]

    def cost(self, e: Edge) -> float:
        return edge_cost(e, self.attr_of(e.eid), self.weights, self.night)


# --------------------------------------------------------------------------- #
# Path reconstruction & scoring
# --------------------------------------------------------------------------- #

def _reconstruct(came_from: Dict[int, Tuple[int, Edge]], start: int, goal: int
                 ) -> Tuple[List[int], List[Edge]]:
    node_path = [goal]
    edge_path: List[Edge] = []
    cur = goal
    while cur != start:
        prev, edge = came_from[cur]
        edge_path.append(edge)
        node_path.append(prev)
        cur = prev
    node_path.reverse()
    edge_path.reverse()
    return node_path, edge_path


def _score(prob: ProblemInstance, edge_path: List[Edge]) -> Tuple[float, float]:
    """Return (physical_distance_km, weighted_cost) for a path's edges."""
    dist = sum(e.length_km for e in edge_path)
    cost = sum(prob.cost(e) for e in edge_path)
    return dist, cost


def _finish(prob, res: SearchResult, came_from, start, goal) -> SearchResult:
    node_path, edge_path = _reconstruct(came_from, start, goal)
    res.node_path = node_path
    res.edge_path = edge_path
    res.hops = len(edge_path)
    res.distance_km, res.weighted_cost = _score(prob, edge_path)
    res.status = Status.SUCCESS
    return res


# --------------------------------------------------------------------------- #
# Breadth-First Search (min hops)
# --------------------------------------------------------------------------- #

def bfs(prob: ProblemInstance, start: int, goal: int, record_trace: bool = False
        ) -> SearchResult:
    res = SearchResult(algorithm="BFS", status=Status.NO_PATH)
    t0 = time.perf_counter()
    lim = prob.limits
    if start == goal:
        return _start_is_goal(res)

    frontier = deque([start])
    came_from: Dict[int, Tuple[int, Edge]] = {}
    discovered = {start}
    expanded_set = set()
    check = 0

    while frontier:
        res.peak_frontier = max(res.peak_frontier, len(frontier))
        u = frontier.popleft()
        res.nodes_expanded += 1
        expanded_set.add(u)
        if record_trace:
            res.record(("expand", u))
        if u == goal:
            res.unique_states_expanded = len(expanded_set)
            res.time_ms = (time.perf_counter() - t0) * 1000
            return _finish(prob, res, came_from, start, goal)

        check += 1
        if _over_budget(prob, res, t0, check):
            res.status = Status.RESOURCE_LIMIT
            break

        for e in prob.successors(u):
            v = e.v
            if v not in discovered:
                discovered.add(v)
                came_from[v] = (u, e)
                frontier.append(v)
                res.nodes_generated += 1
                if record_trace:
                    res.record(("generate", v))

    res.unique_states_expanded = len(expanded_set)
    res.time_ms = (time.perf_counter() - t0) * 1000
    return res


# --------------------------------------------------------------------------- #
# Depth-First Search (no cost guarantee)
# --------------------------------------------------------------------------- #

def dfs(prob: ProblemInstance, start: int, goal: int, record_trace: bool = False
        ) -> SearchResult:
    res = SearchResult(algorithm="DFS", status=Status.NO_PATH)
    t0 = time.perf_counter()
    if start == goal:
        return _start_is_goal(res)

    # Iterative graph-search DFS (visited set) to avoid recursion-depth failures.
    # Stack entries carry (node, parent, edge) so the parent is recorded when
    # the node is actually expanded, giving the true DFS-tree path.
    stack: List[Tuple[int, Optional[int], Optional[Edge]]] = [(start, None, None)]
    came_from: Dict[int, Tuple[int, Edge]] = {}
    visited = set()
    expanded_set = set()
    check = 0

    while stack:
        res.peak_frontier = max(res.peak_frontier, len(stack))
        u, parent, via = stack.pop()
        if u in visited:
            continue
        visited.add(u)
        if parent is not None:
            came_from[u] = (parent, via)
        res.nodes_expanded += 1
        expanded_set.add(u)
        if record_trace:
            res.record(("expand", u))
        if u == goal:
            res.unique_states_expanded = len(expanded_set)
            res.time_ms = (time.perf_counter() - t0) * 1000
            return _finish(prob, res, came_from, start, goal)

        check += 1
        if _over_budget(prob, res, t0, check):
            res.status = Status.RESOURCE_LIMIT
            break

        # Push in reverse sorted order so sorted successors are popped first.
        for e in reversed(prob.successors(u)):
            v = e.v
            if v not in visited:
                stack.append((v, u, e))
                res.nodes_generated += 1
                if record_trace:
                    res.record(("generate", v))

    res.unique_states_expanded = len(expanded_set)
    res.time_ms = (time.perf_counter() - t0) * 1000
    return res


# --------------------------------------------------------------------------- #
# Iterative Deepening Search (min hops when it completes)
# --------------------------------------------------------------------------- #

def ids(prob: ProblemInstance, start: int, goal: int, record_trace: bool = False
        ) -> SearchResult:
    res = SearchResult(algorithm="IDS", status=Status.NO_PATH)
    t0 = time.perf_counter()
    lim = prob.limits
    if start == goal:
        return _start_is_goal(res)

    total_expanded = 0
    total_generated = 0
    peak_frontier = 0
    expanded_union = set()
    check_box = {"n": 0}

    def dls(limit_depth: int):
        """
        Iterative depth-limited DFS with path-based cycle prevention.
        Returns (found_bool, came_from, cutoff_bool). Counts into nonlocal totals.
        """
        nonlocal total_expanded, total_generated, peak_frontier
        came_from: Dict[int, Tuple[int, Edge]] = {}
        cutoff = False
        # frame layout: [node, depth, successor_index, successor_list, edge_used]
        stack: List[list] = [[start, 0, 0, None, None]]
        on_path = {start}
        while stack:
            peak_frontier = max(peak_frontier, len(stack))
            frame = stack[-1]
            node, depth, idx, succ = frame[0], frame[1], frame[2], frame[3]
            if succ is None:
                # First visit to this frame: expand the node.
                total_expanded += 1
                res.nodes_expanded = total_expanded    # live, so budgets apply
                expanded_union.add(node)
                if record_trace:
                    res.record(("expand", node))
                if node == goal:
                    # Rebuild came_from from the current path frames.
                    return True, _came_from_from_stack(stack), cutoff
                check_box["n"] += 1
                if _over_budget(prob, res, t0, check_box["n"]):
                    res.status = Status.RESOURCE_LIMIT
                    return False, came_from, cutoff
                if depth == limit_depth:
                    # Only a real cutoff if the limit hid somewhere to go;
                    # a dead end at the limit is not a cutoff.
                    if any(x.v not in on_path for x in prob.successors(node)):
                        cutoff = True
                    stack.pop()
                    on_path.discard(node)
                    continue
                frame[3] = prob.successors(node)
                succ = frame[3]
            # advance to next unexpanded successor not on current path
            advanced = False
            lst = frame[3]
            while frame[2] < len(lst):
                e = lst[frame[2]]
                frame[2] += 1
                if e.v in on_path:
                    continue
                total_generated += 1
                res.nodes_generated = total_generated
                if record_trace:
                    res.record(("generate", e.v))
                # index 4 holds the edge used to reach this child
                child = [e.v, depth + 1, 0, None, e]
                stack.append(child)
                on_path.add(e.v)
                advanced = True
                break
            if not advanced:
                stack.pop()
                on_path.discard(node)
        return False, came_from, cutoff

    def _came_from_from_stack(stack):
        cf: Dict[int, Tuple[int, Edge]] = {}
        for i in range(1, len(stack)):
            prev = stack[i - 1][0]
            cur = stack[i][0]
            edge = stack[i][4]
            cf[cur] = (prev, edge)
        return cf

    max_depth = lim.ids_max_depth
    for depth in range(0, max_depth + 1):
        found, came_from, cutoff = dls(depth)
        if res.status == Status.RESOURCE_LIMIT:
            break
        if found:
            res.nodes_expanded = total_expanded
            res.unique_states_expanded = len(expanded_union)
            res.nodes_generated = total_generated
            res.peak_frontier = peak_frontier
            res.extra["final_depth"] = depth
            res.time_ms = (time.perf_counter() - t0) * 1000
            return _finish(prob, res, came_from, start, goal)
        if not cutoff:
            # No cutoff at this depth => whole space explored, truly no path.
            res.status = Status.NO_PATH
            break
    else:
        # Loop finished without break => depth budget exhausted.
        res.status = Status.RESOURCE_LIMIT
        res.extra["reason"] = "ids_max_depth reached"

    res.nodes_expanded = total_expanded
    res.unique_states_expanded = len(expanded_union)
    res.nodes_generated = total_generated
    res.peak_frontier = peak_frontier
    res.time_ms = (time.perf_counter() - t0) * 1000
    return res


# --------------------------------------------------------------------------- #
# Uniform Cost Search (optimal on weighted cost)
# --------------------------------------------------------------------------- #

def ucs(prob: ProblemInstance, start: int, goal: int, record_trace: bool = False
        ) -> SearchResult:
    return _best_first(prob, start, goal, "UCS", use_g=True, use_h=False,
                       record_trace=record_trace)


# --------------------------------------------------------------------------- #
# Greedy Best-First Search (h only, no guarantee)
# --------------------------------------------------------------------------- #

def gbfs(prob: ProblemInstance, start: int, goal: int, record_trace: bool = False
         ) -> SearchResult:
    return _best_first(prob, start, goal, "GBFS", use_g=False, use_h=True,
                       record_trace=record_trace)


# --------------------------------------------------------------------------- #
# A* Search (g + h, optimal with admissible/consistent h)
# --------------------------------------------------------------------------- #

def astar(prob: ProblemInstance, start: int, goal: int, record_trace: bool = False
          ) -> SearchResult:
    return _best_first(prob, start, goal, "A*", use_g=True, use_h=True,
                       record_trace=record_trace)


def _best_first(prob: ProblemInstance, start: int, goal: int, name: str,
                use_g: bool, use_h: bool, record_trace: bool) -> SearchResult:
    """Shared priority-queue engine for UCS / GBFS / A*."""
    res = SearchResult(algorithm=name, status=Status.NO_PATH)
    t0 = time.perf_counter()
    if start == goal:
        return _start_is_goal(res)

    h = make_heuristic(prob.graph, goal) if use_h else (lambda n: 0.0)

    g_score: Dict[int, float] = {start: 0.0}
    came_from: Dict[int, Tuple[int, Edge]] = {}
    finalized = set()           # nodes already expanded (closed set)
    expanded_set = set()
    counter = 0                 # tie-breaker for stable, deterministic ordering

    def priority(node: int, g: float) -> float:
        if use_g and use_h:
            return g + h(node)
        if use_g:
            return g
        return h(node)          # GBFS

    start_key = priority(start, 0.0)
    frontier: List[Tuple[float, int, int]] = [(start_key, counter, start)]
    check = 0

    while frontier:
        res.peak_frontier = max(res.peak_frontier, len(frontier))
        _, _, u = heapq.heappop(frontier)
        if u in finalized:
            continue            # stale entry: discard, do NOT count as expansion
        finalized.add(u)
        res.nodes_expanded += 1
        expanded_set.add(u)
        if record_trace:
            res.record(("expand", u))

        if u == goal:
            res.unique_states_expanded = len(expanded_set)
            res.time_ms = (time.perf_counter() - t0) * 1000
            res.extra["g_goal"] = g_score[u]
            return _finish(prob, res, came_from, start, goal)

        check += 1
        if _over_budget(prob, res, t0, check):
            res.status = Status.RESOURCE_LIMIT
            break

        gu = g_score[u]
        for e in prob.successors(u):
            v = e.v
            if v in finalized:
                continue
            step = prob.cost(e)
            if step == INF:
                continue
            tentative = gu + step
            if tentative < g_score.get(v, INF):
                g_score[v] = tentative
                came_from[v] = (u, e)
                counter += 1
                heapq.heappush(frontier, (priority(v, tentative), counter, v))
                res.nodes_generated += 1
                if record_trace:
                    res.record(("generate", v))

    res.unique_states_expanded = len(expanded_set)
    res.time_ms = (time.perf_counter() - t0) * 1000
    return res


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #

def _start_is_goal(res: SearchResult) -> SearchResult:
    res.status = Status.START_IS_GOAL
    res.node_path = []
    res.hops = 0
    res.distance_km = 0.0
    res.weighted_cost = 0.0
    return res


def _over_budget(prob: ProblemInstance, res: SearchResult, t0: float, check: int
                 ) -> bool:
    """Check resource limits periodically to keep overhead low."""
    lim = prob.limits
    if res.nodes_expanded >= lim.expansion_budget:
        res.extra["limit"] = "expansion_budget"
        return True
    if res.nodes_generated >= lim.node_generation_budget:
        res.extra["limit"] = "node_generation_budget"
        return True
    if (check & 0x3FF) == 0:  # every 1024 checks, test wall-clock
        if (time.perf_counter() - t0) >= lim.timeout_s:
            res.extra["limit"] = "timeout"
            return True
    return False


ALGORITHMS: Dict[str, Callable] = {
    "BFS": bfs,
    "DFS": dfs,
    "IDS": ids,
    "UCS": ucs,
    "GBFS": gbfs,
    "A*": astar,
}


def run_algorithm(name: str, prob: ProblemInstance, start: int, goal: int,
                  record_trace: bool = False) -> SearchResult:
    if name not in ALGORITHMS:
        raise ValueError(f"unknown algorithm '{name}'")
    return ALGORITHMS[name](prob, start, goal, record_trace=record_trace)
