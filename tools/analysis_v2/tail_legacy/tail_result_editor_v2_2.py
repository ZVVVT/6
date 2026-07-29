#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
精子尾部整图所见即所得校准工具 V2.2

界面原则：
- 整张Merge图一次显示，不逐个头部翻页；
- 白色粗轮廓表示已经纳入结果的尾部，黄色表示当前选择或手动预览；
- 没有结果的头部不会被标成待处理，因为并非每个头部都有合格尾部；
- 自动结果正确时无需操作；错误时点击后删除或重画；
- 漏识别但确有合格尾部时，点击“新增 / 重画”后选择头部并绘制路径；
- 每次确认或删除后自动保存编辑状态，“保存结果”生成最终输出文件。

鼠标操作：
- 点击已有尾部：立即选中，随后可直接删除或重画；
- 点击当前没有结果的头部：自动进入新增状态；
- 新增/重画时：依次点击尾部路径点，从第二个点开始自动实时计算路径；
- 路径不正确时继续增加较近的引导点，或点击“撤销”返回上一步；
- 路径正确后点击“确认新增/确认重画”。

快捷键仅作为可选辅助：
Enter/A 确认当前路径，Delete/D 删除已选结果，U/Ctrl+Z 撤销，Esc取消，F显示碎片，S保存。
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import cv2
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib import font_manager
    from matplotlib.widgets import Button
    from PIL import Image
    from scipy.ndimage import distance_transform_edt, gaussian_filter1d
    from scipy.spatial import cKDTree
    from skimage.graph import route_through_array
except ImportError as exc:
    print("缺少依赖：", exc)
    raise SystemExit(1) from exc


VERSION = "tail_result_editor_v2_2"


def configure_chinese_font() -> str | None:
    # 优先使用Windows系统自带中文字体，避免界面出现方框。
    preferred_names = [
        "Microsoft YaHei",
        "Microsoft YaHei UI",
        "SimHei",
        "SimSun",
        "NSimSun",
        "Microsoft JhengHei",
        "Noto Sans CJK SC",
        "Source Han Sans SC",
        "Arial Unicode MS",
    ]

    available_names = {
        item.name
        for item in font_manager.fontManager.ttflist
    }

    selected = next(
        (
            name
            for name in preferred_names
            if name in available_names
        ),
        None,
    )

    if selected is None:
        # Windows上有时Matplotlib字体缓存尚未发现系统字体，
        # 直接尝试常见系统字体文件。
        windows_fonts = Path(
            __import__("os").environ.get(
                "WINDIR",
                r"C:\Windows",
            )
        ) / "Fonts"

        preferred_files = [
            "msyh.ttc",
            "msyhbd.ttc",
            "simhei.ttf",
            "simsun.ttc",
            "msjh.ttc",
        ]

        for filename in preferred_files:
            font_path = windows_fonts / filename
            if not font_path.exists():
                continue
            try:
                font_manager.fontManager.addfont(
                    str(font_path)
                )
                selected = font_manager.FontProperties(
                    fname=str(font_path)
                ).get_name()
                break
            except Exception:
                continue

    if selected:
        plt.rcParams["font.family"] = "sans-serif"
        plt.rcParams["font.sans-serif"] = [
            selected,
            "DejaVu Sans",
        ]
        plt.rcParams["axes.unicode_minus"] = False
        print(f"界面中文字体：{selected}")
    else:
        print(
            "警告：未检测到可用中文字体，"
            "界面中文仍可能显示为方框。"
        )

    return selected


def read_image(path: Path) -> np.ndarray:
    with Image.open(path) as image:
        return np.asarray(image)


def resolve_required(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"找不到文件：{path}")
    return path


def robust_normalize(
    image: np.ndarray,
    low_p: float = 0.2,
    high_p: float = 99.8,
) -> np.ndarray:
    array = image.astype(np.float32, copy=False)
    low, high = np.percentile(array, [low_p, high_p])
    if high <= low:
        return np.zeros_like(array, dtype=np.float32)
    return np.clip((array - low) / (high - low), 0.0, 1.0)


def normalize_probability(image: np.ndarray) -> np.ndarray:
    array = image.astype(np.float32, copy=False)
    if array.ndim == 3:
        array = array[..., 0]
    minimum = float(np.nanmin(array))
    maximum = float(np.nanmax(array))
    if minimum < 0.0:
        return robust_normalize(array, low_p=0.1, high_p=99.9)
    if maximum > 1.5:
        array = array / 65535.0
    return np.clip(array, 0.0, 1.0)


def normalize_fragment_labels(image: np.ndarray) -> np.ndarray:
    array = np.asarray(image)
    if array.ndim == 3:
        array = array[..., 0]
    if array.ndim != 2:
        raise ValueError(f"尾部碎片标签图必须是二维图像：{array.shape}")

    labels = np.rint(array).astype(np.int64, copy=False)
    labels[labels < 0] = 0

    unique = np.unique(labels)
    nonzero = unique[unique > 0]
    # 如果输入实际为二值图，自动转成连通域编号。
    if len(nonzero) <= 1:
        _, connected = cv2.connectedComponents(
            (labels > 0).astype(np.uint8),
            connectivity=8,
        )
        labels = connected.astype(np.int64)

    if int(labels.max()) > np.iinfo(np.uint32).max:
        raise ValueError("尾部碎片标签编号超出uint32范围。")
    return labels.astype(np.uint32)


def fragment_ids_near_path(
    fragment_labels: np.ndarray,
    points_xy: np.ndarray,
    radius_px: int,
) -> list[int]:
    """只在路径包围盒内查找碎片，避免每次点击扫描整张大图。"""
    if len(points_xy) < 2:
        return []

    points = np.asarray(points_xy, dtype=np.float32)
    radius = max(1, int(radius_px))
    height, width = fragment_labels.shape

    minimum_x = max(0, int(np.floor(points[:, 0].min())) - radius - 2)
    maximum_x = min(width - 1, int(np.ceil(points[:, 0].max())) + radius + 2)
    minimum_y = max(0, int(np.floor(points[:, 1].min())) - radius - 2)
    maximum_y = min(height - 1, int(np.ceil(points[:, 1].max())) + radius + 2)

    if maximum_x < minimum_x or maximum_y < minimum_y:
        return []

    local_labels = fragment_labels[
        minimum_y : maximum_y + 1,
        minimum_x : maximum_x + 1,
    ]
    local_mask = np.zeros(local_labels.shape, dtype=np.uint8)
    shifted = points.copy()
    shifted[:, 0] -= minimum_x
    shifted[:, 1] -= minimum_y
    shifted_int = np.rint(shifted).astype(np.int32)

    cv2.polylines(
        local_mask,
        [shifted_int.reshape(-1, 1, 2)],
        False,
        1,
        radius * 2 + 1,
        lineType=cv2.LINE_8,
    )
    values = local_labels[local_mask > 0]
    values = np.unique(values[values > 0])
    return [int(value) for value in values.tolist()]


def build_path_aware_region_labels(
    fragment_labels: np.ndarray,
    accepted_records: list[Any],
) -> tuple[np.ndarray, list[dict[str, int]]]:
    """按中心线划分共享碎片，同时保持唯一碎片的原有整块归属。"""
    labels = np.asarray(fragment_labels)
    if labels.ndim != 2:
        raise ValueError(f"尾部碎片标签图必须是二维图像：{labels.shape}")

    height, width = labels.shape
    region_labels = np.zeros(labels.shape, dtype=np.uint16)
    fragment_users: dict[int, list[Any]] = {}
    for record in accepted_records:
        for fragment_id in set(record.selected_fragment_ids):
            value = int(fragment_id)
            if value > 0:
                fragment_users.setdefault(value, []).append(record)

    for fragment_id, records in fragment_users.items():
        fragment_mask = labels == fragment_id
        if not np.any(fragment_mask):
            continue

        if len(records) == 1:
            region_labels[fragment_mask] = np.uint16(records[0].head_id)
            if int(np.count_nonzero(region_labels[fragment_mask])) != int(
                np.count_nonzero(fragment_mask)
            ):
                raise ValueError(
                    f"唯一尾部碎片 {fragment_id} 未保持完整区域。"
                )
            continue

        fragment_y, fragment_x = np.nonzero(fragment_mask)
        all_paths = [
            np.asarray(record.selected_points_xy, dtype=np.float32)
            for record in records
        ]
        valid_paths = [path for path in all_paths if len(path) >= 2]
        if len(valid_paths) != len(records):
            raise ValueError(
                f"共享尾部碎片 {fragment_id} 存在缺少中心线的已接受结果。"
            )

        minimum_x = max(
            0,
            int(
                math.floor(
                    min(
                        float(fragment_x.min()),
                        *(float(path[:, 0].min()) for path in valid_paths),
                    )
                )
            )
            - 1,
        )
        maximum_x = min(
            width - 1,
            int(
                math.ceil(
                    max(
                        float(fragment_x.max()),
                        *(float(path[:, 0].max()) for path in valid_paths),
                    )
                )
            )
            + 1,
        )
        minimum_y = max(
            0,
            int(
                math.floor(
                    min(
                        float(fragment_y.min()),
                        *(float(path[:, 1].min()) for path in valid_paths),
                    )
                )
            )
            - 1,
        )
        maximum_y = min(
            height - 1,
            int(
                math.ceil(
                    max(
                        float(fragment_y.max()),
                        *(float(path[:, 1].max()) for path in valid_paths),
                    )
                )
            )
            + 1,
        )

        local_shape = (
            maximum_y - minimum_y + 1,
            maximum_x - minimum_x + 1,
        )
        distance_images: list[np.ndarray] = []
        for path in valid_paths:
            shifted = path.copy()
            shifted[:, 0] -= minimum_x
            shifted[:, 1] -= minimum_y
            seed_image = np.ones(local_shape, dtype=np.uint8)
            cv2.polylines(
                seed_image,
                [np.rint(shifted).astype(np.int32).reshape(-1, 1, 2)],
                False,
                0,
                1,
                lineType=cv2.LINE_8,
            )
            if not np.any(seed_image == 0):
                raise ValueError(
                    f"共享尾部碎片 {fragment_id} 的中心线无法栅格化。"
                )
            distance_images.append(distance_transform_edt(seed_image))

        distances = np.stack(distance_images, axis=0)
        nearest_distance = np.min(distances, axis=0)
        nearest_count = np.count_nonzero(
            distances == nearest_distance[np.newaxis, ...],
            axis=0,
        )
        nearest_index = np.argmin(distances, axis=0)
        local_fragment = fragment_mask[
            minimum_y : maximum_y + 1,
            minimum_x : maximum_x + 1,
        ]
        local_output = region_labels[
            minimum_y : maximum_y + 1,
            minimum_x : maximum_x + 1,
        ]
        ambiguous = local_fragment & (nearest_count > 1)
        assignment_count = np.zeros(local_shape, dtype=np.uint16)
        for index, record in enumerate(records):
            assigned = (
                local_fragment
                & (nearest_count == 1)
                & (nearest_index == index)
            )
            assignment_count[assigned] += 1
            local_output[assigned] = np.uint16(record.head_id)

        if np.any(assignment_count > 1):
            raise ValueError(
                f"共享尾部碎片 {fragment_id} 仍存在区域重叠。"
            )
        if np.any(local_fragment & (local_output == 0) & ~ambiguous):
            raise ValueError(
                f"共享尾部碎片 {fragment_id} 存在非等距未分配像素。"
            )

    missing_heads = [
        int(record.head_id)
        for record in accepted_records
        if not np.any(region_labels == int(record.head_id))
    ]
    if missing_heads:
        raise ValueError(
            f"已接受尾部没有任何区域像素：Head {missing_heads}"
        )

    conflict_rows: list[dict[str, int]] = []
    return region_labels, conflict_rows


def fragment_boundary_mask(fragment_labels: np.ndarray) -> np.ndarray:
    binary = (fragment_labels > 0).astype(np.uint8)
    kernel = np.ones((3, 3), dtype=np.uint8)
    eroded = cv2.erode(binary, kernel, iterations=1)
    return (binary > eroded)


def build_manual_evidence(
    merge_rgb: np.ndarray,
    probability: np.ndarray,
    fragment_labels: np.ndarray,
    green_image: np.ndarray | None = None,
) -> np.ndarray:
    if green_image is None:
        green = merge_rgb[..., 1]
    else:
        green = green_image
        if green.ndim == 3:
            green = green[..., 1 if green.shape[2] > 1 else 0]

    green_norm = robust_normalize(green, low_p=0.2, high_p=99.8)
    fragment_binary = (fragment_labels > 0).astype(np.float32)
    fragment_support = cv2.GaussianBlur(
        fragment_binary,
        (0, 0),
        sigmaX=1.2,
        sigmaY=1.2,
    )
    fragment_support = np.clip(fragment_support, 0.0, 1.0)

    evidence = np.maximum.reduce(
        [
            probability.astype(np.float32),
            0.85 * green_norm.astype(np.float32),
            0.92 * fragment_support.astype(np.float32),
        ]
    )
    return np.clip(evidence, 0.0, 1.0)


def to_uint8_rgb(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        gray = np.round(robust_normalize(image) * 255.0).astype(np.uint8)
        return np.repeat(gray[..., None], 3, axis=2)

    if image.ndim == 3 and image.shape[2] >= 3:
        rgb = np.zeros(image.shape[:2] + (3,), dtype=np.uint8)
        for channel in range(3):
            rgb[..., channel] = np.round(
                robust_normalize(image[..., channel]) * 255.0
            ).astype(np.uint8)
        return rgb

    raise ValueError(f"无法转换图像维度：{image.shape}")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def ensure_points(points: Any) -> np.ndarray:
    array = np.asarray(points or [], dtype=np.float32)
    if array.ndim != 2 or array.shape[1] != 2:
        return np.zeros((0, 2), dtype=np.float32)
    return array


def path_length(points_xy: np.ndarray) -> float:
    if len(points_xy) < 2:
        return 0.0
    return float(
        np.linalg.norm(
            np.diff(points_xy.astype(np.float32), axis=0),
            axis=1,
        ).sum()
    )


def sample_probability(
    probability: np.ndarray,
    points_xy: np.ndarray,
    low_threshold: float = 0.08,
) -> dict[str, float]:
    if len(points_xy) == 0:
        return {
            "mean_probability": 0.0,
            "min_probability": 0.0,
            "low_probability_fraction": 1.0,
        }

    x = np.clip(
        np.rint(points_xy[:, 0]).astype(np.int32),
        0,
        probability.shape[1] - 1,
    )
    y = np.clip(
        np.rint(points_xy[:, 1]).astype(np.int32),
        0,
        probability.shape[0] - 1,
    )
    values = probability[y, x]
    return {
        "mean_probability": float(values.mean()),
        "min_probability": float(values.min()),
        "low_probability_fraction": float(
            np.mean(values < float(low_threshold))
        ),
    }


def smooth_polyline(
    points_xy: np.ndarray,
    sigma: float = 1.2,
) -> np.ndarray:
    if len(points_xy) < 7:
        return points_xy.astype(np.float32)

    smoothed = np.column_stack(
        [
            gaussian_filter1d(points_xy[:, 0], sigma=sigma),
            gaussian_filter1d(points_xy[:, 1], sigma=sigma),
        ]
    ).astype(np.float32)
    smoothed[0] = points_xy[0]
    smoothed[-1] = points_xy[-1]
    return smoothed


def deduplicate_points(points_xy: np.ndarray) -> np.ndarray:
    if len(points_xy) < 2:
        return points_xy
    keep = np.ones(len(points_xy), dtype=bool)
    keep[1:] = np.any(
        np.rint(points_xy[1:]).astype(np.int32)
        != np.rint(points_xy[:-1]).astype(np.int32),
        axis=1,
    )
    return points_xy[keep]


def local_route(
    probability: np.ndarray,
    start_xy: np.ndarray,
    end_xy: np.ndarray,
    margin_px: int,
) -> np.ndarray:
    height, width = probability.shape
    start_xy = np.asarray(start_xy, dtype=np.float32)
    end_xy = np.asarray(end_xy, dtype=np.float32)

    minimum_x = max(
        0,
        int(math.floor(min(start_xy[0], end_xy[0]))) - margin_px,
    )
    maximum_x = min(
        width - 1,
        int(math.ceil(max(start_xy[0], end_xy[0]))) + margin_px,
    )
    minimum_y = max(
        0,
        int(math.floor(min(start_xy[1], end_xy[1]))) - margin_px,
    )
    maximum_y = min(
        height - 1,
        int(math.ceil(max(start_xy[1], end_xy[1]))) + margin_px,
    )

    local_probability = probability[
        minimum_y : maximum_y + 1,
        minimum_x : maximum_x + 1,
    ]

    blurred = cv2.GaussianBlur(
        local_probability.astype(np.float32),
        (0, 0),
        sigmaX=0.8,
        sigmaY=0.8,
    )

    # 高概率位置代价低；低概率背景代价高。
    cost = (
        0.04
        + 5.0 * np.square(1.0 - blurred)
        + 6.0 * (blurred < 0.025).astype(np.float32)
    ).astype(np.float32)

    start_yx = (
        int(round(start_xy[1])) - minimum_y,
        int(round(start_xy[0])) - minimum_x,
    )
    end_yx = (
        int(round(end_xy[1])) - minimum_y,
        int(round(end_xy[0])) - minimum_x,
    )

    start_yx = (
        int(np.clip(start_yx[0], 0, cost.shape[0] - 1)),
        int(np.clip(start_yx[1], 0, cost.shape[1] - 1)),
    )
    end_yx = (
        int(np.clip(end_yx[0], 0, cost.shape[0] - 1)),
        int(np.clip(end_yx[1], 0, cost.shape[1] - 1)),
    )

    route_yx, _ = route_through_array(
        cost,
        start_yx,
        end_yx,
        fully_connected=True,
        geometric=True,
    )

    route_yx = np.asarray(route_yx, dtype=np.float32)
    route_xy = np.column_stack(
        [
            route_yx[:, 1] + minimum_x,
            route_yx[:, 0] + minimum_y,
        ]
    )
    return route_xy.astype(np.float32)


def piecewise_manual_route(
    probability: np.ndarray,
    guide_points_xy: list[tuple[float, float]],
    margin_px: int,
) -> np.ndarray:
    if len(guide_points_xy) < 2:
        raise ValueError("手动路径至少需要起点和末端两个点。")

    pieces: list[np.ndarray] = []
    for index in range(len(guide_points_xy) - 1):
        start = np.asarray(guide_points_xy[index], dtype=np.float32)
        end = np.asarray(guide_points_xy[index + 1], dtype=np.float32)
        piece = local_route(
            probability,
            start,
            end,
            margin_px=margin_px,
        )
        if index > 0 and len(piece) > 0:
            piece = piece[1:]
        pieces.append(piece)

    combined = np.vstack(pieces)
    combined = deduplicate_points(combined)
    return smooth_polyline(combined, sigma=1.0)


def status_color(status: str) -> str:
    colors = {
        "trusted_auto": "#00ff4c",
        "auto_entry_review": "#00e5ff",
        "review_required": "#ffe600",
        "manual_required": "#ff9d00",
        "user_accepted": "#ffffff",
        "user_review": "#ff9d00",
        "manual_ready": "#ff66ff",
        "deleted": "#ff2020",
        "unassigned": "#ff2020",
    }
    return colors.get(status, "#ffe600")


def status_label(status: str) -> str:
    labels = {
        "trusted_auto": "高可信自动",
        "auto_entry_review": "路径可信，入口需复核",
        "review_required": "待复核",
        "manual_required": "需手动",
        "user_accepted": "人工接受",
        "user_review": "人工标记待复核",
        "manual_ready": "手动新增待接受",
        "deleted": "已删除",
        "unassigned": "未分配",
    }
    return labels.get(status, status)


@dataclass
class EditorRecord:
    head_id: int
    center_x: float
    center_y: float
    entry_status: str
    initial_status: str
    current_status: str
    candidates: list[dict[str, Any]]
    selected_rank: int | None
    selected_points_xy: np.ndarray
    selected_fragment_ids: list[int]
    suggested_points_xy: np.ndarray
    selected_source: str
    accepted_by_user: bool
    deleted: bool
    review_reasons: list[str]
    original_global_status: str
    edit_note: str = ""

    def selected_candidate(self) -> dict[str, Any] | None:
        if self.selected_rank is None:
            return None
        for candidate in self.candidates:
            if int(candidate.get("rank", -1)) == int(self.selected_rank):
                return candidate
        return None


class TailResultEditor:
    """整图所见即所得尾部结果编辑器。"""

    ACCEPTED_STATUSES = {"trusted_auto", "user_accepted"}

    def __init__(
        self,
        *,
        merge_rgb: np.ndarray,
        probability: np.ndarray,
        fragment_labels: np.ndarray,
        green_image: np.ndarray | None,
        entries_payload: dict[str, Any],
        path_payload: dict[str, Any],
        global_payload: dict[str, Any],
        output_dir: Path,
        manual_margin_px: int,
        manual_fragment_radius_px: int,
        display_max_dim: int = 1400,
    ) -> None:
        self.merge_rgb = merge_rgb
        self.probability = probability
        self.fragment_labels = fragment_labels
        self.fragment_boundary = fragment_boundary_mask(fragment_labels)
        self.manual_evidence = build_manual_evidence(
            merge_rgb,
            probability,
            fragment_labels,
            green_image,
        )
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.manual_margin_px = int(manual_margin_px)
        self.manual_fragment_radius_px = int(manual_fragment_radius_px)

        self.records = self._build_records(
            entries_payload,
            path_payload,
            global_payload,
        )
        self.record_by_head = {
            record.head_id: record
            for record in self.records
        }
        self.head_centers = np.asarray(
            [
                [record.center_x, record.center_y]
                for record in self.records
            ],
            dtype=np.float32,
        )
        self.head_tree = cKDTree(self.head_centers)

        # 整图编辑状态：不再按头部逐个翻页。
        self.selected_index: int | None = None
        self.mode = "idle"  # idle / await_head / drawing
        self.manual_target_index: int | None = None
        self.manual_points: list[tuple[float, float]] = []
        self.manual_segments: list[np.ndarray] = []
        self.manual_preview: np.ndarray | None = None
        self.manual_preview_fragment_ids: list[int] = []
        self.manual_conflict_fragment_ids: list[int] = []
        self.message = ""
        self.show_fragments = False
        self.edit_history: list[tuple[int, EditorRecord]] = []

        self.result_owner_image = np.zeros(
            self.fragment_labels.shape,
            dtype=np.uint16,
        )
        self.result_owner_lut = np.zeros(
            int(self.fragment_labels.max()) + 1,
            dtype=np.uint16,
        )
        self.path_tree: cKDTree | None = None
        self.path_tree_head_ids = np.zeros((0,), dtype=np.int32)
        self.result_cache_dirty = True

        # 为提高整图交互速度，显示层限制最大边长；所有鼠标坐标仍保持原图坐标。
        self.image_height, self.image_width = self.merge_rgb.shape[:2]
        self.display_max_dim = max(900, int(display_max_dim))
        self.display_scale = min(
            1.0,
            self.display_max_dim / float(max(self.image_height, self.image_width)),
        )
        self.display_width = max(1, int(round(self.image_width * self.display_scale)))
        self.display_height = max(1, int(round(self.image_height * self.display_scale)))
        self.display_extent = (
            0.0,
            float(self.image_width),
            float(self.image_height),
            0.0,
        )
        self.display_merge_rgb = cv2.resize(
            self.merge_rgb,
            (self.display_width, self.display_height),
            interpolation=cv2.INTER_AREA,
        )
        self.display_fragment_labels = np.rint(
            cv2.resize(
                self.fragment_labels.astype(np.float32),
                (self.display_width, self.display_height),
                interpolation=cv2.INTER_NEAREST,
            )
        ).astype(np.uint32)
        display_fragment_boundary = fragment_boundary_mask(self.display_fragment_labels)
        self.display_fragment_rgba = np.zeros(
            (self.display_height, self.display_width, 4),
            dtype=np.float32,
        )
        self.display_fragment_rgba[display_fragment_boundary] = (
            0.0, 0.9, 1.0, 0.40
        )
        self.display_result_owner_image = np.zeros(
            (self.display_height, self.display_width),
            dtype=np.uint16,
        )
        self.display_accepted_rgba = np.zeros(
            (self.display_height, self.display_width, 4),
            dtype=np.float32,
        )

        self.state_path = self.output_dir / "editor_state_v2_2.json"
        self._load_existing_state()

        self.figure: Any = None
        self.axis: Any = None
        self.status_text: Any = None
        self.buttons: dict[str, Button] = {}

        self._setup_figure()
        self.redraw()

    def _build_records(
        self,
        entries_payload: dict[str, Any],
        path_payload: dict[str, Any],
        global_payload: dict[str, Any],
    ) -> list[EditorRecord]:
        path_by_head = {
            int(item["head_id"]): item
            for item in path_payload.get("results", [])
        }
        global_by_head = {
            int(item["head_id"]): item
            for item in global_payload.get("results", [])
        }

        records: list[EditorRecord] = []
        for entry in entries_payload.get("results", []):
            head_id = int(entry["head_id"])
            path_result = path_by_head.get(head_id, {})
            global_result = global_by_head.get(head_id)

            candidates = deepcopy(path_result.get("candidates", []))
            candidates.sort(key=lambda item: int(item.get("rank", 999)))

            selected_rank: int | None = None
            selected_points = np.zeros((0, 2), dtype=np.float32)
            suggested_points = np.zeros((0, 2), dtype=np.float32)
            global_status = ""
            review_reasons: list[str] = []

            if global_result is not None:
                global_status = str(global_result.get("status", ""))
                selected_rank_value = global_result.get("selected_rank")
                if selected_rank_value is not None:
                    selected_rank = int(selected_rank_value)

                selected_candidate = global_result.get("selected_candidate")
                if selected_candidate:
                    selected_points = ensure_points(
                        selected_candidate.get("points_xy", [])
                    )
                    suggested_points = selected_points.copy()

                review_reasons = list(
                    global_result.get("review_reasons", [])
                )

            entry_status = str(entry.get("status", ""))
            trusted = (
                global_status == "auto_confirmed_unique"
                and entry_status == "auto_confirmed"
                and len(selected_points) >= 2
            )
            initial_status = "trusted_auto" if trusted else "manual_required"

            if selected_rank is not None and len(selected_points) == 0:
                for candidate in candidates:
                    if int(candidate.get("rank", -1)) == selected_rank:
                        selected_points = ensure_points(
                            candidate.get("points_xy", [])
                        )
                        suggested_points = selected_points.copy()
                        break

            if len(suggested_points) == 0 and candidates:
                suggested_points = ensure_points(
                    candidates[0].get("points_xy", [])
                )

            # 不确定结果不作为已有结果显示，也不会被当成“待处理任务”。
            if initial_status == "manual_required":
                selected_rank = None
                selected_points = np.zeros((0, 2), dtype=np.float32)

            selected_fragment_ids = fragment_ids_near_path(
                self.fragment_labels,
                selected_points,
                radius_px=self.manual_fragment_radius_px,
            )

            records.append(
                EditorRecord(
                    head_id=head_id,
                    center_x=float(entry["center_x"]),
                    center_y=float(entry["center_y"]),
                    entry_status=entry_status,
                    initial_status=initial_status,
                    current_status=initial_status,
                    candidates=candidates,
                    selected_rank=selected_rank,
                    selected_points_xy=selected_points,
                    selected_fragment_ids=selected_fragment_ids,
                    suggested_points_xy=suggested_points,
                    selected_source=(
                        "global_candidate" if trusted else "none"
                    ),
                    accepted_by_user=False,
                    deleted=False,
                    review_reasons=review_reasons,
                    original_global_status=global_status,
                )
            )

        records.sort(key=lambda item: item.head_id)
        if not records:
            raise ValueError("没有读取到任何头部记录。")
        return records

    def _load_existing_state(self) -> None:
        candidates = [
            self.state_path,
            self.output_dir / "editor_state_v2_1.json",
        ]
        source_path = next((path for path in candidates if path.exists()), None)
        if source_path is None:
            return
        try:
            payload = load_json(source_path)
        except Exception as exc:
            print("已有编辑状态读取失败，忽略：", exc)
            return

        saved_by_head = {
            int(item["head_id"]): item
            for item in payload.get("records", [])
        }
        loaded_count = 0
        for record in self.records:
            saved = saved_by_head.get(record.head_id)
            if saved is None:
                continue
            record.current_status = str(
                saved.get("current_status", record.current_status)
            )
            record.selected_rank = (
                int(saved["selected_rank"])
                if saved.get("selected_rank") is not None
                else None
            )
            record.selected_points_xy = ensure_points(
                saved.get("selected_points_xy", [])
            )
            record.selected_fragment_ids = [
                int(value)
                for value in saved.get(
                    "selected_fragment_ids",
                    record.selected_fragment_ids,
                )
                if int(value) > 0
            ]
            record.selected_source = str(
                saved.get("selected_source", record.selected_source)
            )
            record.accepted_by_user = bool(
                saved.get("accepted_by_user", False)
            )
            record.deleted = bool(saved.get("deleted", False))
            record.edit_note = str(saved.get("edit_note", ""))
            loaded_count += 1

        current_head_id = payload.get("current_head_id")
        if current_head_id is not None:
            for index, record in enumerate(self.records):
                if record.head_id == int(current_head_id):
                    self.selected_index = index
                    break

        if loaded_count:
            print(f"已恢复编辑状态：{loaded_count}个头部")
            if source_path != self.state_path:
                print(f"已从旧版状态迁移：{source_path}")

    def _setup_figure(self) -> None:
        plt.rcParams["toolbar"] = "None"
        self.figure = plt.figure(figsize=(16, 10))
        try:
            self.figure.canvas.manager.set_window_title(
                "精子尾部结果校准 V2.2"
            )
        except Exception:
            pass

        self.axis = self.figure.add_axes([0.02, 0.16, 0.96, 0.80])
        self.axis.set_axis_off()

        button_specs = [
            ("primary", "新增 / 重画", self.primary_action),
            ("delete", "删除所选", self.delete_selected),
            ("undo", "撤销", self.undo_action),
            ("cancel", "取消操作", self.cancel_operation),
            ("fragments", "显示碎片", self.toggle_fragments),
            ("save", "保存结果", self.save_all),
        ]

        left = 0.03
        width = 0.145
        gap = 0.013
        for key, label_text, callback in button_specs:
            button_axis = self.figure.add_axes(
                [left, 0.055, width, 0.05]
            )
            button = Button(button_axis, label_text)
            button.on_clicked(callback)
            self.buttons[key] = button
            left += width + gap

        self.status_text = self.figure.text(
            0.03,
            0.125,
            "",
            fontsize=10.5,
            va="center",
        )

        self.figure.canvas.mpl_connect(
            "button_press_event",
            self.on_mouse_click,
        )
        self.figure.canvas.mpl_connect(
            "key_press_event",
            self.on_key_press,
        )
        self.figure.canvas.mpl_connect(
            "close_event",
            self.on_close,
        )

    @property
    def selected_record(self) -> EditorRecord | None:
        if self.selected_index is None:
            return None
        return self.records[self.selected_index]

    def _has_result(self, record: EditorRecord) -> bool:
        return (
            not record.deleted
            and record.current_status in self.ACCEPTED_STATUSES
            and len(record.selected_points_xy) >= 2
        )

    def _selected_path(self, record: EditorRecord) -> np.ndarray:
        if not self._has_result(record):
            return np.zeros((0, 2), dtype=np.float32)
        return record.selected_points_xy

    def _status_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for record in self.records:
            counts[record.current_status] = (
                counts.get(record.current_status, 0) + 1
            )
        return counts

    def _accepted_records(self) -> list[EditorRecord]:
        return [record for record in self.records if self._has_result(record)]

    def _build_result_owner(self) -> None:
        max_label = int(self.fragment_labels.max())
        lut = np.zeros(max_label + 1, dtype=np.uint16)
        for record in self._accepted_records():
            for fragment_id in record.selected_fragment_ids:
                if fragment_id <= 0 or fragment_id > max_label:
                    continue
                if lut[fragment_id] == 0:
                    lut[fragment_id] = np.uint16(record.head_id)
        self.result_owner_lut = lut
        self.result_owner_image = lut[self.fragment_labels]
        self.display_result_owner_image = cv2.resize(
            self.result_owner_image,
            (self.display_width, self.display_height),
            interpolation=cv2.INTER_NEAREST,
        ).astype(np.uint16)

        # 已识别结果使用白色粗轮廓，避免与绿色荧光混淆。
        boundary = self._mask_boundary(self.display_result_owner_image > 0)
        halo = cv2.dilate(
            boundary.astype(np.uint8),
            np.ones((3, 3), dtype=np.uint8),
            iterations=1,
        ) > 0
        rgba = np.zeros(
            (self.display_height, self.display_width, 4),
            dtype=np.float32,
        )
        rgba[halo] = (0.0, 0.0, 0.0, 0.85)
        rgba[boundary] = (1.0, 1.0, 1.0, 1.0)
        self.display_accepted_rgba = rgba

        path_points: list[np.ndarray] = []
        path_head_ids: list[np.ndarray] = []
        for record in self._accepted_records():
            path = record.selected_points_xy
            if len(path) < 2:
                continue
            step = max(1, len(path) // 500)
            sampled = path[::step]
            path_points.append(sampled)
            path_head_ids.append(
                np.full(len(sampled), record.head_id, dtype=np.int32)
            )
        if path_points:
            combined = np.vstack(path_points).astype(np.float32)
            self.path_tree = cKDTree(combined)
            self.path_tree_head_ids = np.concatenate(path_head_ids)
        else:
            self.path_tree = None
            self.path_tree_head_ids = np.zeros((0,), dtype=np.int32)
        self.result_cache_dirty = False

    def _mark_result_cache_dirty(self) -> None:
        self.result_cache_dirty = True

    def _ensure_result_cache(self) -> None:
        if self.result_cache_dirty:
            self._build_result_owner()

    @staticmethod
    def _mask_boundary(mask: np.ndarray) -> np.ndarray:
        binary = mask.astype(np.uint8)
        if not np.any(binary):
            return np.zeros_like(mask, dtype=bool)
        eroded = cv2.erode(
            binary,
            np.ones((3, 3), dtype=np.uint8),
            iterations=1,
        )
        return binary > eroded

    def _draw_rgba_boundary(
        self,
        mask: np.ndarray,
        color: tuple[float, float, float],
        alpha: float,
        zorder: int,
    ) -> None:
        boundary = self._mask_boundary(mask)
        if not np.any(boundary):
            return
        halo = cv2.dilate(
            boundary.astype(np.uint8),
            np.ones((3, 3), dtype=np.uint8),
            iterations=1,
        ) > 0
        rgba = np.zeros(mask.shape + (4,), dtype=np.float32)
        rgba[halo] = (0.0, 0.0, 0.0, min(0.8, alpha))
        rgba[boundary] = (*color, alpha)
        self.axis.imshow(rgba, zorder=zorder)

    def _draw_display_boundary(
        self,
        mask: np.ndarray,
        color: tuple[float, float, float],
        alpha: float,
        zorder: int,
    ) -> None:
        boundary = self._mask_boundary(mask)
        if not np.any(boundary):
            return
        halo = cv2.dilate(
            boundary.astype(np.uint8),
            np.ones((3, 3), dtype=np.uint8),
            iterations=1,
        ) > 0
        rgba = np.zeros(mask.shape + (4,), dtype=np.float32)
        rgba[halo] = (0.0, 0.0, 0.0, min(0.85, alpha))
        rgba[boundary] = (*color, alpha)
        self.axis.imshow(
            rgba,
            extent=self.display_extent,
            interpolation="nearest",
            zorder=zorder,
        )

    def _plot_path(
        self,
        path: np.ndarray,
        color: str,
        linewidth: float,
        zorder: int,
        linestyle: str = "-",
        alpha: float = 1.0,
    ) -> None:
        if len(path) < 2:
            return
        self.axis.plot(
            path[:, 0],
            path[:, 1],
            color="#000000",
            linewidth=linewidth + 2.6,
            linestyle=linestyle,
            alpha=min(0.85, alpha),
            zorder=zorder,
        )
        self.axis.plot(
            path[:, 0],
            path[:, 1],
            color=color,
            linewidth=linewidth,
            linestyle=linestyle,
            alpha=alpha,
            zorder=zorder + 0.1,
        )

    def redraw(self) -> None:
        self._ensure_result_cache()
        self.axis.clear()
        self.axis.imshow(
            self.display_merge_rgb,
            extent=self.display_extent,
            interpolation="nearest",
            zorder=1,
        )
        self.axis.set_xlim(0, self.image_width)
        self.axis.set_ylim(self.image_height, 0)
        self.axis.set_axis_off()

        if self.show_fragments:
            self.axis.imshow(
                self.display_fragment_rgba,
                extent=self.display_extent,
                interpolation="nearest",
                zorder=3,
            )

        # 静态已有结果只绘制一层缓存白色轮廓，不再逐条重画中心线。
        self.axis.imshow(
            self.display_accepted_rgba,
            extent=self.display_extent,
            interpolation="nearest",
            zorder=5,
        )

        selected = self.selected_record
        if selected is not None and self._has_result(selected) and self.mode == "idle":
            selected_mask = self.display_result_owner_image == selected.head_id
            self._draw_display_boundary(
                selected_mask,
                color=(1.0, 0.9, 0.0),
                alpha=1.0,
                zorder=8,
            )
            self._plot_path(
                selected.selected_points_xy,
                color="#ffe600",
                linewidth=2.3,
                zorder=9,
            )

        if self.manual_preview_fragment_ids:
            values = np.asarray(self.manual_preview_fragment_ids, dtype=np.uint32)
            preview_mask = np.isin(self.display_fragment_labels, values)
            preview_rgba = np.zeros(
                (self.display_height, self.display_width, 4),
                dtype=np.float32,
            )
            preview_rgba[preview_mask] = (1.0, 0.88, 0.0, 0.38)
            self.axis.imshow(
                preview_rgba,
                extent=self.display_extent,
                interpolation="nearest",
                zorder=10,
            )
            self._draw_display_boundary(
                preview_mask,
                color=(1.0, 0.9, 0.0),
                alpha=1.0,
                zorder=11,
            )

        if self.manual_conflict_fragment_ids:
            values = np.asarray(self.manual_conflict_fragment_ids, dtype=np.uint32)
            conflict_mask = np.isin(self.display_fragment_labels, values)
            conflict_rgba = np.zeros(
                (self.display_height, self.display_width, 4),
                dtype=np.float32,
            )
            conflict_rgba[conflict_mask] = (1.0, 0.0, 0.0, 0.52)
            self.axis.imshow(
                conflict_rgba,
                extent=self.display_extent,
                interpolation="nearest",
                zorder=12,
            )

        if self.manual_preview is not None:
            self._plot_path(
                self.manual_preview,
                color="#ffe600",
                linewidth=2.8,
                zorder=13,
            )

        if self.manual_points:
            manual_array = np.asarray(self.manual_points, dtype=np.float32)
            self.axis.plot(
                manual_array[:, 0],
                manual_array[:, 1],
                "o",
                color="#ffe600",
                markeredgecolor="#000000",
                markeredgewidth=0.8,
                markersize=5.8,
                zorder=14,
            )

        focus_index = (
            self.manual_target_index
            if self.manual_target_index is not None
            else self.selected_index
        )
        if focus_index is not None:
            record = self.records[focus_index]
            self.axis.scatter(
                [record.center_x],
                [record.center_y],
                s=115,
                facecolors="none",
                edgecolors="#ffe600",
                linewidths=2.3,
                zorder=16,
            )

        self.axis.set_title(
            "精子尾部结果校准  |  白色＝已有结果  黄色＝当前选择/新增预览",
            fontsize=12,
        )
        self._update_button_labels()
        self._update_status_text()
        self.figure.canvas.draw_idle()

    def _update_button_labels(self) -> None:
        selected = self.selected_record
        if self.mode == "drawing":
            target = self.records[self.manual_target_index]  # type: ignore[index]
            prefix = "确认重画" if self._has_result(target) else "确认新增"
            primary_label = prefix if self.manual_preview is not None else "请继续点击尾部"
        elif self.mode == "await_head":
            primary_label = "请点击头部"
        elif selected is not None and self._has_result(selected):
            primary_label = "重新绘制"
        else:
            primary_label = "新增尾部"
        self.buttons["primary"].label.set_text(primary_label)
        self.buttons["fragments"].label.set_text(
            "隐藏碎片" if self.show_fragments else "显示碎片"
        )

    def _update_status_text(self) -> None:
        recognized_count = len(self._accepted_records())
        selected = self.selected_record

        if self.mode == "await_head":
            instruction = (
                "请点击需要新增尾部的头部；若该头部当前没有结果，点击后会立即开始绘制。"
            )
        elif self.mode == "drawing":
            target = self.records[self.manual_target_index]  # type: ignore[index]
            if len(self.manual_points) < 2:
                instruction = (
                    f"Head {target.head_id}：请点击尾部起点，再点击下一个路径点。"
                )
            else:
                instruction = (
                    f"Head {target.head_id}：黄色路径会在每次点击后自动更新；"
                    "路线不对就撤销最后一点，再点击更靠近真实尾部的中间点；"
                    "正确后点击确认。"
                )
        elif selected is None:
            instruction = (
                "白色轮廓是已有结果：点击后可直接删除或重画。"
                "没有结果的头部：直接点击头部会自动进入新增。"
                "本来没有合格尾部的头部无需操作。"
            )
        elif self._has_result(selected):
            instruction = (
                f"已选中 Head {selected.head_id} 的已有结果；"
                "点击“删除所选”即可删除，点击“重新绘制”即可重画。"
            )
        else:
            instruction = (
                f"Head {selected.head_id} 当前没有结果；"
                "确有合格尾部时直接开始点击尾部路径，没有尾部则取消即可。"
            )

        text = f"已有尾部结果：{recognized_count} 条\n{instruction}"
        if self.message:
            text += f"\n{self.message}"
        self.status_text.set_text(text)

    def _autosave_state(self) -> None:
        selected_head_id = (
            self.selected_record.head_id
            if self.selected_record is not None
            else None
        )
        payload = {
            "version": VERSION,
            "saved_at_unix": time.time(),
            "current_head_id": selected_head_id,
            "records": [
                {
                    "head_id": record.head_id,
                    "current_status": record.current_status,
                    "selected_rank": record.selected_rank,
                    "selected_points_xy": record.selected_points_xy.tolist(),
                    "selected_fragment_ids": record.selected_fragment_ids,
                    "selected_source": record.selected_source,
                    "accepted_by_user": record.accepted_by_user,
                    "deleted": record.deleted,
                    "edit_note": record.edit_note,
                }
                for record in self.records
            ],
        }
        self.state_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _push_history(self, index: int) -> None:
        self.edit_history.append((index, deepcopy(self.records[index])))
        if len(self.edit_history) > 20:
            self.edit_history.pop(0)

    def _restore_record(self, index: int, record: EditorRecord) -> None:
        self.records[index] = record
        self.record_by_head[record.head_id] = record
        self.selected_index = index
        self._mark_result_cache_dirty()

    def _clear_manual_operation(self) -> None:
        self.mode = "idle"
        self.manual_target_index = None
        self.manual_points = []
        self.manual_segments = []
        self.manual_preview = None
        self.manual_preview_fragment_ids = []
        self.manual_conflict_fragment_ids = []

    def _select_head_id(self, head_id: int) -> None:
        for index, record in enumerate(self.records):
            if record.head_id == int(head_id):
                self.selected_index = index
                self.message = ""
                return

    def _select_at_point(self, point: tuple[float, float]) -> str:
        """返回 result / head / none；已有结果优先于头部。"""
        x, y = point
        xi = int(round(x))
        yi = int(round(y))
        height, width = self.fragment_labels.shape
        self._ensure_result_cache()

        if self.path_tree is not None and len(self.path_tree_head_ids):
            distance, path_index = self.path_tree.query(
                np.asarray(point, dtype=np.float32),
                k=1,
            )
            if float(distance) <= 22.0:
                self._select_head_id(
                    int(self.path_tree_head_ids[int(path_index)])
                )
                return "result"

        if 0 <= xi < width and 0 <= yi < height:
            owner = int(self.result_owner_image[yi, xi])
            if owner > 0:
                self._select_head_id(owner)
                return "result"

        distance, head_index = self.head_tree.query(
            np.asarray(point, dtype=np.float32),
            k=1,
        )
        if float(distance) <= 45.0:
            self.selected_index = int(head_index)
            self.message = ""
            return "head"

        self.selected_index = None
        self.message = "未选中已有尾部或头部。"
        return "none"

    def _start_drawing(self, index: int) -> None:
        self.selected_index = index
        self.manual_target_index = index
        self.mode = "drawing"
        self.manual_points = []
        self.manual_segments = []
        self.manual_preview = None
        self.manual_preview_fragment_ids = []
        self.manual_conflict_fragment_ids = []
        self.show_fragments = True
        target = self.records[index]
        action = "重画" if self._has_result(target) else "新增"
        self.message = (
            f"Head {target.head_id}：已自动进入{action}，请点击尾部起点。"
        )

    def primary_action(self, _event: Any = None) -> None:
        if self.mode == "idle":
            if self.selected_index is None:
                self.mode = "await_head"
                self.message = "请直接点击需要新增尾部的头部。"
            else:
                self._start_drawing(self.selected_index)
        elif self.mode == "await_head":
            self.message = "请先在图中点击对应头部。"
        elif self.mode == "drawing":
            self.confirm_preview()
        self.redraw()

    def _append_manual_point(self, point: tuple[float, float]) -> None:
        current = np.asarray(point, dtype=np.float32)
        if self.manual_points:
            previous = np.asarray(self.manual_points[-1], dtype=np.float32)
            if float(np.linalg.norm(previous - current)) < 1.0:
                return

        if not self.manual_points:
            self.manual_points.append(point)
            self.manual_preview = None
            self.manual_preview_fragment_ids = []
            self.manual_conflict_fragment_ids = []
            self.message = "已设置尾部起点，请继续点击下一处尾部位置。"
            return

        previous = np.asarray(self.manual_points[-1], dtype=np.float32)
        try:
            segment = local_route(
                self.manual_evidence,
                previous,
                current,
                margin_px=self.manual_margin_px,
            )
        except Exception as exc:
            self.message = f"该段路径计算失败：{exc}"
            return

        if self.manual_segments and len(segment) > 0:
            segment = segment[1:]
        self.manual_points.append(point)
        self.manual_segments.append(segment)
        self._refresh_live_preview()

    def _refresh_live_preview(self) -> None:
        if not self.manual_segments:
            self.manual_preview = None
            self.manual_preview_fragment_ids = []
            self.manual_conflict_fragment_ids = []
            return

        nonempty = [segment for segment in self.manual_segments if len(segment)]
        if not nonempty:
            self.manual_preview = None
            self.manual_preview_fragment_ids = []
            self.manual_conflict_fragment_ids = []
            return

        route = deduplicate_points(np.vstack(nonempty))
        route = smooth_polyline(route, sigma=0.8)
        target = self.records[self.manual_target_index]  # type: ignore[index]
        candidate_ids = fragment_ids_near_path(
            self.fragment_labels,
            route,
            radius_px=self.manual_fragment_radius_px,
        )

        self._ensure_result_cache()
        allowed: list[int] = []
        conflicts: list[int] = []
        for fragment_id in candidate_ids:
            owner = (
                int(self.result_owner_lut[fragment_id])
                if 0 <= fragment_id < len(self.result_owner_lut)
                else 0
            )
            if owner not in {0, target.head_id}:
                conflicts.append(fragment_id)
            else:
                allowed.append(fragment_id)

        self.manual_preview = route
        self.manual_preview_fragment_ids = allowed
        self.manual_conflict_fragment_ids = conflicts
        self.message = (
            f"路径已自动更新：{len(self.manual_points)}个点击点，"
            f"选中{len(allowed)}个碎片。"
        )
        if conflicts:
            self.message += f" {len(conflicts)}个冲突碎片已排除。"
        if not allowed:
            self.message += " 当前未选中碎片，请撤销后增加更靠近尾部的引导点。"

    def finish_manual(self) -> None:
        if self.mode != "drawing":
            return
        if len(self.manual_points) < 2 or self.manual_preview is None:
            self.message = "至少需要点击尾部起点和下一个路径点。"
            return
        self.message = "当前路径已自动生成；确认正确后点击确认，错误则撤销或继续增加路径点。"

    def confirm_preview(self) -> None:
        if self.mode != "drawing" or self.manual_target_index is None:
            return
        if self.manual_preview is None or len(self.manual_preview) < 2:
            self.message = "当前还没有可确认的路径，请至少点击两个尾部位置。"
            return
        if not self.manual_preview_fragment_ids:
            self.message = (
                "当前路径没有选中可用尾部碎片，暂不能确认。"
                "请撤销最后一点，改为点击更靠近真实尾部的位置。"
            )
            return

        index = self.manual_target_index
        self._push_history(index)
        record = self.records[index]
        record.selected_points_xy = self.manual_preview.copy()
        record.selected_fragment_ids = list(self.manual_preview_fragment_ids)
        record.selected_rank = None
        record.selected_source = "manual"
        record.current_status = "user_accepted"
        record.accepted_by_user = True
        record.deleted = False
        record.edit_note = (
            f"人工新增/重画，导向点数={len(self.manual_points)}"
        )
        self.selected_index = index
        head_id = record.head_id
        self._clear_manual_operation()
        self._mark_result_cache_dirty()
        self.message = f"Head {head_id} 的尾部结果已确认并自动保存。"
        self._autosave_state()

    def delete_selected(self, _event: Any = None) -> None:
        if self.mode != "idle":
            self.message = "请先确认或取消当前绘制操作。"
            self.redraw()
            return
        record = self.selected_record
        if record is None:
            self.message = "请先点击需要删除的已有尾部。"
            self.redraw()
            return
        if not self._has_result(record):
            self.message = (
                f"Head {record.head_id} 当前没有尾部结果，无需删除。"
            )
            self.redraw()
            return

        index = self.selected_index
        assert index is not None
        self._push_history(index)
        record.current_status = "deleted"
        record.selected_points_xy = np.zeros((0, 2), dtype=np.float32)
        record.selected_rank = None
        record.selected_source = "deleted"
        record.selected_fragment_ids = []
        record.accepted_by_user = False
        record.deleted = True
        record.edit_note = "人工删除"
        self._mark_result_cache_dirty()
        self.message = f"Head {record.head_id} 的尾部结果已删除并自动保存。"
        self._autosave_state()
        self.redraw()

    def undo_action(self, _event: Any = None) -> None:
        if self.mode == "drawing":
            if len(self.manual_points) <= 1:
                if self.manual_points:
                    self.manual_points.pop()
                self.manual_segments = []
                self.manual_preview = None
                self.manual_preview_fragment_ids = []
                self.manual_conflict_fragment_ids = []
                self.message = "已撤销起点，请重新点击尾部起点。"
            else:
                self.manual_points.pop()
                if self.manual_segments:
                    self.manual_segments.pop()
                self._refresh_live_preview()
                self.message = (
                    f"已撤销最后一个路径点，剩余{len(self.manual_points)}个点击点。"
                )
            self.redraw()
            return

        if self.edit_history:
            index, snapshot = self.edit_history.pop()
            self._restore_record(index, snapshot)
            self.message = f"已撤销 Head {snapshot.head_id} 的上一次编辑。"
            self._autosave_state()
        else:
            self.message = "当前没有可撤销的编辑。"
        self.redraw()

    def cancel_operation(self, _event: Any = None) -> None:
        if self.mode != "idle":
            target_head_id = (
                self.records[self.manual_target_index].head_id
                if self.manual_target_index is not None
                else None
            )
            self._clear_manual_operation()
            self.message = (
                f"已取消 Head {target_head_id} 的绘制，原结果未改变。"
                if target_head_id is not None
                else "已取消新增操作。"
            )
        else:
            self.selected_index = None
            self.message = "已取消当前选择。"
        self.redraw()

    def toggle_fragments(self, _event: Any = None) -> None:
        self.show_fragments = not self.show_fragments
        self.message = (
            "已显示RunOmnipose尾部碎片。"
            if self.show_fragments
            else "已隐藏RunOmnipose尾部碎片。"
        )
        self.redraw()

    def on_mouse_click(self, event: Any) -> None:
        if event.inaxes != self.axis:
            return
        if event.xdata is None or event.ydata is None:
            return
        point = (float(event.xdata), float(event.ydata))

        if self.mode == "await_head":
            distance, head_index = self.head_tree.query(
                np.asarray(point, dtype=np.float32),
                k=1,
            )
            if float(distance) <= 45.0:
                self._start_drawing(int(head_index))
            else:
                self.message = "这里没有检测到头部，请点击红色头部中心。"
            self.redraw()
            return

        if self.mode == "drawing":
            self._append_manual_point(point)
            self.redraw()
            return

        selection_kind = self._select_at_point(point)
        if selection_kind == "head" and self.selected_index is not None:
            record = self.records[self.selected_index]
            if not self._has_result(record):
                self._start_drawing(self.selected_index)
        self.redraw()

    def on_key_press(self, event: Any) -> None:
        key = str(event.key or "").lower()
        if key in {"enter", "a"}:
            self.primary_action()
        elif key in {"delete", "backspace", "d"}:
            self.delete_selected()
        elif key in {"u", "ctrl+z"}:
            self.undo_action()
        elif key == "escape":
            self.cancel_operation()
        elif key == "f":
            self.toggle_fragments()
        elif key in {"s", "ctrl+s"}:
            self.save_all()

    def _output_record(
        self,
        record: EditorRecord,
    ) -> dict[str, Any]:
        path = self._selected_path(record)
        metrics = sample_probability(self.probability, path)
        region_pixel_count = 0
        if record.selected_fragment_ids:
            region_pixel_count = int(
                np.count_nonzero(
                    np.isin(
                        self.fragment_labels,
                        np.asarray(
                            record.selected_fragment_ids,
                            dtype=np.uint32,
                        ),
                    )
                )
            )
        return {
            "head_id": record.head_id,
            "center_x": record.center_x,
            "center_y": record.center_y,
            "entry_status": record.entry_status,
            "initial_status": record.initial_status,
            "current_status": record.current_status,
            "accepted_by_user": record.accepted_by_user,
            "deleted": record.deleted,
            "selected_source": record.selected_source,
            "selected_rank": record.selected_rank,
            "selected_points_xy": path.tolist(),
            "selected_fragment_ids": record.selected_fragment_ids,
            "fragment_count": len(record.selected_fragment_ids),
            "region_pixel_count": region_pixel_count,
            "length_px": path_length(path),
            **metrics,
            "review_reasons": record.review_reasons,
            "original_global_status": record.original_global_status,
            "edit_note": record.edit_note,
        }

    def _render_export_overlay(self) -> np.ndarray:
        overlay = self.merge_rgb.copy()
        self._ensure_result_cache()
        mask = self.result_owner_image > 0
        boundary = self._mask_boundary(mask)
        overlay[boundary] = np.asarray([255, 255, 255], dtype=np.uint8)

        for record in self._accepted_records():
            path = record.selected_points_xy
            if len(path) < 2:
                continue
            points_int = np.rint(path).astype(np.int32)
            cv2.polylines(
                overlay,
                [points_int.reshape(-1, 1, 2)],
                False,
                (0, 0, 0),
                4,
                lineType=cv2.LINE_AA,
            )
            cv2.polylines(
                overlay,
                [points_int.reshape(-1, 1, 2)],
                False,
                (255, 255, 255),
                2,
                lineType=cv2.LINE_AA,
            )
        return overlay

    def save_all(self, _event: Any = None) -> None:
        self._autosave_state()
        rows = [self._output_record(record) for record in self.records]

        payload = {
            "version": VERSION,
            "saved_at_unix": time.time(),
            "status_counts": self._status_counts(),
            "results": rows,
        }
        (self.output_dir / "edited_tail_results.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        csv_fields = [
            "head_id",
            "entry_status",
            "initial_status",
            "current_status",
            "accepted_by_user",
            "deleted",
            "selected_source",
            "selected_rank",
            "fragment_count",
            "region_pixel_count",
            "length_px",
            "mean_probability",
            "min_probability",
            "low_probability_fraction",
            "edit_note",
        ]
        with (self.output_dir / "edited_tail_summary.csv").open(
            "w",
            encoding="utf-8-sig",
            newline="",
        ) as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=csv_fields,
                extrasaction="ignore",
            )
            writer.writeheader()
            writer.writerows(rows)

        overlay = self._render_export_overlay()
        Image.fromarray(overlay).save(
            self.output_dir / "edited_tail_overlay.png"
        )

        centerlines = np.zeros(self.merge_rgb.shape[:2], dtype=np.uint16)
        head_id_labels = np.zeros(self.merge_rgb.shape[:2], dtype=np.uint16)
        for record in self._accepted_records():
            path = record.selected_points_xy
            if len(path) < 2:
                continue
            points_int = np.rint(path).astype(np.int32)
            cv2.polylines(
                centerlines,
                [points_int.reshape(-1, 1, 2)],
                False,
                65535,
                1,
                lineType=cv2.LINE_8,
            )
            cv2.polylines(
                head_id_labels,
                [points_int.reshape(-1, 1, 2)],
                False,
                int(record.head_id),
                1,
                lineType=cv2.LINE_8,
            )

        Image.fromarray(centerlines).save(
            self.output_dir / "edited_tail_centerlines_uint16.tif"
        )
        Image.fromarray(head_id_labels).save(
            self.output_dir / "edited_tail_head_id_labels_uint16.tif"
        )

        region_labels, conflict_rows = build_path_aware_region_labels(
            self.fragment_labels,
            self._accepted_records(),
        )

        Image.fromarray(region_labels).save(
            self.output_dir / "edited_tail_regions_head_id_uint16.tif"
        )
        (self.output_dir / "edited_tail_region_conflicts.json").write_text(
            json.dumps(
                {"conflicts": conflict_rows},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        recognized_count = len(self._accepted_records())
        self.message = (
            f"已保存{recognized_count}条尾部结果到：{self.output_dir}"
        )
        self.redraw()
        print(self.message)

    def on_close(self, _event: Any) -> None:
        try:
            self._autosave_state()
        except Exception:
            pass

    def show(self) -> None:
        plt.show()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="精子尾部整图所见即所得校准工具V2.2"
    )
    parser.add_argument(
        "--merge",
        required=True,
        help="1_Merge.tif",
    )
    parser.add_argument(
        "--probability",
        required=True,
        help="路径引导概率图，例如02_probability_uint16.tif",
    )
    parser.add_argument(
        "--fragments",
        required=True,
        help="RunOmnipose高召回对象转图像uint16碎片标签图",
    )
    parser.add_argument(
        "--green",
        required=True,
        help="原始绿色尾部图",
    )
    parser.add_argument(
        "--entries",
        required=True,
        help="Stage 2.1 head_graph_entry_results.json",
    )
    parser.add_argument(
        "--paths",
        required=True,
        help="Stage 2.2 path_results.json",
    )
    parser.add_argument(
        "--global-results",
        required=True,
        help="Stage 2.3 global_selection_results.json",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
    )
    parser.add_argument(
        "--manual-margin",
        type=int,
        default=60,
        help="手动分段最小代价路径ROI边距",
    )
    parser.add_argument(
        "--manual-radius",
        type=int,
        default=5,
        help="手动路径周围选择RunOmnipose碎片的半径",
    )
    parser.add_argument(
        "--display-max-dim",
        type=int,
        default=1400,
        help="界面显示图像最大边长；数值越小越流畅，最终输出仍保持原始分辨率",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="只校验输入，不打开界面",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    configure_chinese_font()

    merge_path = resolve_required(args.merge)
    probability_path = resolve_required(args.probability)
    fragments_path = resolve_required(args.fragments)
    green_path = resolve_required(args.green)
    entries_path = resolve_required(args.entries)
    paths_path = resolve_required(args.paths)
    global_results_path = resolve_required(args.global_results)
    output_dir = Path(args.output_dir).expanduser().resolve()

    merge_rgb = to_uint8_rgb(read_image(merge_path))
    probability = normalize_probability(
        read_image(probability_path)
    )
    fragment_labels = normalize_fragment_labels(
        read_image(fragments_path)
    )
    green_image = read_image(green_path)
    entries_payload = load_json(entries_path)
    path_payload = load_json(paths_path)
    global_payload = load_json(global_results_path)

    if probability.shape != merge_rgb.shape[:2]:
        raise ValueError(
            "概率图与Merge图尺寸不一致："
            f"{probability.shape} != {merge_rgb.shape[:2]}"
        )
    if fragment_labels.shape != merge_rgb.shape[:2]:
        raise ValueError(
            "尾部碎片图与Merge图尺寸不一致："
            f"{fragment_labels.shape} != {merge_rgb.shape[:2]}"
        )
    if (
        green_image is not None
        and green_image.shape[:2] != merge_rgb.shape[:2]
    ):
        raise ValueError(
            "绿色尾部图与Merge图尺寸不一致："
            f"{green_image.shape[:2]} != {merge_rgb.shape[:2]}"
        )

    entry_count = len(entries_payload.get("results", []))
    path_count = len(path_payload.get("results", []))
    global_count = len(global_payload.get("results", []))

    print(f"版本：{VERSION}")
    print(f"入口头部：{entry_count}")
    print(f"路径头部：{path_count}")
    print(f"全局分配头部：{global_count}")
    print(f"输出目录：{output_dir}")

    if entry_count == 0:
        raise ValueError("入口结果中没有头部。")
    if global_count == 0:
        raise ValueError("全局分配结果为空。")

    if args.validate_only:
        print("INPUT_VALIDATE_OK")
        return 0

    editor = TailResultEditor(
        merge_rgb=merge_rgb,
        probability=probability,
        fragment_labels=fragment_labels,
        green_image=green_image,
        entries_payload=entries_payload,
        path_payload=path_payload,
        global_payload=global_payload,
        output_dir=output_dir,
        manual_margin_px=int(args.manual_margin),
        manual_fragment_radius_px=int(args.manual_radius),
        display_max_dim=int(args.display_max_dim),
    )
    editor.show()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
