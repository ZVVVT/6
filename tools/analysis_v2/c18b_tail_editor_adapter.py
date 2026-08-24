"""Convert C18B results into inputs for the manual tail editor only.

This adapter does not launch the editor, create TailFinal files, run
measurement, or invoke any legacy tail_joint stage.
"""

import argparse
import hashlib
import json
import os
import tempfile
import time
from collections import deque
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

import cv2
import numpy as np


ADAPTER_VERSION = "c18b_tail_editor_adapter_v1"
DILATION_RADIUS_PX = 20
MAX_MATCHING_DISTANCE_PX = 80.0
CENTERLINE_ROI_MARGIN_PX = 2
NEIGHBOURS_8 = (
    (-1, -1), (-1, 0), (-1, 1),
    (0, -1), (0, 1),
    (1, -1), (1, 0), (1, 1),
)


def read_label(path: Path) -> np.ndarray:
    label_path = Path(path).expanduser().resolve()
    if not label_path.is_file():
        raise FileNotFoundError("标签图不存在：{}".format(label_path))
    labels = cv2.imread(str(label_path), cv2.IMREAD_UNCHANGED)
    if labels is None:
        raise ValueError("OpenCV无法读取标签图：{}".format(label_path))
    if labels.ndim != 2:
        raise ValueError("标签图必须是二维单通道：{}".format(labels.shape))
    if not np.issubdtype(labels.dtype, np.integer):
        raise ValueError("标签图必须是整数类型：{}".format(labels.dtype))
    if np.any(labels < 0):
        raise ValueError("标签图不能包含负数：{}".format(label_path))
    return labels


def positive_ids(labels: np.ndarray) -> List[int]:
    return [int(value) for value in np.unique(labels[labels > 0]).tolist()]


def nearest_distances(
    query_yx: np.ndarray,
    target_yx: np.ndarray,
    query_block_size: int = 1024,
    target_block_size: int = 1024,
) -> np.ndarray:
    """Return exact nearest Euclidean distances using bounded NumPy blocks."""
    queries = np.asarray(query_yx, dtype=np.float32).reshape(-1, 2)
    targets = np.asarray(target_yx, dtype=np.float32).reshape(-1, 2)
    if not len(targets):
        raise ValueError("最近距离计算缺少目标像素。")
    result = np.empty(len(queries), dtype=np.float32)
    for query_start in range(0, len(queries), int(query_block_size)):
        query_block = queries[query_start:query_start + int(query_block_size)]
        minimum_squared = np.full(len(query_block), np.inf, dtype=np.float32)
        for target_start in range(0, len(targets), int(target_block_size)):
            target_block = targets[target_start:target_start + int(target_block_size)]
            differences = query_block[:, None, :] - target_block[None, :, :]
            squared = np.sum(differences * differences, axis=2)
            minimum_squared = np.minimum(minimum_squared, np.min(squared, axis=1))
        result[query_start:query_start + len(query_block)] = np.sqrt(minimum_squared)
    return result


def maximum_weight_assignment(score_matrix: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Return a maximum-weight rectangular assignment using NumPy only."""
    scores = np.asarray(score_matrix, dtype=np.float64)
    row_count, column_count = scores.shape
    if row_count > column_count:
        raise ValueError("分配矩阵列数不能小于行数。")
    if row_count == 0:
        empty = np.empty(0, dtype=np.int64)
        return empty, empty
    costs = float(np.max(scores)) - scores
    row_potential = np.zeros(row_count + 1, dtype=np.float64)
    column_potential = np.zeros(column_count + 1, dtype=np.float64)
    column_match = np.zeros(column_count + 1, dtype=np.int64)
    predecessor = np.zeros(column_count + 1, dtype=np.int64)
    for row in range(1, row_count + 1):
        column_match[0] = row
        minimum = np.full(column_count + 1, np.inf, dtype=np.float64)
        used = np.zeros(column_count + 1, dtype=bool)
        column = 0
        while True:
            used[column] = True
            matched_row = int(column_match[column])
            delta = np.inf
            next_column = 0
            for candidate in range(1, column_count + 1):
                if used[candidate]:
                    continue
                reduced = (
                    costs[matched_row - 1, candidate - 1]
                    - row_potential[matched_row]
                    - column_potential[candidate]
                )
                if reduced < minimum[candidate]:
                    minimum[candidate] = reduced
                    predecessor[candidate] = column
                if minimum[candidate] < delta:
                    delta = minimum[candidate]
                    next_column = candidate
            for candidate in range(column_count + 1):
                if used[candidate]:
                    row_potential[column_match[candidate]] += delta
                    column_potential[candidate] -= delta
                else:
                    minimum[candidate] -= delta
            column = next_column
            if column_match[column] == 0:
                break
        while True:
            previous = int(predecessor[column])
            column_match[column] = column_match[previous]
            column = previous
            if column == 0:
                break
    assigned_columns = np.empty(row_count, dtype=np.int64)
    for column in range(1, column_count + 1):
        matched_row = int(column_match[column])
        if matched_row:
            assigned_columns[matched_row - 1] = column - 1
    return np.arange(row_count, dtype=np.int64), assigned_columns


def match_instances(
    instances: np.ndarray,
    head_labels: np.ndarray,
    dilation_radius: int,
    maximum_distance: float,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    kernel_size = 2 * int(dilation_radius) + 1
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
    instance_ids = positive_ids(instances)
    head_ids = positive_ids(head_labels)
    if not head_ids:
        raise ValueError("HeadFinalLabels没有非零头部对象。")
    boundary_kernel = np.ones((3, 3), dtype=np.uint8)
    head_boundaries: Dict[int, np.ndarray] = {}
    head_pixel_counts: Dict[int, int] = {}
    for head_id in head_ids:
        head_mask = (head_labels == head_id).astype(np.uint8)
        eroded = cv2.erode(head_mask, boundary_kernel, iterations=1)
        head_boundaries[head_id] = np.argwhere((head_mask > 0) & (eroded == 0))
        head_pixel_counts[head_id] = int(np.count_nonzero(head_mask))

    proposals_by_instance: Dict[int, List[Dict[str, Any]]] = {}
    no_candidate_ids = set()
    for instance_id in instance_ids:
        instance_mask = instances == instance_id
        tail_pixels = int(np.count_nonzero(instance_mask))
        dilated = cv2.dilate(instance_mask.astype(np.uint8), kernel, iterations=1) > 0
        overlapping = head_labels[dilated]
        overlapping = overlapping[overlapping > 0].astype(np.int64, copy=False)
        overlap_counts: Dict[int, int] = {}
        if overlapping.size:
            overlap_ids, counts = np.unique(overlapping, return_counts=True)
            overlap_counts = dict(
                (int(head_id), int(count))
                for head_id, count in zip(overlap_ids.tolist(), counts.tolist())
            )
        tail_uint8 = instance_mask.astype(np.uint8)
        tail_eroded = cv2.erode(tail_uint8, boundary_kernel, iterations=1)
        boundary_yx = np.argwhere((tail_uint8 > 0) & (tail_eroded == 0))
        skeleton_points = np.empty((0, 2), dtype=np.int64)
        ximgproc = getattr(cv2, "ximgproc", None)
        if ximgproc is not None and hasattr(ximgproc, "thinning"):
            skeleton = ximgproc.thinning(tail_uint8 * 255) > 0
            neighbours = cv2.filter2D(
                skeleton.astype(np.uint8), cv2.CV_16U, np.ones((3, 3), dtype=np.uint8)
            )
            skeleton_points = np.argwhere(skeleton & (neighbours == 2))
        if len(boundary_yx) > 2048:
            indices = np.linspace(0, len(boundary_yx) - 1, 2048).astype(np.int64)
            boundary_yx = boundary_yx[indices]
        sample_yx = np.vstack((boundary_yx, skeleton_points)).astype(np.float32)
        rows: List[Dict[str, Any]] = []
        size_score = min(1.0, np.log1p(float(tail_pixels)) / np.log1p(10000.0))
        for head_id in head_ids:
            distance = float(np.min(nearest_distances(sample_yx, head_boundaries[head_id])))
            overlap_count = int(overlap_counts.get(head_id, 0))
            if overlap_count > 0:
                overlap_score = min(
                    1.0, overlap_count / float(max(1, head_pixel_counts[head_id]))
                )
                confidence = min(1.0, 0.75 + 0.20 * overlap_score + 0.05 * size_score)
                assignment_score = 2.0 + confidence
                method = "dilated_overlap_20px"
            elif distance <= float(maximum_distance):
                distance_score = max(0.0, 1.0 - distance / float(maximum_distance))
                confidence = min(0.74, 0.10 + 0.58 * distance_score + 0.06 * size_score)
                assignment_score = confidence
                method = "boundary_endpoint_distance"
            else:
                continue
            rows.append({
                "c18b_instance_id": instance_id,
                "head_id": head_id,
                "overlap_pixel_count": overlap_count,
                "tail_pixel_count": tail_pixels,
                "matching_method": method,
                "matching_distance_px": distance,
                "confidence": float(confidence),
                "assignment_score": float(assignment_score),
            })
        proposals_by_instance[instance_id] = rows
        if not rows:
            no_candidate_ids.add(instance_id)

    row_count = len(instance_ids)
    head_count = len(head_ids)
    score_matrix = np.full((row_count, head_count + row_count), -1000000.0)
    proposal_lookup: Dict[Tuple[int, int], Dict[str, Any]] = {}
    head_columns = dict((head_id, index) for index, head_id in enumerate(head_ids))
    for row_index, instance_id in enumerate(instance_ids):
        score_matrix[row_index, head_count + row_index] = 0.0
        for proposal in proposals_by_instance.get(instance_id, []):
            column = head_columns[int(proposal["head_id"])]
            score_matrix[row_index, column] = float(proposal["assignment_score"])
            proposal_lookup[(row_index, column)] = proposal
    assigned_rows, assigned_columns = maximum_weight_assignment(score_matrix)
    matched: List[Dict[str, Any]] = []
    matched_instance_ids = set()
    for row_index, column in zip(assigned_rows.tolist(), assigned_columns.tolist()):
        proposal = proposal_lookup.get((row_index, column))
        if proposal is not None and float(proposal["assignment_score"]) > 0.0:
            matched.append(proposal)
            matched_instance_ids.add(int(proposal["c18b_instance_id"]))

    unmatched: List[Dict[str, Any]] = []
    for instance_id in instance_ids:
        if instance_id in matched_instance_ids:
            continue
        rows = proposals_by_instance.get(instance_id, [])
        if instance_id in no_candidate_ids:
            unmatched.append({
                "c18b_instance_id": instance_id,
                "reason": "no_head_within_maximum_distance",
                "maximum_distance_px": float(maximum_distance),
            })
        else:
            best = max(rows, key=lambda row: float(row["assignment_score"]))
            unmatched.append({
                "c18b_instance_id": instance_id,
                "reason": "all_candidate_heads_assigned_to_better_tail",
                "best_candidate_head_id": int(best["head_id"]),
                "matching_method": str(best["matching_method"]),
                "matching_distance_px": float(best["matching_distance_px"]),
                "overlap_pixel_count": int(best["overlap_pixel_count"]),
                "confidence": float(best["confidence"]),
            })
    for row in matched:
        row.pop("assignment_score", None)
    matched.sort(key=lambda row: int(row["c18b_instance_id"]))
    unmatched.sort(key=lambda row: int(row["c18b_instance_id"]))
    return matched, unmatched


def read_image(path: Path, name: str) -> np.ndarray:
    image_path = Path(path).expanduser().resolve()
    if not image_path.is_file():
        raise FileNotFoundError("{}不存在：{}".format(name, image_path))
    image = cv2.imread(str(image_path), cv2.IMREAD_UNCHANGED)
    if image is None:
        raise ValueError("OpenCV无法读取{}：{}".format(name, image_path))
    return image


def image_shape(image: np.ndarray) -> Tuple[int, int]:
    if image.ndim not in (2, 3):
        raise ValueError("图像必须是二维或三维：{}".format(image.shape))
    return int(image.shape[0]), int(image.shape[1])


def probability_plane(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        return image
    if image.ndim == 3 and image.shape[2] == 1:
        return image[..., 0]
    raise ValueError("C18B probability必须是单通道图：{}".format(image.shape))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def write_json_atomic(path: Path, payload: Dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix="." + target.stem + ".",
        suffix=".tmp.json",
        dir=str(target.parent),
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        json.loads(temporary.read_text(encoding="utf-8"))
        os.replace(str(temporary), str(target))
    finally:
        if temporary.exists():
            temporary.unlink()


def write_image_atomic(path: Path, image: np.ndarray) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix="." + target.stem + ".",
        suffix=".tmp.tif",
        dir=str(target.parent),
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        if not cv2.imwrite(str(temporary), image):
            raise OSError("无法写入临时图像：{}".format(temporary))
        verified = cv2.imread(str(temporary), cv2.IMREAD_UNCHANGED)
        if verified is None or not np.array_equal(verified, image):
            raise ValueError("图像回读校验失败：{}".format(temporary))
        os.replace(str(temporary), str(target))
    finally:
        if temporary.exists():
            temporary.unlink()


def skeletonize(mask: np.ndarray) -> np.ndarray:
    binary = (np.asarray(mask) > 0).astype(np.uint8) * 255
    ximgproc = getattr(cv2, "ximgproc", None)
    if ximgproc is not None and hasattr(ximgproc, "thinning"):
        return ximgproc.thinning(binary) > 0

    # The deployed MvImageID environment may not contain opencv-contrib.
    # Use the standard two-pass Zhang-Suen thinning rules as a dependency-free
    # fallback.  This operates only inside one C18B instance mask.
    working = (binary > 0).astype(np.uint8)
    changed = True
    while changed:
        changed = False
        for phase in (0, 1):
            padded = np.pad(working, 1, mode="constant")
            p2 = padded[:-2, 1:-1]
            p3 = padded[:-2, 2:]
            p4 = padded[1:-1, 2:]
            p5 = padded[2:, 2:]
            p6 = padded[2:, 1:-1]
            p7 = padded[2:, :-2]
            p8 = padded[1:-1, :-2]
            p9 = padded[:-2, :-2]
            neighbours = p2 + p3 + p4 + p5 + p6 + p7 + p8 + p9
            transitions = (
                ((p2 == 0) & (p3 == 1)).astype(np.uint8)
                + ((p3 == 0) & (p4 == 1)).astype(np.uint8)
                + ((p4 == 0) & (p5 == 1)).astype(np.uint8)
                + ((p5 == 0) & (p6 == 1)).astype(np.uint8)
                + ((p6 == 0) & (p7 == 1)).astype(np.uint8)
                + ((p7 == 0) & (p8 == 1)).astype(np.uint8)
                + ((p8 == 0) & (p9 == 1)).astype(np.uint8)
                + ((p9 == 0) & (p2 == 1)).astype(np.uint8)
            )
            if phase == 0:
                triplet_a = p2 * p4 * p6
                triplet_b = p4 * p6 * p8
            else:
                triplet_a = p2 * p4 * p8
                triplet_b = p2 * p6 * p8
            remove = (
                (working == 1)
                & (neighbours >= 2)
                & (neighbours <= 6)
                & (transitions == 1)
                & (triplet_a == 0)
                & (triplet_b == 0)
            )
            if np.any(remove):
                working[remove] = 0
                changed = True
    return working > 0


def head_records(head_labels: np.ndarray) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for head_id in positive_ids(head_labels):
        y, x = np.nonzero(head_labels == head_id)
        records.append({
            "head_id": int(head_id),
            "center_x": float(x.mean()),
            "center_y": float(y.mean()),
            "status": "manual_required",
        })
    return records


def closest_component_to_head(
    skeleton: np.ndarray,
    head_points_yx: np.ndarray,
) -> np.ndarray:
    component_count, components = cv2.connectedComponents(
        skeleton.astype(np.uint8),
        connectivity=8,
    )
    best_points = np.empty((0, 2), dtype=np.int32)
    best_distance = float("inf")
    for component_id in range(1, int(component_count)):
        points = np.argwhere(components == component_id)
        if not len(points):
            continue
        distances = nearest_distances(points, head_points_yx)
        distance = float(np.min(distances))
        if distance < best_distance:
            best_distance = distance
            best_points = points.astype(np.int32, copy=False)
    return best_points


def ordered_centerline(
    instance_mask: np.ndarray,
    head_mask: np.ndarray,
    timings: Dict[str, float] = None,
) -> List[List[float]]:
    ordered_started = time.perf_counter()
    instance_y, instance_x = np.nonzero(instance_mask)
    if not len(instance_y):
        if timings is not None:
            timings["ordered_centerline_seconds"] += (
                time.perf_counter() - ordered_started
            )
        return []

    height, width = instance_mask.shape
    margin = CENTERLINE_ROI_MARGIN_PX
    y_offset = max(0, int(instance_y.min()) - margin)
    y_limit = min(height, int(instance_y.max()) + margin + 1)
    x_offset = max(0, int(instance_x.min()) - margin)
    x_limit = min(width, int(instance_x.max()) + margin + 1)
    instance_roi = instance_mask[y_offset:y_limit, x_offset:x_limit]

    skeleton_started = time.perf_counter()
    skeleton = skeletonize(instance_roi)
    skeleton_elapsed = time.perf_counter() - skeleton_started
    head_points = np.argwhere(head_mask).astype(np.int64, copy=False)
    if len(head_points):
        head_points[:, 0] -= y_offset
        head_points[:, 1] -= x_offset
    if not np.any(skeleton) or not len(head_points):
        if timings is not None:
            timings["skeletonize_seconds"] += skeleton_elapsed
            timings["ordered_centerline_seconds"] += (
                time.perf_counter() - ordered_started
            )
        return []

    component_points = closest_component_to_head(skeleton, head_points)
    if not len(component_points):
        if timings is not None:
            timings["skeletonize_seconds"] += skeleton_elapsed
            timings["ordered_centerline_seconds"] += (
                time.perf_counter() - ordered_started
            )
        return []
    component = set(
        (int(point[0]), int(point[1])) for point in component_points.tolist()
    )
    distances = nearest_distances(component_points, head_points)
    start_array = component_points[int(np.argmin(distances))]
    start = int(start_array[0]), int(start_array[1])

    queue = deque([start])
    parent = {start: None}
    distance_by_point = {start: 0.0}
    farthest = start
    while queue:
        current = queue.popleft()
        current_distance = float(distance_by_point[current])
        if current_distance > float(distance_by_point[farthest]):
            farthest = current
        for dy, dx in NEIGHBOURS_8:
            neighbour = current[0] + dy, current[1] + dx
            if neighbour not in component or neighbour in parent:
                continue
            parent[neighbour] = current
            distance_by_point[neighbour] = current_distance + (
                1.4142135623730951 if dy and dx else 1.0
            )
            queue.append(neighbour)

    path_yx = []
    current = farthest
    while current is not None:
        path_yx.append(current)
        current = parent[current]
    path_yx.reverse()
    if len(path_yx) < 2:
        if timings is not None:
            timings["skeletonize_seconds"] += skeleton_elapsed
            timings["ordered_centerline_seconds"] += (
                time.perf_counter() - ordered_started
            )
        return []
    points_xy = [
        [float(x + x_offset), float(y + y_offset)] for y, x in path_yx
    ]
    if timings is not None:
        timings["skeletonize_seconds"] += skeleton_elapsed
        timings["ordered_centerline_seconds"] += (
            time.perf_counter() - ordered_started
        )
    return points_xy


def path_length(points_xy: Sequence[Sequence[float]]) -> float:
    points = np.asarray(points_xy, dtype=np.float32)
    if len(points) < 2:
        return 0.0
    return float(np.linalg.norm(np.diff(points, axis=0), axis=1).sum())


def validate_outputs(
    fragments_path: Path,
    probability_path: Path,
    entries_path: Path,
    paths_path: Path,
    global_path: Path,
    expected_shape: Tuple[int, int],
) -> Dict[str, Any]:
    fragments = read_label(fragments_path)
    probability = probability_plane(read_image(probability_path, "输出probability"))
    with entries_path.open("r", encoding="utf-8") as handle:
        entries = list((json.load(handle) or {}).get("results") or [])
    with paths_path.open("r", encoding="utf-8") as handle:
        paths = list((json.load(handle) or {}).get("results") or [])
    with global_path.open("r", encoding="utf-8") as handle:
        global_results = list((json.load(handle) or {}).get("results") or [])
    if fragments.shape != expected_shape or probability.shape != expected_shape:
        raise ValueError("输出图像尺寸不一致。")
    entry_ids = [int(item["head_id"]) for item in entries]
    path_ids = [int(item["head_id"]) for item in paths]
    global_ids = [int(item["head_id"]) for item in global_results]
    if len(entry_ids) != len(set(entry_ids)):
        raise ValueError("entries包含重复head_id。")
    if len(path_ids) != len(set(path_ids)):
        raise ValueError("paths包含重复head_id。")
    if len(global_ids) != len(set(global_ids)):
        raise ValueError("global_results包含重复head_id。")
    if not set(path_ids).issubset(set(entry_ids)):
        raise ValueError("paths包含entries中不存在的head_id。")
    if set(global_ids) != set(path_ids):
        raise ValueError("global_results与paths的head_id不一致。")
    return {
        "valid": True,
        "shape": [int(expected_shape[0]), int(expected_shape[1])],
        "fragment_nonzero_id_count": len(positive_ids(fragments)),
        "entries_count": len(entries),
        "paths_count": len(paths),
        "global_count": len(global_results),
    }


def run_adapter(
    instances_path: Path,
    head_labels_path: Path,
    fitc_path: Path,
    merge_path: Path,
    probability_source_path: Path,
    output_dir: Path,
    dilation_radius: int = DILATION_RADIUS_PX,
    maximum_distance: float = MAX_MATCHING_DISTANCE_PX,
) -> Dict[str, Any]:
    adapter_started = time.perf_counter()
    timings = {
        "skeletonize_seconds": 0.0,
        "ordered_centerline_seconds": 0.0,
    }
    source_paths = {
        "instances": Path(instances_path).expanduser().resolve(),
        "head_labels": Path(head_labels_path).expanduser().resolve(),
        "fitc": Path(fitc_path).expanduser().resolve(),
        "merge": Path(merge_path).expanduser().resolve(),
        "probability": Path(probability_source_path).expanduser().resolve(),
    }
    output_dir = Path(output_dir).expanduser().resolve()

    instances = read_label(source_paths["instances"])
    head_labels = read_label(source_paths["head_labels"])
    fitc = read_image(source_paths["fitc"], "FITC")
    merge = read_image(source_paths["merge"], "Merge")
    probability = probability_plane(
        read_image(source_paths["probability"], "C18B probability")
    )
    expected_shape = tuple(instances.shape)
    shapes = {
        "HeadFinalLabels": tuple(head_labels.shape),
        "FITC": image_shape(fitc),
        "Merge": image_shape(merge),
        "C18B probability": tuple(probability.shape),
    }
    mismatched = {
        name: shape for name, shape in shapes.items() if shape != expected_shape
    }
    if mismatched:
        raise ValueError(
            "输入尺寸不一致；C18B instances={}，其他={}".format(
                expected_shape, mismatched
            )
        )

    instance_ids = positive_ids(instances)
    if not instance_ids:
        raise ValueError("C18B instances没有非零ID。")
    entries = head_records(head_labels)
    if not entries:
        raise ValueError("HeadFinalLabels没有非零Head ID。")
    matched, unmatched = match_instances(
        instances,
        head_labels,
        dilation_radius=max(0, int(dilation_radius)),
        maximum_distance=max(0.1, float(maximum_distance)),
    )

    entry_by_head = {int(item["head_id"]): item for item in entries}
    path_results: List[Dict[str, Any]] = []
    global_results: List[Dict[str, Any]] = []
    skipped_matches: List[Dict[str, Any]] = []
    for match in matched:
        instance_id = int(match["c18b_instance_id"])
        head_id = int(match["head_id"])
        points_xy = ordered_centerline(
            instances == instance_id,
            head_labels == head_id,
            timings=timings,
        )
        if len(points_xy) < 2:
            skipped = dict(match)
            skipped["reason"] = "ordered_centerline_has_fewer_than_two_points"
            skipped_matches.append(skipped)
            continue
        entry_by_head[head_id]["status"] = "auto_confirmed"
        candidate = {
            "rank": 1,
            "points_xy": points_xy,
            "selected_fragment_ids": [instance_id],
            "score": float(match["confidence"]),
            "length_px": path_length(points_xy),
            "source": "c18b_instance_centerline",
            "c18b_instance_id": instance_id,
            "matching_method": str(match["matching_method"]),
            "matching_distance_px": float(match["matching_distance_px"]),
            "confidence": float(match["confidence"]),
        }
        path_results.append({"head_id": head_id, "candidates": [candidate]})
        global_results.append({
            "head_id": head_id,
            "status": "auto_confirmed_unique",
            "selected_rank": 1,
            "selected_candidate": candidate,
            "review_reasons": [],
        })

    output_dir.mkdir(parents=True, exist_ok=True)
    output_paths = {
        "fragments": output_dir / "fragments.tif",
        "probability": output_dir / "probability.tif",
        "entries": output_dir / "entries.json",
        "paths": output_dir / "paths.json",
        "global_results": output_dir / "global_results.json",
        "manifest": output_dir / "manifest.json",
    }
    write_image_atomic(output_paths["fragments"], instances)
    write_image_atomic(output_paths["probability"], probability)
    write_json_atomic(output_paths["entries"], {"version": 1, "results": entries})
    write_json_atomic(
        output_paths["paths"], {"version": 1, "results": path_results}
    )
    write_json_atomic(
        output_paths["global_results"],
        {"version": 1, "results": global_results},
    )

    validation = validate_outputs(
        output_paths["fragments"],
        output_paths["probability"],
        output_paths["entries"],
        output_paths["paths"],
        output_paths["global_results"],
        expected_shape,
    )
    if validation["fragment_nonzero_id_count"] != len(instance_ids):
        raise ValueError("fragments非零ID数量与C18B instances不一致。")
    manifest: Dict[str, Any] = {
        "schema_version": 1,
        "adapter_version": ADAPTER_VERSION,
        "purpose": "C18B结果转换为人工尾部编辑器输入",
        "sources": {
            key: {"path": str(path), "sha256": sha256_file(path)}
            for key, path in source_paths.items()
        },
        "outputs": {
            key: str(path) for key, path in output_paths.items() if key != "manifest"
        },
        "matching": {
            "dilation_radius_px": max(0, int(dilation_radius)),
            "maximum_distance_px": max(0.1, float(maximum_distance)),
            "matched_count": len(matched),
            "editor_initial_result_count": len(global_results),
            "unmatched": unmatched,
            "skipped_matches": skipped_matches,
        },
        "validation": validation,
    }
    write_json_atomic(output_paths["manifest"], manifest)
    timings["adapter_seconds"] = time.perf_counter() - adapter_started
    manifest["timings"] = timings
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="将C18B结果转换为tail editor输入，不启动editor。"
    )
    parser.add_argument("--instances", required=True)
    parser.add_argument("--head-labels", required=True)
    parser.add_argument("--fitc", required=True)
    parser.add_argument("--merge", required=True)
    parser.add_argument("--probability", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--dilation-radius", type=int, default=DILATION_RADIUS_PX)
    parser.add_argument(
        "--maximum-distance",
        type=float,
        default=MAX_MATCHING_DISTANCE_PX,
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    manifest = run_adapter(
        Path(args.instances),
        Path(args.head_labels),
        Path(args.fitc),
        Path(args.merge),
        Path(args.probability),
        Path(args.output_dir),
        dilation_radius=args.dilation_radius,
        maximum_distance=args.maximum_distance,
    )
    validation = dict(manifest["validation"])
    print("fragments非零ID数量：{}".format(
        validation["fragment_nonzero_id_count"]
    ))
    print("entries数量：{}".format(validation["entries_count"]))
    print("paths数量：{}".format(validation["paths_count"]))
    print("global数量：{}".format(validation["global_count"]))
    print("所有尺寸一致：是")
    timings = dict(manifest["timings"])
    print("skeletonize总耗时：{:.6f}秒".format(
        timings["skeletonize_seconds"]
    ))
    print("ordered_centerline总耗时：{:.6f}秒".format(
        timings["ordered_centerline_seconds"]
    ))
    print("adapter总耗时：{:.6f}秒".format(timings["adapter_seconds"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
