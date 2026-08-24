"""Skeleton graph construction and conservative tail-fragment linking.

Only ``outputs/binary_mask.png`` and ``outputs/skeleton.png`` are read.  The
This module contains no head/tail association code.
"""
from __future__ import annotations

import argparse
import csv
import math
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


N8 = [(-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0),
      (-1, 1), (0, 1), (1, 1)]


def read_binary(path: Path) -> np.ndarray:
    im = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if im is None:
        raise FileNotFoundError(path)
    return im > 0


def crossing_number(skel: np.ndarray) -> np.ndarray:
    b = skel.astype(np.uint8)
    p = np.pad(b, 1)
    ring = [p[:-2, 1:-1], p[:-2, 2:], p[1:-1, 2:], p[2:, 2:],
            p[2:, 1:-1], p[2:, :-2], p[1:-1, :-2], p[:-2, :-2]]
    return sum(np.abs(ring[i].astype(np.int8) -
                      ring[(i + 1) % 8].astype(np.int8))
               for i in range(8)) // 2


def clustered_points(mask: np.ndarray):
    n, lab = cv2.connectedComponents(mask.astype(np.uint8), 8)
    result = []
    for i in range(1, n):
        ys, xs = np.where(lab == i)
        result.append((int(round(xs.mean())), int(round(ys.mean()))))
    return result


def ordered_segment(component: np.ndarray):
    """Return an 8-connected component in endpoint-to-endpoint order."""
    ys, xs = np.where(component)
    pts = {(int(x), int(y)) for x, y in zip(xs, ys)}
    if not pts:
        return []
    degree = {p: sum((p[0] + dx, p[1] + dy) in pts for dx, dy in N8) for p in pts}
    ends = sorted(p for p, d in degree.items() if d == 1)
    cur = ends[0] if ends else min(pts)
    path, previous = [], None
    while True:
        path.append(cur)
        choices = [(cur[0] + dx, cur[1] + dy) for dx, dy in N8
                   if (cur[0] + dx, cur[1] + dy) in pts and
                   (cur[0] + dx, cur[1] + dy) != previous and
                   (cur[0] + dx, cur[1] + dy) not in path]
        if not choices:
            break
        # Branch pixels have been removed; this tie-break only handles tiny
        # 2x2 digital-skeleton ambiguities.
        if previous is not None and len(choices) > 1:
            v = np.array(cur) - np.array(previous)
            choices.sort(key=lambda q: -float(np.dot(v, np.array(q) - np.array(cur))))
        previous, cur = cur, choices[0]
    return path


def tangent(path, at_start, sample=12):
    if len(path) < 2:
        return np.zeros(2)
    p = np.asarray(path, float)
    if not at_start:
        p = p[::-1]
    j = min(sample, len(p) - 1)
    # Outward vector: from interior towards the endpoint.
    v = p[0] - p[j]
    n = np.linalg.norm(v)
    return v / n if n else np.zeros(2)


def curvature(path, at_start, sample=8):
    if len(path) < 2 * sample + 1:
        return 0.0
    p = np.asarray(path if at_start else path[::-1], float)
    a, b = p[0] - p[sample], p[sample] - p[2 * sample]
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if not na or not nb:
        return 0.0
    return math.acos(float(np.clip(np.dot(a, b) / (na * nb), -1, 1))) / sample


@dataclass
class EdgeCandidate:
    a: int
    a_start: bool
    b: int
    b_start: bool
    distance: float
    angle_deg: float
    curvature_delta: float
    score: float


def connection_candidates(paths, max_distance, max_angle, max_curvature_delta):
    ends = []
    for i, p in enumerate(paths):
        if len(p) >= 2:
            ends.extend([(i, True, np.array(p[0], float), tangent(p, True), curvature(p, True)),
                         (i, False, np.array(p[-1], float), tangent(p, False), curvature(p, False))])
    out = []
    for ia in range(len(ends)):
        a, ast, ap, av, ac = ends[ia]
        for ib in range(ia + 1, len(ends)):
            b, bst, bp, bv, bc = ends[ib]
            if a == b:
                continue
            d = float(np.linalg.norm(bp - ap))
            if d == 0 or d > max_distance:
                continue
            bridge = (bp - ap) / d
            # Both outward tangents should point into the gap.
            aa = math.degrees(math.acos(float(np.clip(np.dot(av, bridge), -1, 1))))
            ab = math.degrees(math.acos(float(np.clip(np.dot(bv, -bridge), -1, 1))))
            angle = max(aa, ab)
            cd = abs(ac - bc)
            if angle > max_angle or cd > max_curvature_delta:
                continue
            score = .45 * d / max_distance + .45 * angle / max_angle + \
                    .10 * cd / max_curvature_delta
            out.append(EdgeCandidate(a, ast, b, bst, d, angle, cd, score))
    return sorted(out, key=lambda e: e.score)


def select_links(candidates, n_paths, max_links_per_candidate=8):
    """Greedy endpoint matching plus cycle prevention."""
    used, links = set(), []
    parent = list(range(n_paths))

    def root(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for e in candidates:
        ea, eb = (e.a, e.a_start), (e.b, e.b_start)
        if ea in used or eb in used or root(e.a) == root(e.b):
            continue
        if len(links) >= max_links_per_candidate * n_paths:
            break
        used.update((ea, eb)); links.append(e)
        parent[root(e.a)] = root(e.b)
    return links


def candidate_groups(n, links):
    adj = [[] for _ in range(n)]
    for e in links:
        adj[e.a].append(e.b); adj[e.b].append(e.a)
    groups, seen = [], set()
    for i in range(n):
        if i in seen:
            continue
        stack, group = [i], []; seen.add(i)
        while stack:
            x = stack.pop(); group.append(x)
            for y in adj[x]:
                if y not in seen:
                    seen.add(y); stack.append(y)
        groups.append(group)
    return groups


def color_for(i):
    return tuple(int(x) for x in cv2.cvtColor(
        np.uint8([[[i * 47 % 180, 220, 255]]]), cv2.COLOR_HSV2BGR)[0, 0])


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mask", type=Path, default=Path("outputs/binary_mask.png"))
    ap.add_argument("--skeleton", type=Path, default=Path("outputs/skeleton.png"))
    ap.add_argument("--output", type=Path, default=Path("outputs"))
    ap.add_argument("--min-segment-length", type=int, default=5)
    ap.add_argument("--min-candidate-length", type=int, default=25)
    ap.add_argument("--max-gap", type=float, default=35)
    ap.add_argument("--max-angle", type=float, default=42)
    ap.add_argument("--max-curvature-delta", type=float, default=.12)
    args = ap.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    mask, skel = read_binary(args.mask), read_binary(args.skeleton)
    if mask.shape != skel.shape:
        raise ValueError("binary mask and skeleton sizes differ")

    cn = crossing_number(skel)
    endpoint_mask = skel & (cn == 1)
    branch_mask = skel & (cn >= 3)
    endpoints, branches = clustered_points(endpoint_mask), clustered_points(branch_mask)
    # Remove each whole branch cluster and its one-pixel contact ring so arms
    # become clean independent graph edges.
    cut = cv2.dilate(branch_mask.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
    split = skel & ~cut
    n, labels, stats, _ = cv2.connectedComponentsWithStats(split.astype(np.uint8), 8)
    paths = []
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] >= args.min_segment_length:
            paths.append(ordered_segment(labels == i))

    proposals = connection_candidates(paths, args.max_gap, args.max_angle,
                                     args.max_curvature_delta)
    links = select_links(proposals, len(paths))
    groups = candidate_groups(len(paths), links)
    lengths = [sum(len(paths[j]) for j in g) for g in groups]
    retained = [i for i, length in enumerate(lengths) if length >= args.min_candidate_length]

    base = cv2.cvtColor((mask.astype(np.uint8) * 70), cv2.COLOR_GRAY2BGR)
    graph = base.copy(); graph[skel] = (180, 180, 180)
    for x, y in endpoints:
        cv2.circle(graph, (x, y), 4, (0, 255, 0), -1)
    for x, y in branches:
        cv2.circle(graph, (x, y), 5, (0, 0, 255), -1)
    cv2.imwrite(str(args.output / "01_graph_overlay.png"), graph)

    overlay = base.copy()
    for ci in retained:
        color = color_for(ci)
        for j in groups[ci]:
            for x, y in paths[j]:
                overlay[y, x] = color
    overlay = cv2.dilate(overlay, np.ones((3, 3), np.uint8))
    for e in links:
        pa = paths[e.a][0 if e.a_start else -1]
        pb = paths[e.b][0 if e.b_start else -1]
        cv2.line(overlay, pa, pb, color_for(next(i for i, g in enumerate(groups) if e.a in g)), 2)
    cv2.imwrite(str(args.output / "02_candidate_paths_overlay.png"), overlay)

    raw_n = cv2.connectedComponents(skel.astype(np.uint8), 8)[0] - 1
    fields = ["record_type", "id", "raw_component_count", "pixel_length",
              "segment_count", "endpoint_count", "branch_point_count",
              "accepted_link_count", "mean_link_gap_px", "retained"]
    rows = [dict(record_type="summary", id="all", pixel_length=int(skel.sum()),
                 raw_component_count=raw_n,
                 segment_count=len(paths), endpoint_count=len(endpoints),
                 branch_point_count=len(branches), accepted_link_count=len(links),
                 mean_link_gap_px=(round(float(np.mean([e.distance for e in links])), 3)
                                   if links else 0),
                 retained=len(retained))]
    for ci, group in enumerate(groups):
        group_links = [e for e in links if e.a in group]
        rows.append(dict(record_type="candidate", id=ci + 1, pixel_length=lengths[ci],
                         raw_component_count="", segment_count=len(group), endpoint_count="",
                         branch_point_count="", accepted_link_count=len(group_links),
                         mean_link_gap_px=(round(float(np.mean([e.distance for e in group_links])), 3)
                                           if group_links else 0),
                         retained=int(ci in retained)))
    with (args.output / "03_component_statistics.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(rows)

    print(f"raw_skeleton_components={raw_n}")
    print(f"endpoints={len(endpoints)} branch_points={len(branches)}")
    print(f"segments_after_branch_cut={len(paths)} accepted_links={len(links)}")
    print(f"candidate_groups={len(groups)} retained_candidates_ge_{args.min_candidate_length}px={len(retained)}")


if __name__ == "__main__":
    main()
