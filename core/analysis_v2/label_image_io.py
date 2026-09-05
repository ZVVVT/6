"""Analysis V2 uint16 标签图的 OpenCV 读写与验证。"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import numpy as np

from .opencv_compat import cv2


_REPLACE_RETRY_DELAYS = (0.05, 0.10, 0.20, 0.40)


def read_label_image(
    path: Path,
    expected_shape: Optional[Tuple[int, int]] = None,
    require_objects: bool = True,
) -> np.ndarray:
    """读取并验证二维 uint16 对象标签图。"""
    label_path = Path(path)
    if not label_path.is_file():
        raise FileNotFoundError("标签图不存在：{}".format(label_path))
    labels = cv2.imread(str(label_path), cv2.IMREAD_UNCHANGED)
    if labels is None:
        raise ValueError("OpenCV 无法读取标签图：{}".format(label_path))
    validate_label_image(labels, expected_shape=expected_shape, require_objects=require_objects)
    return labels


def validate_label_image(
    labels: np.ndarray,
    expected_shape: Optional[Tuple[int, int]] = None,
    require_objects: bool = True,
) -> Dict[str, Any]:
    """验证标签数组并返回稳定统计。"""
    array = np.asarray(labels)
    if array.ndim != 2:
        raise ValueError("标签图必须是二维数组，实际 shape={}".format(array.shape))
    if array.dtype != np.uint16:
        raise ValueError("标签图 dtype 必须是 uint16，实际为 {}".format(array.dtype))
    if expected_shape is not None and tuple(array.shape) != tuple(expected_shape):
        raise ValueError(
            "标签尺寸 {} 与预期 {} 不一致".format(tuple(array.shape), tuple(expected_shape))
        )
    minimum = int(array.min()) if array.size else 0
    if minimum != 0:
        raise ValueError("标签背景必须包含并以 0 表示")
    positive = np.unique(array[array > 0])
    if require_objects and positive.size == 0:
        raise ValueError("标签图不包含正整数对象")
    if positive.size and int(positive[-1]) > np.iinfo(np.uint16).max:
        raise ValueError("标签值超过 uint16 上限")
    return {
        "dtype": str(array.dtype),
        "shape": [int(array.shape[0]), int(array.shape[1])],
        "minimum_label": minimum,
        "maximum_label": int(positive[-1]) if positive.size else 0,
        "object_count": int(positive.size),
        "nonzero_pixels": int(np.count_nonzero(array)),
        "positive_labels": [int(value) for value in positive.tolist()],
    }


def atomic_save_label_image(path: Path, labels: np.ndarray) -> Dict[str, Any]:
    """在目标同目录写唯一临时 TIFF，回读验证后原子替换。"""
    target = Path(path)
    statistics = validate_label_image(labels, require_objects=False)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".{}.".format(target.stem),
        suffix=".tmp.tif",
        dir=str(target.parent),
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        if not cv2.imwrite(str(temporary_path), np.asarray(labels)):
            raise OSError("OpenCV 写入临时标签失败：{}".format(temporary_path))
        verified = read_label_image(
            temporary_path,
            expected_shape=tuple(np.asarray(labels).shape),
            require_objects=False,
        )
        if not np.array_equal(verified, labels):
            raise ValueError("临时标签回读内容与内存标签不一致")
        for attempt in range(len(_REPLACE_RETRY_DELAYS) + 1):
            try:
                os.replace(str(temporary_path), str(target))
                break
            except PermissionError:
                if attempt >= len(_REPLACE_RETRY_DELAYS):
                    raise
                time.sleep(_REPLACE_RETRY_DELAYS[attempt])
    finally:
        if temporary_path.exists():
            temporary_path.unlink()
    return statistics


def relabel_consecutive(labels: np.ndarray) -> Tuple[np.ndarray, Dict[int, int]]:
    """将正标签按旧 ID 升序连续重编号为 1..N。"""
    validate_label_image(labels, require_objects=False)
    positive = np.unique(labels[labels > 0])
    result = np.zeros(labels.shape, dtype=np.uint16)
    mapping = {}  # type: Dict[int, int]
    for new_id, old_value in enumerate(positive.tolist(), start=1):
        if new_id > np.iinfo(np.uint16).max:
            raise ValueError("对象数超过 uint16 上限")
        old_id = int(old_value)
        mapping[old_id] = new_id
        result[labels == old_id] = new_id
    return result, mapping


def read_color_image(path: Path) -> np.ndarray:
    image_path = Path(path)
    image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("OpenCV 无法读取底图：{}".format(image_path))
    return image
