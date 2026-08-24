"""Stage 6: merge validated graph candidates that represent one FITC tail.

The merge decision is independent of TailFinalLabels and uses buffered path
overlap/proximity, local direction agreement, and FITC continuity only.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import cv2
import numpy as np

from candidate_scoring import line_points
from candidate_validation import reconstruct


def gray_view(fitc):
    lo, hi = np.percentile(fitc, [1, 99.8])
    return np.uint8(np.clip((fitc - lo) * 255 / max(hi - lo, 1e-6), 0, 255))


def path_mask(path, shape, radius=2):
    out = np.zeros(shape, np.uint8)
    cv2.polylines(out, [path.reshape(-1, 1, 2)], False, 1,
                  max(1, 2 * radius + 1), cv2.LINE_8)
    return out > 0


def tangent(path, at_start, window=12):
    """Unit vector pointing out of the selected endpoint."""
    p = path.astype(float)
    if len(p) < 2:
        return np.zeros(2)
    if at_start:
        v = p[0] - p[min(window, len(p) - 1)]
    else:
        v = p[-1] - p[max(0, len(p) - 1 - window)]
    return v / max(np.linalg.norm(v), 1e-6)


def endpoint_metrics(a, b, fitc):
    best = None
    for a_start in (True, False):
        pa = a[0] if a_start else a[-1]
        va = tangent(a, a_start)
        for b_start in (True, False):
            pb = b[0] if b_start else b[-1]
            vb = tangent(b, b_start)
            delta = pb.astype(float) - pa
            gap = float(np.linalg.norm(delta))
            if gap < 1e-6:
                facing = 1.0
            else:
                u = delta / gap
                facing = max(0.0, min(float(np.dot(va, u)), float(np.dot(vb, -u))))
            direction = abs(float(np.dot(va, vb)))
            item = (gap, direction, facing, a_start, b_start, pa, pb)
            if best is None or gap < best[0]:
                best = item
    gap, direction, facing, a_start, b_start, pa, pb = best
    bridge = np.asarray(line_points(tuple(pa), tuple(pb)), dtype=int)
    bridge[:, 0] = np.clip(bridge[:, 0], 0, fitc.shape[1] - 1)
    bridge[:, 1] = np.clip(bridge[:, 1], 0, fitc.shape[0] - 1)
    av = fitc[a[:, 1], a[:, 0]]; bv = fitc[b[:, 1], b[:, 0]]
    endpoint_level = max(min(float(np.median(av[-12:] if not a_start else av[:12])),
                             float(np.median(bv[-12:] if not b_start else bv[:12]))), 1e-6)
    continuity = float(np.clip(np.percentile(fitc[bridge[:, 1], bridge[:, 0]], 25)
                               / endpoint_level, 0, 1))
    return gap, direction, facing, continuity


def pair_metrics(a, b, fitc, radius):
    # Work in a pair-local crop: full-frame distance transforms for every pair
    # are needlessly expensive on large microscopy fields.
    pad = radius + 1
    x0 = max(0, int(min(a[:, 0].min(), b[:, 0].min())) - pad)
    y0 = max(0, int(min(a[:, 1].min(), b[:, 1].min())) - pad)
    x1 = min(fitc.shape[1], int(max(a[:, 0].max(), b[:, 0].max())) + pad + 1)
    y1 = min(fitc.shape[0], int(max(a[:, 1].max(), b[:, 1].max())) + pad + 1)
    local_shape = (y1 - y0, x1 - x0)
    aa, bb = a - (x0, y0), b - (x0, y0)
    ma, mb = path_mask(aa, local_shape, radius), path_mask(bb, local_shape, radius)
    inter = int((ma & mb).sum())
    overlap = inter / max(1, min(int(ma.sum()), int(mb.sum())))
    # Sampled point-to-point distance is enough for the proximity gate.
    sa = a[::max(1, len(a) // 80)].astype(float)
    sb = b[::max(1, len(b) // 80)].astype(float)
    distances = np.sqrt(((sa[:, None, :] - sb[None, :, :]) ** 2).sum(axis=2))
    spatial = float(min(np.median(distances.min(axis=1)),
                        np.median(distances.min(axis=0))))
    gap, direction, facing, continuity = endpoint_metrics(a, b, fitc)
    return overlap, spatial, gap, direction, facing, continuity


def merge_candidates(rows, paths, fitc, args):
    n = len(paths)
    parent = list(range(n))
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x
    def union(a, b):
        a, b = find(a), find(b)
        if a != b: parent[b] = a

    pair_rows = []
    for i in range(n):
        for j in range(i + 1, n):
            overlap, spatial, gap, direction, facing, continuity = pair_metrics(
                paths[i], paths[j], fitc, args.path_radius)
            parallel_merge = (overlap >= args.min_overlap and
                              spatial <= args.max_spatial_distance and
                              direction >= args.min_direction and
                              continuity >= args.min_fitc_continuity)
            endpoint_merge = (gap <= args.max_endpoint_gap and
                              direction >= args.min_direction and
                              facing >= args.min_facing and
                              continuity >= args.min_fitc_continuity)
            merge = parallel_merge or endpoint_merge
            if merge: union(i, j)
            # The table contains accepted relationships; rejected O(n^2) pairs
            # would obscure the candidate-to-merged-instance audit.
            if merge:
                pair_rows.append(dict(candidate_id_a=rows[i]["candidate_id"],
                    candidate_id_b=rows[j]["candidate_id"], overlap_rate=overlap,
                    spatial_distance_px=spatial, endpoint_gap_px=gap,
                    direction_consistency=direction, endpoint_facing=facing,
                    fitc_continuity=continuity,
                    merge_reason="path_overlap" if parallel_merge else "endpoint_connection"))
    groups = {}
    for i in range(n): groups.setdefault(find(i), []).append(i)
    ordered = sorted(groups.values(), key=lambda g: min(rows[i]["candidate_id"] for i in g))
    assignment = {}
    for mid, group in enumerate(ordered, 1):
        for i in group: assignment[i] = mid
    return ordered, assignment, pair_rows


def overlay(base, paths, assignment, count, title, merged=False):
    out = cv2.cvtColor(base, cv2.COLOR_GRAY2BGR)
    rng = np.random.default_rng(6022)
    colors = rng.integers(45, 256, size=(count + 1, 3), dtype=np.uint8)
    for i, path in enumerate(paths):
        color = tuple(int(x) for x in (colors[assignment[i]] if merged else colors[i + 1]))
        cv2.polylines(out, [path.reshape(-1, 1, 2)], False, color, 2, cv2.LINE_AA)
    cv2.putText(out, title, (12, 26), cv2.FONT_HERSHEY_SIMPLEX, .65,
                (255, 255, 255), 2, cv2.LINE_AA)
    return out


def reference_iou(groups, paths, reference, radius):
    if reference is None: return {}
    result = {}
    for mid, group in enumerate(groups, 1):
        mask = np.zeros(reference.shape, bool)
        for i in group: mask |= path_mask(paths[i], reference.shape, radius)
        ids, counts = np.unique(reference[mask & (reference != 0)], return_counts=True)
        best_id, best_iou = 0, 0.0
        for rid, inter in zip(ids, counts):
            union = int(mask.sum() + (reference == rid).sum() - inter)
            iou = float(inter / max(union, 1))
            if iou > best_iou: best_id, best_iou = int(rid), iou
        result[mid] = (best_id, best_iou)
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fitc", type=Path, required=True)
    ap.add_argument("--mask", type=Path, required=True)
    ap.add_argument("--skeleton", type=Path, required=True)
    ap.add_argument("--reference", type=Path)
    ap.add_argument("--output", type=Path, default=Path("outputs/candidate_merging"))
    ap.add_argument("--validation-threshold", type=float, default=.60)
    ap.add_argument("--path-radius", type=int, default=3)
    ap.add_argument("--min-overlap", type=float, default=.35)
    ap.add_argument("--max-spatial-distance", type=float, default=5.0)
    # Broken tails in the 4k ZBFY fields can have long dim gaps. The permissive
    # distance is therefore guarded by three independent direction/FITC gates.
    ap.add_argument("--max-endpoint-gap", type=float, default=400.0)
    ap.add_argument("--min-direction", type=float, default=.40)
    ap.add_argument("--min-facing", type=float, default=.10)
    ap.add_argument("--min-fitc-continuity", type=float, default=.12)
    ap.add_argument("--min-segment-length", type=int, default=5)
    ap.add_argument("--min-candidate-length", type=int, default=25)
    ap.add_argument("--max-gap", type=float, default=35)
    ap.add_argument("--max-angle", type=float, default=42)
    ap.add_argument("--max-curvature-delta", type=float, default=.12)
    args = ap.parse_args(); args.output.mkdir(parents=True, exist_ok=True)
    fitc = cv2.imread(str(args.fitc), cv2.IMREAD_UNCHANGED)
    if fitc is None: raise FileNotFoundError(args.fitc)
    if fitc.ndim == 3: fitc = cv2.cvtColor(fitc, cv2.COLOR_BGR2GRAY)
    fitc = fitc.astype(np.float32)
    rows, paths = reconstruct(args, fitc)
    kept = [(r, p) for r, p in zip(rows, paths)
            if r["final_score"] >= args.validation_threshold]
    kept_rows, kept_paths = [x[0] for x in kept], [x[1] for x in kept]
    groups, assignment, pair_rows = merge_candidates(kept_rows, kept_paths, fitc, args)
    reference = None
    if args.reference:
        reference = cv2.imread(str(args.reference), cv2.IMREAD_UNCHANGED)
        if reference is None: raise FileNotFoundError(args.reference)
        if reference.ndim == 3: reference = reference[..., 0]
    ious = reference_iou(groups, kept_paths, reference, args.path_radius)
    members = {mid: [kept_rows[i]["candidate_id"] for i in group]
               for mid, group in enumerate(groups, 1)}
    accepted = {(int(r["candidate_id_a"]), int(r["candidate_id_b"])): r for r in pair_rows}
    table = []
    for i, row in enumerate(kept_rows):
        mid = assignment[i]; group = groups[mid - 1]
        links = [r for (a, b), r in accepted.items() if row["candidate_id"] in (a, b)]
        best = max(links, key=lambda r: max(r["overlap_rate"], r["fitc_continuity"]), default={})
        rid, iou = ious.get(mid, ("", ""))
        table.append(dict(candidate_id=row["candidate_id"], merged_candidate_id=mid,
            group_size=len(group), member_candidate_ids=";".join(map(str, members[mid])),
            merge_status="merged" if len(group) > 1 else "unique",
            merge_reason=best.get("merge_reason", ""),
            overlap_rate=best.get("overlap_rate", ""),
            spatial_distance_px=best.get("spatial_distance_px", ""),
            endpoint_gap_px=best.get("endpoint_gap_px", ""),
            direction_consistency=best.get("direction_consistency", ""),
            fitc_continuity=best.get("fitc_continuity", ""),
            best_reference_tail_id=rid, best_reference_iou=iou))
    fields = list(table[0]) if table else ["candidate_id", "merged_candidate_id"]
    with (args.output / "candidate_merge_table.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(table)
    before, after = len(kept_paths), len(groups); base = gray_view(fitc)
    cv2.imwrite(str(args.output / "merging_before_overlay.png"),
                overlay(base, kept_paths, {i: i + 1 for i in range(before)}, before,
                        f"before merging: {before} validated candidates"))
    cv2.imwrite(str(args.output / "merging_after_overlay.png"),
                overlay(base, kept_paths, assignment, after,
                        f"after merging: {after} unique candidates", True))
    with (args.output / "merging_statistics.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["before_candidate_count", "after_candidate_count"])
        w.writeheader(); w.writerow({"before_candidate_count": before, "after_candidate_count": after})
    # Preserve each merged graph candidate as a group of its original
    # polylines.  Keeping components separate avoids inventing straight-line
    # pixels between paths that were merged by the Stage-6 decision.
    merged_paths = [
        [[[int(x), int(y)] for x, y in kept_paths[i]] for i in group]
        for group in groups
    ]
    (args.output / "merged_graph_paths.json").write_text(
        json.dumps({"paths": merged_paths}), encoding="utf-8")
    print({"before_candidate_count": before, "after_candidate_count": after})


if __name__ == "__main__":
    main()
