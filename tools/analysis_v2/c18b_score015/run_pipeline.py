"""Unified entry point for the frozen C-03 identity_graph_v3 pipeline."""
from __future__ import annotations

import argparse
import csv
import json
import tempfile
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


def run_one(input_path, output_root, cfg):
    sample = input_path.stem[:-2] if input_path.stem.endswith("_G") else input_path.stem
    output = output_root / sample
    output.mkdir(parents=True, exist_ok=True)

    _, g8 = read_fitc(input_path)
    fitc = cv2.imread(str(input_path), cv2.IMREAD_UNCHANGED)
    if fitc is None:
        raise FileNotFoundError(input_path)
    if fitc.ndim == 3:
        fitc = cv2.cvtColor(fitc, cv2.COLOR_BGR2GRAY)
    fitc = fitc.astype(np.float32)

    enhanced = enhanced_mask(g8)
    cv2.imwrite(str(output / "01_fitc_enhanced.png"), enhanced)
    skel = skeleton(enhanced)
    cv2.imwrite(str(output / "02_skeleton.png"), skel)

    graph = cfg["graph"]
    with tempfile.TemporaryDirectory(prefix="c01_graph_") as td_name:
        td = Path(td_name)
        mask_path, skeleton_path = td / "mask.png", td / "skeleton.png"
        cv2.imwrite(str(mask_path), enhanced)
        cv2.imwrite(str(skeleton_path), skel)
        ns = argparse.Namespace(mask=mask_path, skeleton=skeleton_path, **graph)
        rows, paths = reconstruct(ns, fitc)
    threshold = cfg["validation_threshold"]
    kept = [(r, p) for r, p in zip(rows, paths) if r["final_score"] >= threshold]
    kept_rows = [x[0] for x in kept]
    kept_paths = [x[1] for x in kept]
    cv2.imwrite(str(output / "03_graph_paths.png"), paths_view(fitc, paths, kept_paths))

    merge_args = argparse.Namespace(**cfg["candidate_merging"])
    merged, _, _ = merge_candidates(kept_rows, kept_paths, fitc, merge_args)
    groups = [[kept_paths[i] for i in group] for group in merged]
    grow_cfg = cfg["region_growing"]
    grown, _ = grow(fitc, groups, grow_cfg["max_distance"], grow_cfg["intensity_weight"], grow_cfg["direction_weight"])
    cols = np.random.default_rng(6022).integers(45, 256, (len(groups)+1, 3), dtype=np.uint8)
    cv2.imwrite(str(output / "04_region_growing.png"), instance_overlay(fitc, grown, cols))

    nodes, edges = graph_data(groups, fitc)
    identity_cfg = cfg["identity_graph_v3"]
    membership, communities = cluster(nodes, edges, identity_cfg["community_resolution"],
                                      identity_cfg["seed"])
    reconstruction_cfg = cfg["reconstruction"]
    final = identity_reconstruct(grown, fitc, groups, membership,
                                 reconstruction_cfg["intensity_weight"],
                                 reconstruction_cfg["direction_weight"])
    final_cols = np.random.default_rng(6023).integers(45, 256, (int(final.max())+1, 3), dtype=np.uint8)
    cv2.imwrite(str(output / "05_separation_result.png"), instance_overlay(fitc, final, final_cols))
    cv2.imwrite(str(output / "06_final_tail_instances.tif"), final)

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
    print(input_path.name, stats)
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
