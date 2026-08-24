"""Adapt C18B score015 output to the existing Analysis V2 Stage 1 contract.

This module changes only automatic tail recognition.  The existing topology,
matching, editor, export, measurement and report stages continue to consume
their original files and code paths.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict


_C18B_REEXEC_MARKER = "SPERM_ANALYZER_C18B_REEXEC"


def _reexec_with_c18b_python() -> None:
    """Run this adapter in its dedicated environment before third-party imports."""
    if os.environ.get(_C18B_REEXEC_MARKER) == "1":
        return

    project_dir = Path(__file__).resolve().parents[2]
    runtime_root = project_dir / ".venv-c18b"
    candidates = (
        runtime_root / "python.exe",
        runtime_root / "Scripts" / "python.exe",
    )
    c18b_python = next((path for path in candidates if path.is_file()), None)
    if c18b_python is None:
        return

    current_python = os.path.normcase(os.path.realpath(sys.executable))
    target_python = os.path.normcase(os.path.realpath(str(c18b_python)))
    if current_python == target_python:
        return

    child_env = os.environ.copy()
    child_env[_C18B_REEXEC_MARKER] = "1"
    try:
        completed = subprocess.run(
            [str(c18b_python), str(Path(__file__).resolve())] + sys.argv[1:],
            env=child_env,
        )
    except OSError:
        # Continue locally; missing dependencies will produce an explicit
        # non-zero adapter exit instead of switching to another backend.
        return
    raise SystemExit(completed.returncode)


_reexec_with_c18b_python()

import cv2
import numpy as np
import tifffile


ADAPTER_VERSION = "c18b_score015_stage1_adapter_v1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_gray(path: Path) -> np.ndarray:
    image = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if image is None:
        raise FileNotFoundError("无法读取图像：{}".format(path))
    if image.ndim == 3:
        image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    if image.ndim != 2:
        raise ValueError("图像必须是二维灰度图：{}".format(path))
    return image


def _load_config(c18b_dir: Path) -> Dict[str, Any]:
    candidates = (
        c18b_dir / "frozen_parameters.json",
        c18b_dir / "config" / "frozen_parameters.json",
    )
    config_path = next((path for path in candidates if path.is_file()), None)
    if config_path is None:
        raise FileNotFoundError(
            "C18B score015 缺少 frozen_parameters.json；已检查：{}".format(
                ", ".join(str(path) for path in candidates)
            )
        )
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    required = (
        "graph",
        "validation_threshold",
        "candidate_merging",
        "region_growing",
        "identity_graph_v3",
        "reconstruction",
    )
    missing = [key for key in required if key not in payload]
    if missing:
        raise ValueError(
            "C18B frozen_parameters.json 缺少字段：{}".format(
                ", ".join(missing)
            )
        )
    threshold = float(payload["validation_threshold"])
    if abs(threshold - 0.15) > 1e-9:
        raise ValueError(
            "C18B 配置不是 score015：validation_threshold={}".format(
                threshold
            )
        )
    payload["_config_path"] = str(config_path.resolve())
    return payload


def _require_ximgproc() -> None:
    module = getattr(cv2, "ximgproc", None)
    if module is None or not hasattr(module, "thinning"):
        raise RuntimeError(
            "当前 OpenCV 不包含 cv2.ximgproc.thinning；"
            "无法生成 C18B Stage 1 兼容输出。"
        )


def _normalize_probability(enhanced: np.ndarray, mask: np.ndarray) -> np.ndarray:
    source = enhanced.astype(np.float32)
    maximum = float(source.max())
    if maximum > 0:
        source /= maximum
    source *= mask.astype(np.float32)
    return np.rint(np.clip(source, 0.0, 1.0) * 65535.0).astype(np.uint16)


def _validate_stage1_directory(stage1_dir: Path) -> None:
    required_names = (
        "02_probability_uint16.tif",
        "strict_skeleton_uint8.tif",
        "balanced_mask_uint8.tif",
        "c18b_score015_adapter_manifest.json",
    )
    missing = [
        str(stage1_dir / name)
        for name in required_names
        if not (stage1_dir / name).is_file()
        or (stage1_dir / name).stat().st_size <= 0
    ]
    if missing:
        raise RuntimeError(
            "C18B Stage 1 兼容输出不完整：\n{}".format("\n".join(missing))
        )

    manifest_path = stage1_dir / "c18b_score015_adapter_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("stage1_source") != "C18B":
        raise RuntimeError("C18B Stage 1 manifest 来源标记无效。")


def _publish_stage1_directory(temporary_dir: Path, output_dir: Path) -> None:
    backup_root = None
    backup_dir = None
    try:
        if output_dir.exists():
            backup_root = Path(
                tempfile.mkdtemp(
                    prefix=output_dir.name + ".previous-",
                    dir=str(output_dir.parent),
                )
            )
            backup_dir = backup_root / output_dir.name
            os.replace(str(output_dir), str(backup_dir))
        os.replace(str(temporary_dir), str(output_dir))
    except Exception:
        if backup_dir is not None and backup_dir.exists() and not output_dir.exists():
            os.replace(str(backup_dir), str(output_dir))
        raise
    else:
        if backup_root is not None and backup_root.exists():
            shutil.rmtree(str(backup_root))


def run_adapter(
    green_path: Path,
    head_labels_path: Path,
    output_dir: Path,
    c18b_output_dir: Path,
) -> Dict[str, Any]:
    started = time.perf_counter()
    c18b_dir = (Path(__file__).resolve().parent / "c18b_score015").resolve()
    config = _load_config(c18b_dir)
    _require_ximgproc()

    green_path = Path(green_path).resolve()
    head_labels_path = Path(head_labels_path).resolve()
    output_dir = Path(output_dir).resolve()
    c18b_output_dir = Path(c18b_output_dir).resolve()
    if not green_path.is_file():
        raise FileNotFoundError("C18B 绿色通道不存在：{}".format(green_path))
    if not head_labels_path.is_file():
        raise FileNotFoundError(
            "C18B 头部标签不存在：{}".format(head_labels_path)
        )

    sys.path.insert(0, str(c18b_dir))
    try:
        from run_pipeline import run_one

        result_dir, statistics = run_one(
            green_path,
            c18b_output_dir,
            {key: value for key, value in config.items() if not key.startswith("_")},
        )
    finally:
        try:
            sys.path.remove(str(c18b_dir))
        except ValueError:
            pass

    result_dir = Path(result_dir).resolve()
    labels_path = result_dir / "06_final_tail_instances.tif"
    enhanced_path = result_dir / "01_fitc_enhanced.png"
    if not labels_path.is_file() or not enhanced_path.is_file():
        raise RuntimeError(
            "C18B 输出不完整：labels={} enhanced={}".format(
                labels_path,
                enhanced_path,
            )
        )

    labels = _read_gray(labels_path)
    enhanced = _read_gray(enhanced_path)
    head_labels = _read_gray(head_labels_path)
    if labels.shape != enhanced.shape or labels.shape != head_labels.shape:
        raise ValueError(
            "C18B 输出尺寸不一致：labels={} enhanced={} heads={}".format(
                labels.shape,
                enhanced.shape,
                head_labels.shape,
            )
        )

    candidate_mask = labels > 0
    head_mask = cv2.dilate(
        (head_labels > 0).astype(np.uint8),
        np.ones((5, 5), dtype=np.uint8),
        iterations=1,
    ) > 0
    candidate_mask &= ~head_mask
    if not np.any(candidate_mask):
        raise RuntimeError("C18B 没有生成可供 matching 使用的尾部候选。")

    skeleton = cv2.ximgproc.thinning(
        candidate_mask.astype(np.uint8) * 255,
        thinningType=cv2.ximgproc.THINNING_ZHANGSUEN,
    )
    probability = _normalize_probability(enhanced, candidate_mask)
    mask_uint8 = candidate_mask.astype(np.uint8) * 255
    skeleton_uint8 = (skeleton > 0).astype(np.uint8) * 255

    manifest = {
        "schema_version": 1,
        "adapter_version": ADAPTER_VERSION,
        "candidate_backend": "c18b_score015",
        "stage1_source": "C18B",
        "green_path": str(green_path),
        "head_labels_path": str(head_labels_path),
        "c18b_result_dir": str(result_dir),
        "c18b_labels_path": str(labels_path),
        "config_path": config["_config_path"],
        "config_sha256": _sha256(Path(config["_config_path"])),
        "validation_threshold": float(config["validation_threshold"]),
        "instance_count": int(labels.max()),
        "candidate_pixel_count": int(np.count_nonzero(candidate_mask)),
        "skeleton_pixel_count": int(np.count_nonzero(skeleton_uint8)),
        "statistics": statistics,
        "elapsed_seconds": float(time.perf_counter() - started),
        "downstream_contract": (
            "legacy topology/matching/editor/export/measurement/report unchanged"
        ),
    }
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary_dir = Path(
        tempfile.mkdtemp(
            prefix=output_dir.name + ".c18b-",
            dir=str(output_dir.parent),
        )
    )
    try:
        tifffile.imwrite(
            str(temporary_dir / "02_probability_uint16.tif"),
            probability,
        )
        for preset in ("loose", "balanced", "strict"):
            tifffile.imwrite(
                str(temporary_dir / (preset + "_mask_uint8.tif")),
                mask_uint8,
            )
            tifffile.imwrite(
                str(temporary_dir / (preset + "_skeleton_uint8.tif")),
                skeleton_uint8,
            )

        manifest_path = temporary_dir / "c18b_score015_adapter_manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        _validate_stage1_directory(temporary_dir)
        _publish_stage1_directory(temporary_dir, output_dir)
    finally:
        if temporary_dir.exists():
            shutil.rmtree(str(temporary_dir))
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="将 C18B score015 尾部候选转换为 Analysis V2 Stage 1 输入。"
    )
    parser.add_argument("--green", required=True)
    parser.add_argument("--head-labels", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--c18b-output-dir", required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = run_adapter(
        green_path=Path(args.green),
        head_labels_path=Path(args.head_labels),
        output_dir=Path(args.output_dir),
        c18b_output_dir=Path(args.c18b_output_dir),
    )
    print("C18B score015 adapter 完成。")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
