"""Focused regression coverage for the C18B Head-independent split."""

import csv
import hashlib
import importlib.util
import inspect
import os
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest
import tifffile


ROOT = Path(__file__).resolve().parents[1]
ADAPTER_PATH = ROOT / "tools" / "analysis_v2" / "c18b_score015_adapter.py"


def _adapter(monkeypatch):
    monkeypatch.setenv("SPERM_ANALYZER_C18B_REEXEC", "1")
    name = "c18b_score015_adapter_split_test"
    spec = importlib.util.spec_from_file_location(name, ADAPTER_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _write_csv(path, fields, rows):
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _fake_backend(module, labels, enhanced):
    def fake_pipeline(green_path, c18b_output_dir, config,
                      candidate_path_mode, c18b_dir):
        result_dir = Path(c18b_output_dir) / Path(green_path).stem
        result_dir.mkdir(parents=True, exist_ok=True)
        tifffile.imwrite(str(result_dir / "06_final_tail_instances.tif"), labels)
        _write_csv(result_dir / "shadow_communities.csv", [
            "dense_final_instance_id", "identity_community_id",
            "max_candidate_path_length",
        ], [
            {"dense_final_instance_id": 1, "identity_community_id": 1,
             "max_candidate_path_length": 120},
            {"dense_final_instance_id": 2, "identity_community_id": 2,
             "max_candidate_path_length": 20},
        ])
        _write_csv(result_dir / "final_instance_diagnostics.csv", [
            "final_instance_id", "identity_community_id",
        ], [
            {"final_instance_id": 1, "identity_community_id": 1},
            {"final_instance_id": 2, "identity_community_id": 2},
        ])
        return result_dir, {"final_instance_count": int(labels.max())}

    module._run_c18b_pipeline = fake_pipeline
    module._load_enhanced_for_finalize = lambda green_path, c18b_dir: enhanced.copy()
    module._require_ximgproc = lambda: None

    class _XImgProc:
        THINNING_ZHANGSUEN = 0

        @staticmethod
        def thinning(value, thinningType):
            return value

    module.cv2.ximgproc = _XImgProc()


def _legacy_probability(labels, enhanced, heads, module):
    candidate_mask = labels > 0
    head_mask = cv2.dilate(
        (heads > 0).astype(np.uint8), np.ones((5, 5), dtype=np.uint8),
        iterations=1,
    ) > 0
    candidate_mask &= ~head_mask
    return candidate_mask, module._normalize_probability(enhanced, candidate_mask)


def test_serial_composition_matches_pre_split_head_postprocess(tmp_path, monkeypatch):
    module = _adapter(monkeypatch)
    labels = np.array([
        [0, 1, 1, 0, 2, 2],
        [0, 1, 1, 0, 2, 2],
        [0, 0, 0, 0, 0, 0],
    ], dtype=np.uint16)
    enhanced = np.array([
        [0, 8, 9, 0, 4, 6],
        [0, 7, 6, 0, 3, 5],
        [0, 0, 0, 0, 0, 0],
    ], dtype=np.uint8)
    heads = np.zeros(labels.shape, dtype=np.uint16)
    heads[0, 0] = 1
    green = tmp_path / "field_FITC.tif"
    head_path = tmp_path / "field_HeadFinalLabels.tif"
    tifffile.imwrite(str(green), enhanced)
    tifffile.imwrite(str(head_path), heads)
    _fake_backend(module, labels, enhanced)

    output_root = tmp_path / "c18b"
    contract = tmp_path / "contract"
    manifest = module.run_adapter(green, head_path, contract, output_root)

    expected_mask, expected_probability = _legacy_probability(
        labels, enhanced, heads, module,
    )
    actual_probability = tifffile.imread(str(contract / "02_probability_uint16.tif"))
    actual_mask = tifffile.imread(str(contract / "balanced_mask_uint8.tif"))
    actual_06 = tifffile.imread(str(output_root / green.stem / "06_final_tail_instances.tif"))
    actual_07 = tifffile.imread(
        str(output_root / green.stem / "07_extreme_fragment_filtered_labels.tif")
    )
    expected_07 = labels.copy()
    expected_07[expected_07 == 2] = 0
    assert np.array_equal(actual_06, labels)
    assert np.array_equal(actual_07, expected_07)
    assert actual_probability.shape == expected_probability.shape
    assert actual_probability.dtype == expected_probability.dtype
    assert np.array_equal(actual_probability, expected_probability)
    assert np.array_equal(actual_mask, expected_mask.astype(np.uint8) * 255)
    expected_probability_path = tmp_path / "expected_probability.tif"
    tifffile.imwrite(str(expected_probability_path), expected_probability)
    assert hashlib.sha256((contract / "02_probability_uint16.tif").read_bytes()).hexdigest() == hashlib.sha256(expected_probability_path.read_bytes()).hexdigest()
    assert manifest["candidate_pixel_count"] == int(np.count_nonzero(expected_mask))


def test_backend_has_no_head_parameter_or_head_file_access(tmp_path, monkeypatch):
    module = _adapter(monkeypatch)
    labels = np.array([[0, 1], [2, 2]], dtype=np.uint16)
    enhanced = np.array([[0, 2], [3, 4]], dtype=np.uint8)
    green = tmp_path / "field_FITC.tif"
    tifffile.imwrite(str(green), enhanced)
    _fake_backend(module, labels, enhanced)

    backend = module.run_backend(green, tmp_path / "c18b")
    assert backend.labels_path.is_file()
    assert backend.filtered_labels_path.is_file()
    assert not hasattr(backend, "head_labels_path")
    assert "head_labels" not in inspect.getsource(module.run_backend)


def test_finalize_requires_head_with_existing_error_semantics(tmp_path, monkeypatch):
    module = _adapter(monkeypatch)
    labels = np.array([[0, 1], [1, 0]], dtype=np.uint16)
    enhanced = np.array([[0, 5], [4, 0]], dtype=np.uint8)
    green = tmp_path / "field_FITC.tif"
    tifffile.imwrite(str(green), enhanced)
    _fake_backend(module, labels, enhanced)
    backend = module.run_backend(green, tmp_path / "c18b")

    with pytest.raises(FileNotFoundError, match="头部标签不存在"):
        module.finalize_with_head(
            backend, tmp_path / "missing_HeadFinalLabels.tif", tmp_path / "contract",
        )


def test_tail_core_recovery_and_association_stay_after_head_finalize():
    execution_source = (
        ROOT / "core" / "analysis_v2" / "c18b_execution.py"
    ).read_text(encoding="utf-8")
    prepare = execution_source[
        execution_source.index("def _prepare_c18b_editor_payload"):
        execution_source.index("def _recovery_source_for_field")
    ]
    assert prepare.index('"--finalize-with-head"') < prepare.index(
        "write_and_verify_tail_core_checkpoint"
    )
    assert prepare.index("write_and_verify_tail_core_checkpoint") < prepare.index(
        "write_and_verify_association_checkpoint"
    )
    assert "recover_and_verify_tail_core_checkpoint" in execution_source
    assert "tail_core_recovery_sources" in execution_source
