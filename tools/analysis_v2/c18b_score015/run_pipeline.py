"""Unified entry point for the frozen C-03 identity_graph_v3 pipeline."""
from __future__ import annotations

import argparse
import csv
import heapq
import json
import tempfile
import time
from pathlib import Path

import cv2
import numpy as np

from candidate_merging import merge_candidates
from candidate_validation import reconstruct
from fitc_processing import enhanced_mask, read_fitc, skeleton
from graph_constrained_instance_separation import _mask_shape_metrics
from graph_seeded_region_growing import grow, instance_overlay
from identity_graph_v3 import cluster, graph_data, reconstruct as identity_reconstruct

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "frozen_parameters.json"
SAVE_DEBUG_IMAGES = False


def write_csv(path, rows, fields=None):
    rows = list(rows)
    fields = fields or (list(rows[0]) if rows else [])
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def base_view(fitc):
    lo, hi = np.percentile(fitc, [1, 99.8])
    return cv2.cvtColor(np.uint8(np.clip((fitc-lo)*255/max(hi-lo, 1), 0, 255)), cv2.COLOR_GRAY2BGR)


def paths_view(fitc, paths, kept):
    out = base_view(fitc)
    kept_ids = {id(p) for p in kept}
    for p in paths:
        cv2.polylines(out, [p.reshape(-1, 1, 2)], False,
                      (0, 220, 0) if id(p) in kept_ids else (0, 0, 220), 2, cv2.LINE_AA)
    return out


def _candidate_path_lengths(candidate_rows, indexes):
    return [float(candidate_rows[index]["length"]) for index in indexes]


def shadow_group_rows(candidate_rows, candidate_paths, merged):
    """Snapshot merged groups using only post-merge, pre-grow data."""
    diagnostics = []
    for merged_group_id, indexes in enumerate(merged, 1):
        lengths = _candidate_path_lengths(candidate_rows, indexes)
        points = np.concatenate([
            np.asarray(candidate_paths[index], dtype=np.int32)
            for index in indexes
        ])
        xs, ys = points[:, 0], points[:, 1]
        diagnostics.append({
            "shadow_stage": "post_merge_pre_grow",
            "shadow_metric_available": True,
            "merged_group_id": merged_group_id,
            "source_candidate_ids": ";".join(
                str(candidate_rows[index]["candidate_id"])
                for index in indexes
            ),
            "source_candidate_count": len(indexes),
            "min_candidate_path_length": min(lengths),
            "max_candidate_path_length": max(lengths),
            "total_candidate_path_length": sum(lengths),
            "mean_candidate_path_length": float(np.mean(lengths)),
            "bbox_width": int(xs.max() - xs.min() + 1),
            "bbox_height": int(ys.max() - ys.min() + 1),
            "group_path_length": sum(lengths),
            "region_parent_id": merged_group_id,
        })
    return diagnostics


def shadow_community_rows(candidate_rows, merged, membership, communities):
    """Snapshot identity communities before reconstruction or separation."""
    diagnostics = []
    for community_id, nodes in enumerate(communities, 1):
        indexes = []
        parent_ids = []
        for node in nodes:
            parent_text, fragment_text = node.split(":")
            parent_id = int(parent_text[1:])
            fragment_id = int(fragment_text[1:])
            if membership[node] != community_id:
                raise ValueError("Identity community membership is inconsistent")
            indexes.append(merged[parent_id - 1][fragment_id - 1])
            parent_ids.append(parent_id)
        lengths = _candidate_path_lengths(candidate_rows, indexes)
        unique_parents = sorted(set(parent_ids))
        parent_text = ";".join(str(value) for value in unique_parents)
        diagnostics.append({
            "shadow_stage": "post_identity_community",
            "shadow_metric_available": True,
            "identity_community_id": community_id,
            "parent_group_id": (unique_parents[0]
                                if len(unique_parents) == 1 else parent_text),
            "region_parent_id": (unique_parents[0]
                                 if len(unique_parents) == 1 else parent_text),
            "source_candidate_ids": ";".join(
                str(candidate_rows[index]["candidate_id"])
                for index in indexes
            ),
            "source_candidate_count": len(indexes),
            "min_candidate_path_length": min(lengths),
            "max_candidate_path_length": max(lengths),
            "total_candidate_path_length": sum(lengths),
            "mean_candidate_path_length": float(np.mean(lengths)),
            "community_member_node_count": len(nodes),
            "dense_final_instance_id": community_id,
        })
    return diagnostics


def candidate_diagnostic_rows(rows, paths, fitc, threshold, merged,
                              assignment, membership, final_id_by_community=None):
    """Build read-only candidate diagnostics from existing pipeline results."""
    merged_positions = {}
    for merged_id, group in enumerate(merged, 1):
        for fragment_id, kept_index in enumerate(group, 1):
            merged_positions[kept_index] = (
                assignment[kept_index], fragment_id, len(group)
            )

    kept_index = 0
    diagnostics = []
    for row, path in zip(rows, paths):
        xy = np.asarray(path, dtype=np.int32)
        unique_xy = np.unique(xy, axis=0)
        xs, ys = unique_xy[:, 0], unique_xy[:, 1]
        width = int(xs.max() - xs.min() + 1)
        height = int(ys.max() - ys.min() + 1)
        endpoint_distance = float(np.linalg.norm(
            xy[-1].astype(float) - xy[0].astype(float)
        ))
        path_length = float(row["length"])
        values = fitc[ys, xs]
        accepted = row["final_score"] >= threshold
        merged_id = ""
        merged_value = False
        final_instance_id = ""
        if accepted:
            merged_id, fragment_id, group_size = merged_positions[kept_index]
            merged_value = group_size > 1
            community_id = membership[
                "P{}:F{}".format(merged_id, fragment_id)
            ]
            final_instance_id = (final_id_by_community or {}).get(
                community_id, community_id
            )
            kept_index += 1
        diagnostics.append({
            "candidate_id": row["candidate_id"],
            "source_type": "graph_candidate",
            "area": int(len(unique_xy)),
            "skeleton_pixels": int(len(unique_xy)),
            "skeleton_length": path_length,
            "main_path_length": path_length,
            "bbox_width": width,
            "bbox_height": height,
            "aspect_ratio": float(max(width, height) / max(1, min(width, height))),
            "euclidean_end_distance": endpoint_distance,
            "tortuosity": float(path_length / max(endpoint_distance, 1e-6)),
            "mean_fitc": float(values.mean()),
            "integrated_fitc": float(values.sum()),
            "intensity_score": row["intensity_score"],
            "width_score": row["width_score"],
            "curvature_score": row["curvature_score"],
            "score": row["final_score"],
            "validation_passed": accepted,
            "validation_reject_reason": "accepted" if accepted else "score_low",
            "merged": merged_value,
            "merged_candidate_id": merged_id,
            "final_instance_id": final_instance_id,
        })
    return diagnostics


def _skeleton_geometry(mask):
    """Return read-only geometry for a final-instance mask copy.

    The main path is the longest endpoint-to-endpoint shortest path in each
    skeleton component.  It is exact for tree-like skeletons.  A closed loop
    has no endpoints, so its main-path fields are intentionally left blank.
    """
    source = np.asarray(mask, dtype=bool).copy()
    thinned = skeleton(source.astype(np.uint8) * 255) > 0
    points = [tuple(point) for point in np.argwhere(thinned)]
    point_set = set(points)
    adjacency = {}
    total_length = 0.0
    diagonal = 2.0 ** 0.5
    for point in points:
        y, x = point
        neighbours = []
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if not (dy or dx):
                    continue
                other = (y + dy, x + dx)
                if other in point_set:
                    weight = diagonal if dy and dx else 1.0
                    neighbours.append((other, weight))
                    if other > point:
                        total_length += weight
        adjacency[point] = neighbours

    endpoints = [point for point in points if len(adjacency[point]) == 1]
    best_length = None
    best_pair = None
    for start in endpoints:
        distances = {start: 0.0}
        queue = [(0.0, start)]
        while queue:
            distance, current = heapq.heappop(queue)
            if distance != distances[current]:
                continue
            for other, weight in adjacency[current]:
                candidate = distance + weight
                if candidate < distances.get(other, float("inf")):
                    distances[other] = candidate
                    heapq.heappush(queue, (candidate, other))
        for end in endpoints:
            if end <= start or end not in distances:
                continue
            if best_length is None or distances[end] > best_length:
                best_length = distances[end]
                best_pair = (start, end)

    if best_pair is None:
        endpoint_distance = ""
        tortuosity = ""
        main_path_length = ""
    else:
        endpoint_distance = float(np.linalg.norm(
            np.asarray(best_pair[1], dtype=float) -
            np.asarray(best_pair[0], dtype=float)
        ))
        main_path_length = float(best_length)
        tortuosity = (float(best_length / endpoint_distance)
                      if endpoint_distance > 0 else "")
    return {
        "skeleton_pixels": int(len(points)),
        "skeleton_length": float(total_length),
        "main_path_length": main_path_length,
        "euclidean_end_distance": endpoint_distance,
        "tortuosity": tortuosity,
    }


def _label_bounds(labels):
    """Find all label bounding boxes in one image scan."""
    ys, xs = np.nonzero(labels)
    ids = labels[ys, xs].astype(np.intp)
    count = int(labels.max()) + 1
    x0 = np.full(count, labels.shape[1], dtype=np.int32)
    y0 = np.full(count, labels.shape[0], dtype=np.int32)
    x1 = np.full(count, -1, dtype=np.int32)
    y1 = np.full(count, -1, dtype=np.int32)
    np.minimum.at(x0, ids, xs)
    np.minimum.at(y0, ids, ys)
    np.maximum.at(x1, ids, xs)
    np.maximum.at(y1, ids, ys)
    return {
        final_id: (int(x0[final_id]), int(y0[final_id]),
                   int(x1[final_id]), int(y1[final_id]))
        for final_id in range(1, count) if x1[final_id] >= 0
    }


def final_instance_diagnostic_rows(final_labels, fitc, candidate_rows,
                                   merged, membership):
    """Build one diagnostic row per final label without mutating labels."""
    labels = np.asarray(final_labels)
    community_ids = sorted(set(membership.values()))
    bounds = _label_bounds(labels)
    final_ids = sorted(bounds)
    if len(community_ids) != len(final_ids):
        raise ValueError(
            "Cannot reliably map identity communities to final instances: "
            "{} communities, {} non-empty final labels".format(
                len(community_ids), len(final_ids)
            )
        )
    final_id_by_community = dict(zip(community_ids, final_ids))

    sources = {final_id: [] for final_id in final_ids}
    merged_ids = {final_id: set() for final_id in final_ids}
    kept_index = 0
    for row in candidate_rows:
        if not row["validation_passed"]:
            continue
        merged_id = int(row["merged_candidate_id"])
        group = merged[merged_id - 1]
        fragment_id = group.index(kept_index) + 1
        community_id = membership[
            "P{}:F{}".format(merged_id, fragment_id)
        ]
        final_id = final_id_by_community[community_id]
        sources[final_id].append(row)
        merged_ids[final_id].add(merged_id)
        kept_index += 1

    diagnostics = []
    height, width = labels.shape[:2]
    for final_id in final_ids:
        x0, y0, x1, y1 = bounds[final_id]
        local_mask = (
            labels[y0:y1 + 1, x0:x1 + 1] == final_id
        ).copy()
        bbox_width, bbox_height = x1 - x0 + 1, y1 - y0 + 1
        values = fitc[y0:y1 + 1, x0:x1 + 1][local_mask]
        source = sources[final_id]
        scores = [float(row["score"]) for row in source]
        lengths = [float(row["main_path_length"]) for row in source]
        community_id = next(
            cid for cid, iid in final_id_by_community.items() if iid == final_id
        )
        geometry = _skeleton_geometry(local_mask)
        diagnostics.append({
            "final_instance_id": final_id,
            "pixel_area": int(local_mask.sum()),
            "bbox_width": bbox_width,
            "bbox_height": bbox_height,
            "bbox_aspect_ratio": float(
                max(bbox_width, bbox_height) /
                max(1, min(bbox_width, bbox_height))
            ),
            **geometry,
            "mean_fitc": float(values.mean()),
            "integrated_fitc": float(values.sum()),
            "source_candidate_count": len(source),
            "source_candidate_ids": ";".join(
                str(row["candidate_id"]) for row in source
            ),
            "merged_candidate_count": len(merged_ids[final_id]),
            "identity_community_id": community_id,
            "source_candidate_min_score": min(scores),
            "source_candidate_max_score": max(scores),
            "source_candidate_mean_score": float(np.mean(scores)),
            "source_candidate_min_path_length": min(lengths),
            "source_candidate_max_path_length": max(lengths),
            "source_candidate_total_path_length": sum(lengths),
            "touches_image_border": bool(
                x0 == 0 or y0 == 0 or x1 == width - 1 or y1 == height - 1
            ),
            "manual_class": "",
            "manual_note": "",
        })
    return diagnostics, final_id_by_community


def final_instance_id_overlay(fitc, final_labels):
    """Render final IDs on a diagnostic copy of the final labels."""
    labels = np.asarray(final_labels)
    colors = np.random.default_rng(6023).integers(
        45, 256, (int(labels.max()) + 1, 3), dtype=np.uint8
    )
    out = base_view(fitc)
    foreground = labels > 0
    color_image = colors[labels]
    out[foreground] = (
        out[foreground].astype(np.uint16) +
        color_image[foreground].astype(np.uint16)
    ) // 2
    for final_id, (x0, y0, x1, y1) in _label_bounds(labels).items():
        local_mask = np.uint8(
            labels[y0:y1 + 1, x0:x1 + 1] == final_id
        )
        distance = cv2.distanceTransform(local_mask, cv2.DIST_L2, 3)
        local_y, local_x = np.unravel_index(
            int(np.argmax(distance)), distance.shape
        )
        x, y = x0 + local_x, y0 + local_y
        text = str(final_id)
        cv2.putText(out, text, (int(x), int(y)), cv2.FONT_HERSHEY_SIMPLEX,
                    0.55, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(out, text, (int(x), int(y)), cv2.FONT_HERSHEY_SIMPLEX,
                    0.55, (255, 255, 255), 1, cv2.LINE_AA)
    return out


def run_one(input_path, output_root, cfg, return_enhanced=False,
            candidate_path_mode="ordered"):
    pipeline_started = time.perf_counter()
    timings = {}
    debug_image_seconds = 0.0
    sample = input_path.stem[:-2] if input_path.stem.endswith("_G") else input_path.stem
    output = output_root / sample
    output.mkdir(parents=True, exist_ok=True)

    started = time.perf_counter()
    fitc, g8 = read_fitc(input_path)
    if fitc.ndim == 3:
        fitc = cv2.cvtColor(fitc, cv2.COLOR_BGR2GRAY)
    fitc = fitc.astype(np.float32)
    timings["fitc_read"] = time.perf_counter() - started

    started = time.perf_counter()
    enhanced = enhanced_mask(g8)
    timings["preprocess"] = time.perf_counter() - started
    if SAVE_DEBUG_IMAGES:
        started = time.perf_counter()
        cv2.imwrite(str(output / "01_fitc_enhanced.png"), enhanced)
        debug_image_seconds += time.perf_counter() - started
    started = time.perf_counter()
    skel = skeleton(enhanced)
    timings["skeletonize"] = time.perf_counter() - started
    if SAVE_DEBUG_IMAGES:
        started = time.perf_counter()
        cv2.imwrite(str(output / "02_skeleton.png"), skel)
        debug_image_seconds += time.perf_counter() - started

    graph = cfg["graph"]
    with tempfile.TemporaryDirectory(prefix="c01_graph_") as td_name:
        td = Path(td_name)
        mask_path, skeleton_path = td / "mask.png", td / "skeleton.png"
        cv2.imwrite(str(mask_path), enhanced)
        cv2.imwrite(str(skeleton_path), skel)
        ns = argparse.Namespace(mask=mask_path, skeleton=skeleton_path,
                                candidate_path_mode=candidate_path_mode, **graph)
        rows, paths = reconstruct(ns, fitc, performance_timings=timings)
    threshold = cfg["validation_threshold"]
    started = time.perf_counter()
    kept = [(r, p) for r, p in zip(rows, paths) if r["final_score"] >= threshold]
    kept_rows = [x[0] for x in kept]
    kept_paths = [x[1] for x in kept]
    timings["validation"] = time.perf_counter() - started
    if SAVE_DEBUG_IMAGES:
        started = time.perf_counter()
        cv2.imwrite(str(output / "03_graph_paths.png"), paths_view(fitc, paths, kept_paths))
        debug_image_seconds += time.perf_counter() - started

    merge_args = argparse.Namespace(**cfg["candidate_merging"])
    started = time.perf_counter()
    merged, assignment, _ = merge_candidates(kept_rows, kept_paths, fitc, merge_args)
    groups = [[kept_paths[i] for i in group] for group in merged]
    timings["candidate_merging"] = time.perf_counter() - started
    started = time.perf_counter()
    write_csv(output / "shadow_groups.csv",
              shadow_group_rows(kept_rows, kept_paths, merged))
    timings["shadow_group_diagnostics"] = time.perf_counter() - started
    grow_cfg = cfg["region_growing"]
    started = time.perf_counter()
    grown, _ = grow(fitc, groups, grow_cfg["max_distance"], grow_cfg["intensity_weight"], grow_cfg["direction_weight"])
    timings["region_growing"] = time.perf_counter() - started
    if SAVE_DEBUG_IMAGES:
        started = time.perf_counter()
        cols = np.random.default_rng(6022).integers(45, 256, (len(groups)+1, 3), dtype=np.uint8)
        cv2.imwrite(str(output / "04_region_growing.png"), instance_overlay(fitc, grown, cols))
        debug_image_seconds += time.perf_counter() - started

    started = time.perf_counter()
    nodes, edges = graph_data(groups, fitc)
    identity_cfg = cfg["identity_graph_v3"]
    membership, communities = cluster(nodes, edges, identity_cfg["community_resolution"],
                                      identity_cfg["seed"])
    timings["identity_graph"] = time.perf_counter() - started
    started = time.perf_counter()
    write_csv(output / "shadow_communities.csv", shadow_community_rows(
        kept_rows, merged, membership, communities
    ))
    timings["shadow_community_diagnostics"] = time.perf_counter() - started
    reconstruction_cfg = cfg["reconstruction"]
    started = time.perf_counter()
    final = identity_reconstruct(grown, fitc, groups, membership,
                                 reconstruction_cfg["intensity_weight"],
                                 reconstruction_cfg["direction_weight"])
    timings["instance_separation"] = time.perf_counter() - started
    if SAVE_DEBUG_IMAGES:
        started = time.perf_counter()
        final_cols = np.random.default_rng(6023).integers(45, 256, (int(final.max())+1, 3), dtype=np.uint8)
        cv2.imwrite(str(output / "05_separation_result.png"), instance_overlay(fitc, final, final_cols))
        debug_image_seconds += time.perf_counter() - started
    started = time.perf_counter()
    cv2.imwrite(str(output / "06_final_tail_instances.tif"), final)
    timings["final_label_save"] = time.perf_counter() - started
    timings["pipeline_to_label_save"] = time.perf_counter() - pipeline_started
    timings["debug_image_output"] = debug_image_seconds

    started = time.perf_counter()
    metrics = []
    for iid in range(1, int(final.max()) + 1):
        mask = final == iid
        length, branches = _mask_shape_metrics(mask)
        metrics.append({"instance_id": iid, "area": int(mask.sum()),
                        "FITC_integrated": float(fitc[mask].sum()), "skeleton_length": length,
                        "branch_points": branches})
    write_csv(output / "instance_metrics.csv", metrics,
              ["instance_id", "area", "FITC_integrated", "skeleton_length", "branch_points"])
    stats = {"skeleton_count": int(cv2.connectedComponents((skel > 0).astype(np.uint8), 8)[0]-1),
             "skeleton_pixels": int(np.count_nonzero(skel)), "graph_candidate_count": len(paths),
             "validation_count": len(kept_paths), "region_growing_instance_count": len(groups),
             "identity_graph_nodes": len(nodes), "identity_graph_edges": len(edges),
             "identity_communities": len(communities),
             "separation_before_count": int(grown.max()), "separation_after_count": int(final.max()),
             "max_instance_area": max((r["area"] for r in metrics), default=0)}
    write_csv(output / "run_statistics.csv", [stats])
    report = f"""# {sample} C-03 pipeline report

FITC enhancement → C skeleton → graph candidates → validation={threshold} → candidate merging → graph-seeded region growing → identity_graph_v3 (Louvain resolution={identity_cfg['community_resolution']:.2f}) → graph-constrained instance separation/reconstruction.

- Skeleton components: {stats['skeleton_count']}
- Skeleton pixels: {stats['skeleton_pixels']}
- Graph candidates: {stats['graph_candidate_count']}
- Validation retained: {stats['validation_count']}
- Region growing instances: {stats['region_growing_instance_count']}
- Identity graph nodes / edges: {stats['identity_graph_nodes']} / {stats['identity_graph_edges']}
- Identity communities: {stats['identity_communities']}
- Final instances: {stats['separation_after_count']}
- Max instance area: {stats['max_instance_area']}

TailFinalLabels was not read or used by this run.
"""
    (output / "report.md").write_text(report, encoding="utf-8")
    timings["final_output"] = time.perf_counter() - started

    started = time.perf_counter()
    provisional_candidate_diagnostics = candidate_diagnostic_rows(
        rows, paths, fitc, threshold, merged, assignment, membership
    )
    candidate_diagnostic_seconds = time.perf_counter() - started

    started = time.perf_counter()
    final_diagnostics, final_id_by_community = final_instance_diagnostic_rows(
        final.copy(), fitc, provisional_candidate_diagnostics, merged, membership
    )
    write_csv(output / "final_instance_diagnostics.csv", final_diagnostics)
    timings["final_instance_diagnostics"] = time.perf_counter() - started

    started = time.perf_counter()
    diagnostics = candidate_diagnostic_rows(
        rows, paths, fitc, threshold, merged, assignment, membership,
        final_id_by_community
    )
    write_csv(output / "candidate_diagnostics.csv", diagnostics)
    timings["candidate_diagnostics"] = (
        candidate_diagnostic_seconds + time.perf_counter() - started
    )

    started = time.perf_counter()
    overlay = final_instance_id_overlay(fitc, final.copy())
    cv2.imwrite(
        str(output / "final_instance_id_overlay.png"), overlay,
        [cv2.IMWRITE_PNG_COMPRESSION, 1]
    )
    timings["final_instance_overlay"] = time.perf_counter() - started
    timings["total"] = time.perf_counter() - pipeline_started
    counts = {
        "raw_candidate_count": len(paths),
        "validated_candidate_count": len(kept_paths),
        "merged_candidate_count": len(groups),
        "region_growing_instance_count": int(grown.max()),
        "final_instance_count": int(final.max()),
    }
    timing_payload = {
        "sample": sample,
        "stages_seconds": timings,
        "counts": counts,
    }
    (output / "timing.json").write_text(
        json.dumps(timing_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    for stage_name, seconds in timings.items():
        print(
            "[C18B_PERF] sample={} stage={} seconds={:.6f}".format(
                sample, stage_name, seconds
            ),
            flush=True,
        )
    print(input_path.name, stats)
    if return_enhanced:
        return output, stats, enhanced
    return output, stats


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path)
    args = parser.parse_args()
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    inputs = [args.input] if args.input else sorted((ROOT / "input" / "images").glob("*_RGB_G.tif"))
    if not inputs:
        raise SystemExit("No *_RGB_G.tif images found in input\\images")
    for path in inputs:
        run_one(path.resolve(), ROOT / "output", cfg)


if __name__ == "__main__":
    main()
