import json
import sys
import types

import numpy as np
import pytest
import tifffile


if "torch" not in sys.modules:
    sys.modules["torch"] = types.ModuleType("torch")
if "cellpose" not in sys.modules:
    cellpose = types.ModuleType("cellpose")
    cellpose.models = types.ModuleType("cellpose.models")
    sys.modules["cellpose"] = cellpose
    sys.modules["cellpose.models"] = cellpose.models

from core.analysis_v2.segmentation_service import validate_worker_field
from tools.analysis_v2.direct_cellpose_worker import validate_saved_labels


def _write_labels(tmp_path, labels):
    path = tmp_path / "labels.tif"
    tifffile.imwrite(str(path), labels)
    return path


def test_single_instance_labels_are_valid(tmp_path):
    labels = np.zeros((8, 8), dtype=np.uint16)
    labels[2:5, 3:6] = 1
    path = _write_labels(tmp_path, labels)

    stats = validate_saved_labels(path, labels.shape, object_count=1)

    assert stats["positive_unique_count"] == 1
    assert stats["maximum_label"] == 1
    assert stats["is_binary"] is True


def test_multiple_instance_labels_are_valid(tmp_path):
    labels = np.zeros((8, 8), dtype=np.uint16)
    labels[1:3, 1:3] = 1
    labels[5:7, 5:7] = 2
    path = _write_labels(tmp_path, labels)

    stats = validate_saved_labels(path, labels.shape, object_count=2)

    assert stats["positive_unique_count"] == 2
    assert stats["maximum_label"] == 2


def test_zero_labels_are_rejected(tmp_path):
    labels = np.zeros((8, 8), dtype=np.uint16)
    path = _write_labels(tmp_path, labels)

    with pytest.raises(ValueError, match="没有正标签对象"):
        validate_saved_labels(path, labels.shape, object_count=0)


def test_declared_object_count_mismatch_is_rejected(tmp_path):
    labels = np.zeros((8, 8), dtype=np.uint16)
    labels[2:5, 3:6] = 1
    path = _write_labels(tmp_path, labels)

    with pytest.raises(ValueError):
        validate_saved_labels(path, labels.shape, object_count=2)


def test_worker_field_accepts_valid_single_instance(tmp_path):
    labels = np.zeros((8, 8), dtype=np.uint16)
    labels[2:5, 3:6] = 1
    labels_path = _write_labels(tmp_path, labels)
    overlay_path = tmp_path / "overlay.png"
    overlay_path.write_bytes(b"overlay")
    objects_path = tmp_path / "objects.json"
    objects_path.write_text(
        json.dumps({"objects": [{"object_id": 1}]}),
        encoding="utf-8",
    )
    field = {
        "field_id": "single",
        "error": None,
        "labels_output_path": str(labels_path),
        "overlay_output_path": str(overlay_path),
        "objects_output_path": str(objects_path),
        "labels_dtype": "uint16",
        "source_shape": [8, 8, 3],
        "labels_shape": [8, 8],
        "object_count": 1,
        "minimum_label": 0,
        "maximum_label": 1,
        "positive_unique_count": 1,
        "is_binary": True,
    }

    assert validate_worker_field(field) is field
