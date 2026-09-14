"""Lightweight candidate generation for Analysis V2 tail measurement."""

import csv
import time
from decimal import Decimal, ROUND_HALF_EVEN
from pathlib import Path

import cv2
import numpy as np
import tifffile


IMAGE_HEADER = ("Count_G_objects", "Count_R_colocalized", "Count_R_objects",
                "ImageNumber", "Math_ColocalizationRate")
OBJECT_HEADER = ("ImageNumber", "ObjectNumber", "AreaShape_Area",
                 "Math_MeanIntensity255")
OVERLAYS = (("g", "tail_final_labels", (0, 0, 255), "G_G_objects"),
            ("r", "head_final_labels", (255, 255, 0), "R_R_objects"),
            ("r", "tail_positive_head_labels", (255, 255, 0), "G_G_colocalized"))


class TailMeasurementInputError(ValueError):
    """An engine-independent input contract failed."""


class LightweightGenerationError(RuntimeError):
    """An eligible, attempt-local array or overlay computation failed."""


def _read_field(record):
    paths = {key: Path(record[key + "_path"]) for key in
             ("g", "r", "head_final_labels", "tail_final_labels",
              "tail_positive_head_labels")}
    for path in paths.values():
        if not path.is_file():
            raise TailMeasurementInputError("测量输入不存在：{}".format(path))
    g = cv2.imread(str(paths["g"]), cv2.IMREAD_UNCHANGED)
    r = cv2.imread(str(paths["r"]), cv2.IMREAD_UNCHANGED)
    labels = {key: tifffile.imread(str(paths[key])) for key in
              ("head_final_labels", "tail_final_labels", "tail_positive_head_labels")}
    return g, r, labels


def validate_standardized_tail_fields(records, check_cancelled=lambda: None):
    """Check only shared input invariants before selecting an engine."""
    if not records:
        raise TailMeasurementInputError("没有尾部测量视野。")
    for record in sorted(records, key=lambda item: str(item["field_id"])):
        check_cancelled()
        g, r, labels = _read_field(record)
        check_cancelled()
        if (g is None or r is None or g.dtype != np.uint8 or
                r.dtype != np.uint8 or g.ndim != 3 or g.shape[2] != 3 or
                r.shape != g.shape):
            raise TailMeasurementInputError("视野 {} 的 G/R 必须为同尺寸 RGB uint8。".format(record["field_id"]))
        for name, array in labels.items():
            if (array.ndim != 2 or array.shape != g.shape[:2] or
                    not np.issubdtype(array.dtype, np.integer)):
                raise TailMeasurementInputError("视野 {} 的 {} 标签形状或类型错误。".format(record["field_id"], name))
        tail_ids = np.unique(labels["tail_final_labels"])
        tail_ids = tail_ids[tail_ids != 0]
        head_ids = np.unique(labels["head_final_labels"])
        head_ids = head_ids[head_ids != 0]
        positive_ids = np.unique(labels["tail_positive_head_labels"])
        positive_ids = positive_ids[positive_ids != 0]
        tail_count = int(record["tail_object_count"])
        if (tail_count <= 0 or not np.array_equal(tail_ids, np.arange(1, tail_count + 1)) or
                len(head_ids) <= 0 or len(head_ids) != int(record["head_object_count"]) or
                len(positive_ids) != int(record["associated_object_count"]) or
                not np.isin(positive_ids, tail_ids).all()):
            raise TailMeasurementInputError("视野 {} 的标签对象计数或 Tail ID 合同错误。".format(record["field_id"]))
        check_cancelled()


def _boundary(labels):
    padded = np.pad(labels, ((1, 1), (1, 1)), mode="symmetric")
    center = padded[1:-1, 1:-1]
    neighbors = (center, padded[:-2, 1:-1], padded[2:, 1:-1],
                 padded[1:-1, :-2], padded[1:-1, 2:])
    return np.maximum.reduce(neighbors) != np.minimum.reduce(neighbors)


def _rate(numerator, denominator):
    value = (Decimal(numerator) / Decimal(denominator)).quantize(
        Decimal("0.0001"), rounding=ROUND_HALF_EVEN)
    return format(value, ".4f")


def generate_tail_measurement(records, candidate_output, check_cancelled=lambda: None,
                              log=lambda message: None):
    """Write one pair of CSVs and three overlays per ordered field."""
    started = time.perf_counter()
    output = Path(candidate_output)
    output.mkdir(parents=True, exist_ok=False)
    ordered = sorted(records, key=lambda item: str(item["field_id"]))
    field_timings = []
    with (output / "Image.csv").open("w", encoding="utf-8", newline="") as image_file, \
            (output / "G_objects.csv").open("w", encoding="utf-8", newline="") as object_file:
        images = csv.writer(image_file, lineterminator="\r\n")
        objects = csv.writer(object_file, lineterminator="\r\n")
        images.writerow(IMAGE_HEADER)
        objects.writerow(OBJECT_HEADER)
        for image_number, record in enumerate(ordered, 1):
            field_started = time.perf_counter()
            check_cancelled()
            g, r, labels = _read_field(record)
            check_cancelled()
            tail_count = int(record["tail_object_count"])
            head_count = int(record["head_object_count"])
            associated = int(record["associated_object_count"])
            try:
                flat = labels["tail_final_labels"].reshape(-1)
                areas = np.bincount(flat, minlength=tail_count + 1)[1:tail_count + 1]
                sums = np.bincount(flat, weights=g[..., 1].reshape(-1),
                                   minlength=tail_count + 1)[1:tail_count + 1]
                means = sums / areas
            except (ValueError, TypeError, FloatingPointError) as error:
                raise LightweightGenerationError("{} math: {}".format(record["field_id"], error)) from error
            check_cancelled()
            images.writerow(("{}.0".format(tail_count), "{}.0".format(associated),
                             "{}.0".format(head_count), str(image_number),
                             _rate(associated, head_count)))
            for object_number, (area, mean) in enumerate(zip(areas, means), 1):
                objects.writerow((str(image_number), str(object_number), str(int(area)),
                                  str(np.round(mean, 2))))
            for background, label_name, color, suffix in OVERLAYS:
                check_cancelled()
                try:
                    mask = _boundary(labels[label_name])
                    overlay = (g if background == "g" else r).copy()
                    overlay[mask] = color
                except (ValueError, TypeError, FloatingPointError) as error:
                    raise LightweightGenerationError("{} overlay: {}".format(record["field_id"], error)) from error
                target = output / "{}_{}_OrigOverlay.png".format(record["field_id"], suffix)
                if not cv2.imwrite(str(target), overlay):
                    raise OSError("PNG 写入失败：{}".format(target))
                check_cancelled()
                del mask, overlay
            field_seconds = time.perf_counter() - field_started
            field_timings.append({"field_id": str(record["field_id"]),
                                  "generation_seconds": field_seconds})
            log("Lightweight field {} generation_seconds={:.3f}".format(
                record["field_id"], field_seconds))
    check_cancelled()
    return {"field_count": len(ordered), "generation_seconds": time.perf_counter() - started,
            "runtime": "lightweight_tail_measurement_v1", "fields": field_timings}
