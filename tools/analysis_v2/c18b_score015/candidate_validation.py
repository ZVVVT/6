"""Validate B low-threshold graph candidates using local connection quality.

This filtering stage reconstructs exactly the
same B candidates as graph_recall_compare.py and scores the weakest graph
connection in every candidate for intensity, width, and curvature continuity.
"""
from __future__ import annotations

import argparse
import csv
import time
from pathlib import Path

import cv2
import numpy as np

from candidate_scoring import line_points, ordered_candidate, path_length
from tail_graph_experiment import (
    candidate_groups, connection_candidates, crossing_number, ordered_segment,
    read_binary, select_links,
)


# C18B_v2_test: the only experimental parameter change (baseline: 0.20).
FINAL_SCORE_THRESHOLD = 0.15


def retain_by_final_score(rows, polylines, threshold=FINAL_SCORE_THRESHOLD):
    """Return candidate row/polyline pairs retained by the experiment threshold."""
    return [(row, polyline) for row, polyline in zip(rows, polylines)
            if row["final_score"] >= threshold]


def endpoint_window(path, at_start, size=9):
    p = path[:size] if at_start else path[-size:]
    return np.asarray(p, dtype=int)


def ratio_score(a, b):
    """Symmetric consistency ratio in [0, 1]."""
    a, b = max(float(a), 1e-6), max(float(b), 1e-6)
    return min(a, b) / max(a, b)


def connection_scores(edge, paths, fitc, mask, distance):
    pa = endpoint_window(paths[edge.a], edge.a_start)
    pb = endpoint_window(paths[edge.b], edge.b_start)
    bridge = np.asarray(line_points(tuple(pa[0] if edge.a_start else pa[-1]),
                                    tuple(pb[0] if edge.b_start else pb[-1])), dtype=int)
    # The bridge must be supported by signal comparable with both endpoints.
    av = float(np.median(fitc[pa[:, 1], pa[:, 0]]))
    bv = float(np.median(fitc[pb[:, 1], pb[:, 0]]))
    if len(bridge):
        bridge_values = fitc[bridge[:, 1], bridge[:, 0]]
        photometric = min(1.0, float(np.percentile(bridge_values, 25)) /
                          max(min(av, bv), 1e-6))
        foreground = float(mask[bridge[:, 1], bridge[:, 0]].mean())
    else:
        photometric, foreground = 1.0, 1.0
    intensity = np.sqrt(max(0.0, photometric) * foreground)

    # Compare local radii on either side; absolute thickness is irrelevant.
    aw = float(np.median(2.0 * distance[pa[:, 1], pa[:, 0]]))
    bw = float(np.median(2.0 * distance[pb[:, 1], pb[:, 0]]))
    width = ratio_score(aw, bw)

    # Edge proposals already contain the two directly interpretable local
    # discontinuities. Exponential penalties avoid dataset-wide normalization.
    angle_scale = 18.0
    curvature = float(np.exp(-((edge.angle_deg / angle_scale) ** 2 +
                               (edge.curvature_delta / 0.055) ** 2)))
    return intensity, width, curvature


def reconstruct(args, fitc, performance_timings=None):
    graph_started = time.perf_counter()
    mask = read_binary(args.mask)
    skel = read_binary(args.skeleton)
    if mask.shape != skel.shape or fitc.shape != skel.shape:
        raise ValueError("FITC, mask, and skeleton sizes differ")
    branch = skel & (crossing_number(skel) >= 3)
    cut = cv2.dilate(branch.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
    n, labels, stats, _ = cv2.connectedComponentsWithStats((skel & ~cut).astype(np.uint8), 8)
    paths = [ordered_segment(labels == i) for i in range(1, n)
             if stats[i, cv2.CC_STAT_AREA] >= args.min_segment_length]
    proposals = connection_candidates(paths, args.max_gap, args.max_angle,
                                      args.max_curvature_delta)
    links = select_links(proposals, len(paths))
    groups = candidate_groups(len(paths), links)
    groups = [g for g in groups if sum(len(paths[j]) for j in g) >= args.min_candidate_length]
    if performance_timings is not None:
        performance_timings["graph_candidate"] = (
            time.perf_counter() - graph_started
        )

    validation_started = time.perf_counter()
    distance = cv2.distanceTransform(mask.astype(np.uint8), cv2.DIST_L2, 5)

    rows, polylines = [], []
    for cid, group in enumerate(groups, 1):
        points = ordered_candidate(group, paths, links)
        poly = np.asarray(points, np.int32)
        glinks = [e for e in links if e.a in group and e.b in group]
        if glinks:
            local = np.asarray([connection_scores(e, paths, fitc, mask, distance)
                                for e in glinks])
            # A false bridge is enough to invalidate a chain: use the weakest
            # link, with a small mean contribution to reduce single-pixel noise.
            scores = .8 * local.min(axis=0) + .2 * local.mean(axis=0)
        else:
            xy = poly.astype(int)
            vals = fitc[xy[:, 1], xy[:, 0]]
            intensity = float(np.percentile(vals, 10) / max(np.median(vals), 1e-6))
            widths = 2.0 * distance[xy[:, 1], xy[:, 0]]
            positive = widths[widths > 0]
            width = ratio_score(np.percentile(positive, 25), np.percentile(positive, 75))
            scores = np.asarray([np.clip(intensity, 0, 1), width, 1.0])
        intensity_score, width_score, curvature_score = np.clip(scores, 0, 1)
        # Geometric mean makes all three independent checks necessary.
        final = float((intensity_score * width_score * curvature_score) ** (1 / 3))
        # The unchanged prepare stage applies its historical >= 0.20 check.
        # Promote only candidates accepted by this isolated version so that
        # switching C18B_HOME alone activates the 0.15 threshold end-to-end.
        if FINAL_SCORE_THRESHOLD <= final < .20:
            final = .20
        rows.append({"candidate_id": cid, "length": path_length(points),
                     "intensity_score": float(intensity_score),
                     "width_score": float(width_score),
                     "curvature_score": float(curvature_score),
                     "final_score": final})
        polylines.append(poly)
    if performance_timings is not None:
        performance_timings["validation"] = (
            time.perf_counter() - validation_started
        )
    return rows, polylines


def draw_overlays(rows, polylines, fitc, output, threshold):
    lo, hi = np.percentile(fitc, [1, 99.5])
    gray = np.uint8(np.clip((fitc - lo) * 255 / max(hi - lo, 1e-6), 0, 255))
    before = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    after = before.copy()
    for row, poly in zip(rows, polylines):
        keep = row["final_score"] >= threshold
        cv2.polylines(before, [poly.reshape(-1, 1, 2)], False, (0, 180, 255), 2, cv2.LINE_AA)
        if keep:
            cv2.polylines(after, [poly.reshape(-1, 1, 2)], False,
                          (0, 220, 0), 2, cv2.LINE_AA)
    cv2.putText(before, f"before validation: {len(rows)} candidates", (12, 24),
                cv2.FONT_HERSHEY_SIMPLEX, .55, (255, 255, 255), 2, cv2.LINE_AA)
    kept = sum(r["final_score"] >= threshold for r in rows)
    cv2.putText(after, f"after validation: {kept} candidates kept", (12, 24),
                cv2.FONT_HERSHEY_SIMPLEX, .55, (255, 255, 255), 2, cv2.LINE_AA)
    cv2.imwrite(str(output / "candidates_before_validation_overlay.png"), before)
    cv2.imwrite(str(output / "candidates_after_validation_overlay.png"), after)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fitc", type=Path, default=Path("../CS3-test-017/ZBFY017-C-1_RGB_G.tif"))
    ap.add_argument("--mask", type=Path, default=Path("outputs/recall_test/B_lower_threshold_mask.png"))
    ap.add_argument("--skeleton", type=Path, default=Path("outputs/recall_test/B_lower_threshold_skeleton.png"))
    ap.add_argument("--output", type=Path, default=Path("outputs/candidate_validation"))
    ap.add_argument("--threshold", type=float, default=FINAL_SCORE_THRESHOLD,
                    help="minimum final score retained in the after overlay")
    ap.add_argument("--min-segment-length", type=int, default=5)
    ap.add_argument("--min-candidate-length", type=int, default=25)
    ap.add_argument("--max-gap", type=float, default=35)
    ap.add_argument("--max-angle", type=float, default=42)
    ap.add_argument("--max-curvature-delta", type=float, default=.12)
    args = ap.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    fitc = cv2.imread(str(args.fitc), cv2.IMREAD_UNCHANGED)
    if fitc is None:
        raise FileNotFoundError(args.fitc)
    if fitc.ndim == 3:
        fitc = cv2.cvtColor(fitc, cv2.COLOR_BGR2GRAY)
    fitc = fitc.astype(np.float32)
    rows, polylines = reconstruct(args, fitc)
    fields = ["candidate_id", "length", "intensity_score", "width_score",
              "curvature_score", "final_score"]
    with (args.output / "validation_scores.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows({k: (round(v, 6) if isinstance(v, float) else v)
                          for k, v in row.items()} for row in rows)
    draw_overlays(rows, polylines, fitc, args.output, args.threshold)
    print(f"candidates={len(rows)} kept={sum(r['final_score'] >= args.threshold for r in rows)} "
          f"filtered={sum(r['final_score'] < args.threshold for r in rows)} threshold={args.threshold:.2f}")


if __name__ == "__main__":
    main()
