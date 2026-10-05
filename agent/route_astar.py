"""Exact A* ordering of a small day's stops, including the return leg."""
from __future__ import annotations

import heapq
from functools import lru_cache


def order_stops(member_indices: list[int], distance_matrix) -> list[int]:
    nodes = tuple(member_indices)
    n = len(nodes)
    if n < 2:
        return list(nodes)
    full = (1 << n) - 1

    def leg(a: int, b: int) -> float:
        # -1 is the hotel; all other indices refer to `nodes`.
        return distance_matrix.between(0 if a == -1 else nodes[a] + 1,
                                       0 if b == -1 else nodes[b] + 1)

    @lru_cache(None)
    def mst(mask: int) -> float:
        remaining = [i for i in range(n) if mask & (1 << i)]
        if len(remaining) < 2:
            return 0.0
        seen = {remaining.pop(0)}
        total = 0.0
        while remaining:
            cost, chosen = min((min(leg(i, j), leg(j, i)), j)
                               for i in seen for j in remaining)
            total += cost
            seen.add(chosen)
            remaining.remove(chosen)
        return total

    def heuristic(mask: int, last: int) -> float:
        remaining_mask = full ^ mask
        if not remaining_mask:
            return leg(last, -1)
        remaining = [i for i in range(n) if remaining_mask & (1 << i)]
        return (min(leg(last, i) for i in remaining)
                + mst(remaining_mask)
                + min(leg(i, -1) for i in remaining))

    queue = [(heuristic(0, -1), 0.0, 0, -1, ())]
    best = {(0, -1): 0.0}
    while queue:
        _, cost, mask, last, path = heapq.heappop(queue)
        if cost > best.get((mask, last), float("inf")) + 1e-9:
            continue
        if mask == full:
            return [nodes[i] for i in path]
        for i in range(n):
            bit = 1 << i
            if mask & bit:
                continue
            next_mask = mask | bit
            next_cost = cost + leg(last, i)
            key = (next_mask, i)
            if next_cost < best.get(key, float("inf")) - 1e-9:
                best[key] = next_cost
                heapq.heappush(queue, (next_cost + heuristic(next_mask, i),
                                       next_cost, next_mask, i, path + (i,)))
    return list(nodes)
