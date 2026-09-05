"""Post-grow separation of anomalous merged instances using original graph paths.

This module deliberately operates on the output of region growing.  It does not
change foreground enhancement, skeleton construction, validation, or seed
generation/merging.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np


@dataclass
class InstanceDiagnostic:
    instance_id: int
    area: int
    skeleton_length: int
    branch_count: int
    source_path_count: int
    abnormal_area: bool = False
    abnormal_skeleton: bool = False
    abnormal_branches: bool = False

    @property
    def abnormal(self) -> bool:
        return self.abnormal_area or self.abnormal_skeleton or self.abnormal_branches


def _mask_shape_metrics(mask: np.ndarray) -> tuple[int, int]:
    skel = cv2.ximgproc.thinning(mask.astype(np.uint8) * 255) > 0
    neighbours = cv2.filter2D(skel.astype(np.uint8), cv2.CV_16S,
                              np.ones((3, 3), np.uint8)) - skel
    junctions = ((neighbours >= 3) & skel).astype(np.uint8)
    branches = cv2.connectedComponents(junctions, connectivity=8)[0] - 1
    return int(skel.sum()), int(branches)


def _upper_outlier(values: np.ndarray, floor: float = 0.) -> float:
    """Conservative robust upper fence, stable for a small instance sample."""
    q1, q3 = np.percentile(values, [25, 75])
    median = float(np.median(values))
    mad = float(np.median(np.abs(values - median)))
    return max(float(floor), float(q3 + 3. * (q3 - q1)), median + 6. * mad)


def diagnose(labels: np.ndarray, groups: list[list[np.ndarray]]) -> list[InstanceDiagnostic]:
    rows = []
    for iid, group in enumerate(groups, 1):
        mask = labels == iid
        length, branches = _mask_shape_metrics(mask)
        rows.append(InstanceDiagnostic(iid, int(mask.sum()), length, branches, len(group)))
    areas = np.asarray([r.area for r in rows], float)
    lengths = np.asarray([r.skeleton_length for r in rows], float)
    branch = np.asarray([r.branch_count for r in rows], float)
    area_hi = _upper_outlier(areas, 4_000.)
    length_hi = _upper_outlier(lengths, 600.)
    branch_hi = _upper_outlier(branch, 8.)
    for r in rows:
        r.abnormal_area = r.area > area_hi
        r.abnormal_skeleton = r.skeleton_length > length_hi
        r.abnormal_branches = r.branch_count > branch_hi
    return rows


def _path_geometry(path: np.ndarray, shape: tuple[int, int],
                   coordinate_grid: Optional[np.ndarray] = None
                   ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Distance and nearest-path tangent for every image pixel."""
    seed = np.zeros(shape, np.uint8)
    tangent_x = np.zeros(shape, np.float32)
    tangent_y = np.zeros(shape, np.float32)
    for k, (x, y) in enumerate(path):
        if not (0 <= x < shape[1] and 0 <= y < shape[0]):
            continue
        a = path[max(0, k - 6)].astype(float)
        b = path[min(len(path) - 1, k + 6)].astype(float)
        v = b - a
        norm = max(float(np.linalg.norm(v)), 1.)
        seed[y, x] = 1
        tangent_x[y, x], tangent_y[y, x] = v / norm
    # OpenCV labels each pixel by its nearest zero pixel. Convert seed to zeros.
    distance, nearest = cv2.distanceTransformWithLabels(1 - seed, cv2.DIST_L2, 5,
                                                        labelType=cv2.DIST_LABEL_PIXEL)
    ys, xs = np.where(seed)
    # DIST_LABEL_PIXEL uses consecutive labels in row-major order for zero pixels.
    order = np.lexsort((xs, ys)); ys, xs = ys[order], xs[order]
    lut_x = np.zeros(len(xs) + 1, np.float32); lut_y = np.zeros(len(xs) + 1, np.float32)
    lut_tx = np.zeros(len(xs) + 1, np.float32); lut_ty = np.zeros(len(xs) + 1, np.float32)
    lut_x[1:], lut_y[1:] = xs, ys
    lut_tx[1:], lut_ty[1:] = tangent_x[ys, xs], tangent_y[ys, xs]
    np.minimum(nearest, len(xs), out=nearest)
    if coordinate_grid is None:
        vx = np.indices(shape, dtype=np.float32)[1] - lut_x[nearest]
        vy = np.indices(shape, dtype=np.float32)[0] - lut_y[nearest]
    else:
        if coordinate_grid.shape != (2,) + shape:
            raise ValueError("coordinate_grid shape does not match image shape")
        vx = coordinate_grid[1] - lut_x[nearest]
        vy = coordinate_grid[0] - lut_y[nearest]
    align = (np.abs(vx * lut_tx[nearest] + vy * lut_ty[nearest]) /
             np.maximum(distance, 1.))
    np.clip(align, 0., 1., out=align)
    return distance, align, seed


def _axis_angle(a: np.ndarray, b: np.ndarray) -> float:
    va = a[-1].astype(float) - a[0]
    vb = b[-1].astype(float) - b[0]
    cosine = abs(float(np.dot(va, vb))) / max(float(np.linalg.norm(va) * np.linalg.norm(vb)), 1e-9)
    return float(np.degrees(np.arccos(np.clip(cosine, 0., 1.))))


def _endpoint_continuation(a: np.ndarray, b: np.ndarray) -> tuple[float, float]:
    """Return closest endpoint gap and tangent mismatch for a possible smooth join."""
    candidates = []
    window = 12
    for ia, inward_a in ((0, min(window, len(a) - 1)), (-1, -1 - min(window, len(a) - 1))):
        for ib, inward_b in ((0, min(window, len(b) - 1)), (-1, -1 - min(window, len(b) - 1))):
            pa, pb = a[ia].astype(float), b[ib].astype(float)
            gap = float(np.linalg.norm(pb - pa))
            # Outward tangents must face each other and lie on one smooth axis.
            ta = pa - a[inward_a].astype(float)
            tb = pb - b[inward_b].astype(float)
            connector = pb - pa
            if gap < 1e-6:
                mismatch = _axis_angle(a, b)
            else:
                ca = float(np.dot(ta, connector)) / max(float(np.linalg.norm(ta) * gap), 1e-9)
                cb = float(np.dot(tb, -connector)) / max(float(np.linalg.norm(tb) * gap), 1e-9)
                mismatch = max(float(np.degrees(np.arccos(np.clip(ca, -1., 1.)))),
                               float(np.degrees(np.arccos(np.clip(cb, -1., 1.)))))
            candidates.append((gap, mismatch))
    return min(candidates, key=lambda value: value[0])


def fragment_pair_features(a: np.ndarray, b: np.ndarray, fitc: np.ndarray,
                           region: np.ndarray | None = None) -> dict[str, float | str]:
    """GT-free evidence describing whether two fragments form one trajectory."""
    sa = a[::max(1, len(a) // 180)].astype(float)
    sb = b[::max(1, len(b) // 180)].astype(float)
    distances = np.sqrt(((sa[:, None] - sb[None, :]) ** 2).sum(2))
    minimum = float(distances.min())
    overlap = float(max(np.mean(distances.min(1) <= 5.), np.mean(distances.min(0) <= 5.)))
    gap, continuation = _endpoint_continuation(a, b)
    axis = _axis_angle(a, b)

    endpoint_pairs = [(a[ia], b[ib], ia, ib) for ia in (0, -1) for ib in (0, -1)]
    pa, pb, ia, ib = min(endpoint_pairs, key=lambda z: np.linalg.norm(z[0].astype(float) - z[1]))
    n = max(2, int(np.linalg.norm(pa.astype(float) - pb.astype(float))) + 1)
    xs = np.clip(np.rint(np.linspace(pa[0], pb[0], n)).astype(int), 0, fitc.shape[1] - 1)
    ys = np.clip(np.rint(np.linspace(pa[1], pb[1], n)).astype(int), 0, fitc.shape[0] - 1)
    av = fitc[a[:, 1], a[:, 0]]; bv = fitc[b[:, 1], b[:, 0]]
    native = max(float(np.median(np.r_[av, bv])), 1.)
    bridge_values = fitc[ys, xs]
    support = float(np.clip(np.percentile(bridge_values, 25) / native, 0., 2.))
    background = float(np.mean(bridge_values < .25 * native))

    width_diff = 0.
    if region is not None:
        width = cv2.distanceTransform(region.astype(np.uint8), cv2.DIST_L2, 5)
        wa = float(np.median(width[a[:, 1], a[:, 0]])); wb = float(np.median(width[b[:, 1], b[:, 0]]))
        width_diff = abs(wa - wb) / max(wa, wb, 1.)

    # Close contact away from both endpoints is characteristic of a crossing
    # or parallel overlap, rather than an end-to-end continuation.
    ai, bi = np.unravel_index(int(np.argmin(distances)), distances.shape)
    a_end = min(ai, len(sa) - 1 - ai) <= max(1, len(sa) // 12)
    b_end = min(bi, len(sb) - 1 - bi) <= max(1, len(sb) // 12)
    relation = "endpoint" if a_end and b_end else ("crossing" if minimum <= 6. and axis >= 25. else "parallel")
    return {"endpoint_min_distance": gap, "minimum_spatial_distance": minimum,
            "endpoint_tangent_angle": continuation, "main_direction_difference": axis,
            "smooth_continuation_angle": continuation, "bridge_FITC_support": support,
            "bridge_background_ratio": background, "local_width_difference": width_diff,
            "path_overlap": overlap, "branch/crossing_relation": relation,
            "path_length_a": int(len(a)), "path_length_b": int(len(b))}


def _compatibility(f: dict[str, float | str]) -> float:
    """Multi-evidence same-tail score. No single distance gate can accept a pair."""
    gap = float(f["endpoint_min_distance"]); spatial = float(f["minimum_spatial_distance"])
    turn = float(f["smooth_continuation_angle"]); axis = float(f["main_direction_difference"])
    support = float(f["bridge_FITC_support"]); bg = float(f["bridge_background_ratio"])
    width = float(f["local_width_difference"]); overlap = float(f["path_overlap"])
    relation = str(f["branch/crossing_relation"])
    endpoint_evidence = (1.8 * np.exp(-gap / 75.) + 1.8 * np.exp(-turn / 24.) +
                         .9 * min(support, 1.) - .9 * bg - .6 * width)
    overlap_evidence = 2.2 * overlap + 1.0 * np.exp(-spatial / 5.) + .8 * np.exp(-axis / 22.)
    score = max(endpoint_evidence, overlap_evidence)
    if relation == "crossing": score -= 1.5 + .8 * (axis / 90.)
    if axis > 55.: score -= .9
    if turn > 65.: score -= 1.2
    if gap > 160. and support < .35: score -= 1.0
    return float(score)


def path_clusters(group: list[np.ndarray], fitc: np.ndarray, region: np.ndarray
                  ) -> tuple[list[list[int]], dict[tuple[int, int], dict[str, float | str]]]:
    """Conservative hierarchical clustering using complete-link compatibility."""
    pair = {(i, j): fragment_pair_features(group[i], group[j], fitc, region)
            for i in range(len(group)) for j in range(i + 1, len(group))}
    clusters = [[i] for i in range(len(group))]
    # Complete-link prevents a permissive transitive chain from joining two
    # tails merely because both touch a crossing fragment.
    while True:
        best = None
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                scores = [_compatibility(pair[min(a, b), max(a, b)]) for a in clusters[i] for b in clusters[j]]
                value = min(scores)
                if value >= 2.10 and (best is None or value > best[0]): best = (value, i, j)
        if best is None: break
        _, i, j = best; clusters[i] += clusters[j]; del clusters[j]
    return clusters, pair


def _path_clusters(group: list[np.ndarray], fitc: np.ndarray | None = None,
                   region: np.ndarray | None = None) -> list[list[int]]:
    """Compatibility wrapper for callers using the original API."""
    if fitc is None or region is None:
        return [[i] for i in range(len(group))]
    return path_clusters(group, fitc, region)[0]


def separate(labels: np.ndarray, fitc: np.ndarray, groups: list[list[np.ndarray]],
             intensity_weight: float = 3., direction_weight: float = .8
             ) -> tuple[np.ndarray, list[InstanceDiagnostic], dict[int, list[int]]]:
    """Reassign anomalous-instance pixels among its unmerged source graph paths."""
    diagnostics = diagnose(labels, groups)
    out = np.zeros_like(labels, dtype=np.uint16)
    split_map: dict[int, list[int]] = {}
    next_id = 1
    coordinate_grid = np.indices(fitc.shape, dtype=np.float32)
    for row, group in zip(diagnostics, groups):
        region = labels == row.instance_id
        if not row.abnormal or len(group) < 2:
            out[region] = next_id
            split_map[row.instance_id] = [next_id]
            next_id += 1
            continue

        clusters, pair_features = path_clusters(group, fitc, region)
        if len(clusters) < 2:
            out[region] = next_id
            split_map[row.instance_id] = [next_id]
            next_id += 1
            continue

        costs = []
        cluster_seeds = []
        for cluster in clusters:
            geometries = [
                _path_geometry(group[k], fitc.shape, coordinate_grid)
                for k in cluster
            ]
            distance = np.min(np.stack([g[0] for g in geometries]), axis=0)
            nearest = np.argmin(np.stack([g[0] for g in geometries]), axis=0)
            direction = np.take_along_axis(np.stack([g[1] for g in geometries]), nearest[None], axis=0)[0]
            seed = np.maximum.reduce([g[2] for g in geometries])
            cluster_seeds.append(seed > 0)
            seed_values = fitc[seed > 0]
            level = max(float(np.median(seed_values)), 1.)
            # The photometric term rejects assignment to a graph whose native
            # intensity is unlike the pixel; direction penalizes travel along a
            # path tangent, matching the region grower's crossing constraint.
            intensity = np.abs(fitc - level) / level
            costs.append(distance + intensity_weight * intensity * np.maximum(distance, 1.)
                         + direction_weight * direction * np.maximum(distance, 1.))
        winner = np.argmin(np.stack(costs), axis=0)
        proposed = [region & (winner == k) for k in range(len(clusters))]
        parent_area = int(region.sum())
        sane = []
        for child, seed in zip(proposed, cluster_seeds):
            length, _ = _mask_shape_metrics(child)
            seed_coverage = float(np.count_nonzero(child & seed) / max(1, np.count_nonzero(seed & region)))
            sane.append(int(child.sum()) >= max(180, int(.015 * parent_area)) and
                        length >= 25 and seed_coverage >= .35)
        # Local fallback only: absorb a failed cluster into its most compatible
        # sane neighbour; if none is convincing, retain it as residual/uncertain.
        for bad in [k for k, ok in enumerate(sane) if not ok]:
            choices = []
            for good in [k for k, ok in enumerate(sane) if ok]:
                scores = [_compatibility(pair_features[min(a, b), max(a, b)])
                          for a in clusters[bad] for b in clusters[good]]
                choices.append((max(scores), good))
            if choices and max(choices)[0] >= 2.35:
                _, target = max(choices)
                proposed[target] |= proposed[bad]
                proposed[bad] = np.zeros_like(region)

        ids = []
        for child in proposed:
            if not child.any():
                continue
            out[child] = next_id; ids.append(next_id); next_id += 1
        split_map[row.instance_id] = ids
    return out, diagnostics, split_map
