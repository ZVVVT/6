"""Score graph tail candidates.

The script deliberately rebuilds candidates with the Phase-2 defaults, reads
the FITC image for photometric features, and writes only diagnostic outputs.
"""
from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path

import cv2
import numpy as np

from tail_graph_experiment import (
    candidate_groups, connection_candidates, crossing_number, ordered_segment,
    read_binary, select_links,
)


def line_points(a, b):
    """Integer pixels on a bridge, excluding its first endpoint."""
    n = max(abs(b[0] - a[0]), abs(b[1] - a[1]))
    if n == 0:
        return []
    xs = np.rint(np.linspace(a[0], b[0], n + 1)).astype(int)
    ys = np.rint(np.linspace(a[1], b[1], n + 1)).astype(int)
    return list(dict.fromkeys(zip(xs[1:], ys[1:])))


def ordered_candidate(group, paths, links):
    """Join a Phase-2 segment group into one endpoint-to-endpoint polyline."""
    if len(group) == 1:
        return list(paths[group[0]])
    adj = {i: [] for i in group}
    for e in links:
        if e.a in adj and e.b in adj:
            adj[e.a].append((e.b, e.a_start, e.b_start))
            adj[e.b].append((e.a, e.b_start, e.a_start))
    start = min((i for i in group if len(adj[i]) <= 1), default=min(group))
    order, prev, cur = [], None, start
    while True:
        options = [x for x in adj[cur] if x[0] != prev]
        nxt = options[0] if options else None
        incoming = None
        if prev is not None:
            incoming = next(own_start for j, own_start, _ in adj[cur] if j == prev)
        outgoing = nxt[1] if nxt else None
        p = list(paths[cur])
        # Desired orientation is incoming endpoint -> outgoing endpoint. At the
        # first segment, put its outgoing endpoint last.
        if incoming is not None:
            if not incoming:  # incoming is currently path[-1]
                p.reverse()
        elif outgoing is True:  # outgoing is path[0], so reverse it to the end
            p.reverse()
        if order:
            order.extend(line_points(order[-1], p[0]))
        order.extend(p if not order or order[-1] != p[0] else p[1:])
        if nxt is None:
            break
        prev, cur = cur, nxt[0]
    return order


def graph_preserving_candidate(group, paths, links):
    """Reconstruct a branched candidate as its longest start-anchored path."""
    adjacency = {index: [] for index in group}
    for edge in links:
        if edge.a in adjacency and edge.b in adjacency:
            adjacency[edge.a].append((edge.b, edge.a_start))
            adjacency[edge.b].append((edge.a, edge.b_start))
    if all(len(edges) <= 2 for edges in adjacency.values()):
        return ordered_candidate(group, paths, links)

    ends = sorted(node for node, edges in adjacency.items() if len(edges) <= 1)
    starts = ends or sorted(adjacency)
    chains = []

    def visit(node, target, used, chain):
        if node == target:
            chains.append(chain)
            return
        for neighbor, unused in adjacency[node]:
            if neighbor not in used:
                visit(neighbor, target, used | {neighbor}, chain + [neighbor])

    for offset, start in enumerate(starts):
        for target in starts[offset + 1:]:
            visit(start, target, {start}, [start])
    if not chains:
        chains = [[node] for node in sorted(adjacency)]

    formal_start = min((node for node in group if len(adjacency[node]) <= 1),
                       default=min(group))
    chains = [chain for chain in chains
              if chain[0] == formal_start or chain[-1] == formal_start]
    chains = [chain if chain[0] == formal_start else list(reversed(chain))
              for chain in chains]

    alternatives = []
    for chain in chains:
        output = []
        for position, node in enumerate(chain):
            previous = chain[position - 1] if position else None
            following = chain[position + 1] if position + 1 < len(chain) else None
            incoming = (next(value for neighbor, value in adjacency[node]
                             if neighbor == previous)
                        if previous is not None else None)
            outgoing = (next(value for neighbor, value in adjacency[node]
                             if neighbor == following)
                        if following is not None else None)
            points = list(paths[node])
            if incoming is not None:
                if not incoming:
                    points.reverse()
            elif outgoing is True:
                points.reverse()
            if output:
                output.extend(line_points(output[-1], points[0]))
            output.extend(points if not output or output[-1] != points[0]
                          else points[1:])
        alternatives.append(output)
    return max(alternatives, key=path_length)


def candidate_path(group, paths, links, mode="ordered"):
    if mode == "ordered":
        return ordered_candidate(group, paths, links)
    if mode == "graph_preserving":
        return graph_preserving_candidate(group, paths, links)
    raise ValueError("Unsupported candidate_path_mode: {}".format(mode))


def path_length(points):
    p = np.asarray(points, float)
    return float(np.linalg.norm(np.diff(p, axis=0), axis=1).sum()) if len(p) > 1 else 0.0


def path_curvature(points, stride=4):
    """Mean absolute turning angle per path pixel (radians/pixel)."""
    p = np.asarray(points, float)
    if len(p) < 2 * stride + 1:
        return 0.0
    a, b = p[stride:-stride] - p[:-2 * stride], p[2 * stride:] - p[stride:-stride]
    na, nb = np.linalg.norm(a, axis=1), np.linalg.norm(b, axis=1)
    ok = (na > 0) & (nb > 0)
    if not np.any(ok):
        return 0.0
    angles = np.arccos(np.clip(np.sum(a[ok] * b[ok], axis=1) / (na[ok] * nb[ok]), -1, 1))
    return float(np.sum(np.abs(angles)) / max(path_length(points), 1.0))


def robust01(values, higher=True):
    v = np.asarray(values, float)
    lo, hi = np.percentile(v, [10, 90])
    z = np.clip((v - lo) / max(hi - lo, 1e-9), 0, 1)
    return z if higher else 1 - z


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fitc", type=Path, default=Path("../CS3-test-017/ZBFY017-C-1_RGB_G.tif"))
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
    fitc = cv2.imread(str(args.fitc), cv2.IMREAD_UNCHANGED)
    if fitc is None:
        raise FileNotFoundError(args.fitc)
    if fitc.ndim == 3:
        fitc = cv2.cvtColor(fitc, cv2.COLOR_BGR2GRAY)
    if fitc.shape != skel.shape:
        raise ValueError("FITC, mask, and skeleton sizes differ")
    fitc_f = fitc.astype(np.float32)

    branch = skel & (crossing_number(skel) >= 3)
    cut = cv2.dilate(branch.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
    n, labels, stats, _ = cv2.connectedComponentsWithStats((skel & ~cut).astype(np.uint8), 8)
    paths = [ordered_segment(labels == i) for i in range(1, n)
             if stats[i, cv2.CC_STAT_AREA] >= args.min_segment_length]
    proposals = connection_candidates(paths, args.max_gap, args.max_angle, args.max_curvature_delta)
    links = select_links(proposals, len(paths))
    groups = candidate_groups(len(paths), links)
    retained = [(g, sum(len(paths[j]) for j in g)) for g in groups]
    retained = [x for x in retained if x[1] >= args.min_candidate_length]

    # Phase-1's binary foreground is the operational signal threshold. Sampling
    # the full reconstructed path makes bridge failures lower continuity.
    dist = cv2.distanceTransform(mask.astype(np.uint8), cv2.DIST_L2, 5)
    rows, polylines = [], []
    for cid, (group, _) in enumerate(retained, 1):
        pts = ordered_candidate(group, paths, links)
        xy = np.asarray(pts, int)
        xs, ys = xy[:, 0], xy[:, 1]
        vals = fitc_f[ys, xs]
        group_links = [e for e in links if e.a in group and e.b in group]
        rows.append({
            "candidate_id": cid,
            "skeleton_length_px": round(path_length(pts), 3),
            "path_curvature_rad_per_px": round(path_curvature(pts), 6),
            "fitc_mean_intensity": round(float(vals.mean()), 3),
            "intensity_continuity": round(float(mask[ys, xs].mean()), 4),
            "path_gap_count": len(group_links),
            "width_estimate_px": round(float((2.0 * dist[ys, xs]).mean()), 3),
        })
        polylines.append(xy.astype(np.int32))

    if len(rows) != 81:
        raise RuntimeError(f"Expected 81 retained Phase-2 candidates, reconstructed {len(rows)}")

    length_s = robust01([r["skeleton_length_px"] for r in rows])
    curve_s = robust01([r["path_curvature_rad_per_px"] for r in rows], False)
    intensity_s = robust01([r["fitc_mean_intensity"] for r in rows])
    continuity_s = np.asarray([r["intensity_continuity"] for r in rows])
    gap_density = [r["path_gap_count"] / max(r["skeleton_length_px"] / 100, 1) for r in rows]
    gap_s = robust01(gap_density, False)
    widths = np.asarray([r["width_estimate_px"] for r in rows])
    medw = float(np.median(widths))
    width_s = np.exp(-np.abs(np.log((widths + .25) / (medw + .25))))
    score = 100 * (.24 * length_s + .18 * curve_s + .20 * intensity_s +
                   .20 * continuity_s + .13 * gap_s + .05 * width_s)
    q_low, q_high = np.percentile(score, [33.333, 66.667])
    for r, s in zip(rows, score):
        r["candidate_score"] = round(float(s), 2)
        r["automatic_class"] = ("likely_tail" if s >= q_high else
                                  "likely_false_connection" if s < q_low else "uncertain")

    fields = list(rows[0])
    with (args.output / "02_candidate_scores.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(rows)

    lo, hi = float(fitc_f.min()), float(fitc_f.max())
    gray = np.uint8(np.clip((fitc_f - lo) * 255 / max(hi - lo, 1e-9), 0, 255))
    overlay = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    for r, poly in zip(rows, polylines):
        s = r["candidate_score"] / 100.0
        color = (0, int(255 * s), int(255 * (1 - s)))
        cv2.polylines(overlay, [poly.reshape(-1, 1, 2)], False, color, 2, cv2.LINE_AA)
        mid = tuple(poly[len(poly) // 2])
        cv2.putText(overlay, str(r["candidate_id"]), mid, cv2.FONT_HERSHEY_SIMPLEX,
                    .35, (255, 255, 255), 1, cv2.LINE_AA)
    cv2.putText(overlay, "score: red=low, green=high", (12, 24),
                cv2.FONT_HERSHEY_SIMPLEX, .6, (255, 255, 255), 2, cv2.LINE_AA)
    cv2.imwrite(str(args.output / "01_candidate_score_overlay.png"), overlay)
    print(f"candidates_scored={len(rows)} score_range={score.min():.2f}-{score.max():.2f}")
    print(f"class_cutoffs: false<{q_low:.2f}, tail>={q_high:.2f}")


if __name__ == "__main__":
    main()
