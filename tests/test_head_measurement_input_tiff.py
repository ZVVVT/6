import ast
import hashlib
from pathlib import Path
import re
import sys

import numpy as np
import pytest
import tifffile
from PIL import Image

from core.analysis_v2 import input_manifest_checkpoint
from core.analysis_v2.head_measurement_service import (
    _prepare_measurement_channel_image,
    prepare_standardized_head_input,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROVENANCE_SOURCES = (
    "input_fingerprint.py",
    "input_manifest_checkpoint.py",
    "tail_core_result.py",
    "association_result.py",
    "tail_objects_revision_checkpoint.py",
    "tail_objects_revision.py",
)


def _spec_literal(spec_text, name):
    tree = ast.parse(spec_text)
    for node in tree.body:
        if isinstance(node, ast.Assign):
            if any(isinstance(target, ast.Name) and target.id == name
                   for target in node.targets):
                return ast.literal_eval(node.value)
    raise AssertionError("spec missing assignment: {}".format(name))


def _create_frozen_provenance_fixture(tmp_path):
    product = tmp_path / "product"
    internal_root = product / "_internal"
    internal = internal_root / "core" / "analysis_v2"
    internal.mkdir(parents=True)
    (product / "SpermProteinAnalyzer.exe").touch()
    source = PROJECT_ROOT / "core" / "analysis_v2"
    for name in PROVENANCE_SOURCES:
        (internal / name).write_bytes((source / name).read_bytes())
    return source, product, internal_root, internal


def _powershell_array(script_text, name):
    match = re.search(
        r"\$" + re.escape(name) + r"\s*=\s*@\((.*?)\n\)",
        script_text,
        flags=re.DOTALL,
    )
    assert match is not None, "build script missing array: {}".format(name)
    return tuple(re.findall(r"'([^']+)'", match.group(1)))


def _prepare(tmp_path, channel_path):
    labels = np.array(
        [[0, 1, 1], [0, 513, 65535]],
        dtype=np.uint16,
    )
    labels_path = tmp_path / "labels.tif"
    tifffile.imwrite(str(labels_path), labels)
    output = tmp_path / "measurement"
    prepare_standardized_head_input(
        [{
            "field_id": "xxx",
            "fitc": Path(channel_path),
            "tritc": Path(channel_path),
            "merge": Path(channel_path),
            "labels": labels_path,
            "objects": tmp_path / "objects.json",
            "expected_object_count": 2,
        }],
        output,
    )
    return output, labels, labels_path.read_bytes()


def _assert_true_tiff(path, expected):
    assert path.read_bytes()[:3] != b"\xff\xd8\xff"
    with Image.open(path) as image:
        assert image.format == "TIFF"
        actual = np.asarray(image)
    tifffile_array = tifffile.imread(str(path))
    assert actual.shape == expected.shape
    assert np.array_equal(actual, expected)
    assert np.array_equal(tifffile_array, expected)


def test_rgb_jpeg_is_decoded_and_reencoded_as_real_tiff(tmp_path):
    pixels = np.arange(9 * 11 * 3, dtype=np.uint8).reshape(9, 11, 3)
    source = tmp_path / "source.tif"
    Image.fromarray(pixels, mode="RGB").save(source, format="JPEG", quality=91)
    with Image.open(source) as image:
        image.load()
        assert image.format == "JPEG"
        decoded = np.asarray(image).copy()

    output, _, _ = _prepare(tmp_path, source)

    _assert_true_tiff(output / "xxx_G.tif", decoded)
    _assert_true_tiff(output / "xxx_R.tif", decoded)


def test_rgb_png_is_reencoded_as_real_tiff_without_pixel_change(tmp_path):
    pixels = np.arange(7 * 13 * 3, dtype=np.uint8).reshape(7, 13, 3)
    source = tmp_path / "source.png"
    Image.fromarray(pixels, mode="RGB").save(source, format="PNG")

    output, _, _ = _prepare(tmp_path, source)

    _assert_true_tiff(output / "xxx_G.tif", pixels)


def test_real_tiff_is_preserved_without_unnecessary_pixel_change(tmp_path):
    pixels = np.arange(8 * 10 * 3, dtype=np.uint8).reshape(8, 10, 3)
    source = tmp_path / "source.tif"
    tifffile.imwrite(str(source), pixels)
    source_bytes = source.read_bytes()

    output, _, _ = _prepare(tmp_path, source)

    target = output / "xxx_G.tif"
    _assert_true_tiff(target, pixels)
    assert target.read_bytes() == source_bytes


def test_jpeg_compressed_tiff_uses_formal_head_prepare_path(tmp_path):
    pixels = np.arange(18 * 24 * 3, dtype=np.uint8).reshape(18, 24, 3)
    source = tmp_path / "jpeg_compressed.tif"
    destination = tmp_path / "prepared.tif"
    tifffile.imwrite(str(source), pixels, compression="jpeg", photometric="rgb")

    with tifffile.TiffFile(str(source)) as tif:
        assert tif.pages[0].compression.name == "JPEG"

    expected = tifffile.imread(str(source))
    _prepare_measurement_channel_image(source, destination)
    actual = tifffile.imread(str(destination))

    assert destination.read_bytes() == source.read_bytes()
    assert np.array_equal(actual, expected)


def test_packaging_contract_collects_imagecodecs_and_runs_exe_smoke():
    project_root = PROJECT_ROOT
    requirements = (
        project_root / "packaging/windows/requirements-build.txt"
    ).read_text(encoding="utf-8")
    spec = (
        project_root / "packaging/windows/SpermProteinAnalyzer.spec"
    ).read_text(encoding="utf-8")
    build = (
        project_root / "packaging/windows/build.ps1"
    ).read_text(encoding="utf-8-sig")

    assert "imagecodecs==2023.3.16" in requirements
    assert 'collect_all(\n    "imagecodecs"\n)' in spec
    assert "imagecodecs_binaries" in spec
    assert '"imagecodecs",' in build
    assert "_jpeg8*.pyd" in build
    assert "--packaging-smoke-jpeg-tiff" in build
    assert "verify_batch_readiness.py" in build
    assert '(("protein1", "Q9BYW3"), ("protein3", "Q96P56"))' in build
    assert tuple(_spec_literal(spec, "provenance_sources")) == PROVENANCE_SOURCES
    assert _spec_literal(spec, "provenance_destination") == "core/analysis_v2"
    assert 'os.path.join(project_root, "core", "analysis_v2")' in spec
    assert (
        "(os.path.join(provenance_source_root, name), provenance_destination)"
        in spec
    )
    assert _powershell_array(build, "FrozenProvenanceResources") == tuple(
        "core\\analysis_v2\\{}".format(name) for name in PROVENANCE_SOURCES
    )
    assert "F:\\" not in spec
    assert "Join-Path $SourceRoot $relative" in build
    assert "Join-Path $InternalPath $relative" in build
    assert "missing frozen provenance resource:" in build
    assert "Frozen provenance resource SHA256 mismatch:" in build
    assert "input_manifest_checkpoint._producer_resources()" in build


def test_frozen_provenance_resources_are_opened_and_hashed(tmp_path, monkeypatch):
    source, product, internal_root, internal = _create_frozen_provenance_fixture(
        tmp_path
    )
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(internal_root), raising=False)
    monkeypatch.setattr(sys, "executable", str(product / "SpermProteinAnalyzer.exe"))
    monkeypatch.setattr(
        input_manifest_checkpoint,
        "__file__",
        str(internal / "input_manifest_checkpoint.py"),
    )

    resources = input_manifest_checkpoint._producer_resources()

    assert set(resources) == {
        "input_fingerprint.py", "input_manifest_checkpoint.py",
    }
    for name in PROVENANCE_SOURCES:
        source_bytes = (source / name).read_bytes()
        packaged_bytes = (internal / name).read_bytes()
        assert packaged_bytes == source_bytes
        assert hashlib.sha256(packaged_bytes).hexdigest() == hashlib.sha256(
            source_bytes
        ).hexdigest()


def test_frozen_provenance_missing_input_fingerprint_reproduces_old_bug(
    tmp_path, monkeypatch,
):
    _source, product, internal_root, internal = _create_frozen_provenance_fixture(
        tmp_path
    )
    (internal / "input_fingerprint.py").unlink()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(internal_root), raising=False)
    monkeypatch.setattr(sys, "executable", str(product / "SpermProteinAnalyzer.exe"))
    monkeypatch.setattr(
        input_manifest_checkpoint,
        "__file__",
        str(internal / "input_manifest_checkpoint.py"),
    )

    with pytest.raises(FileNotFoundError, match="input_fingerprint.py"):
        input_manifest_checkpoint._producer_resources()


def test_source_mode_producer_resources_remain_readable():
    resources = input_manifest_checkpoint._producer_resources()

    assert set(resources) == {
        "input_fingerprint.py", "input_manifest_checkpoint.py",
    }


def test_uint16_head_labels_are_copied_without_any_change(tmp_path):
    pixels = np.arange(6 * 8 * 3, dtype=np.uint8).reshape(6, 8, 3)
    source = tmp_path / "source.png"
    Image.fromarray(pixels, mode="RGB").save(source, format="PNG")

    output, labels, source_label_bytes = _prepare(tmp_path, source)

    target = output / "xxx_HeadFinalLabels.tif"
    actual = tifffile.imread(str(target))
    assert actual.dtype == np.uint16
    assert actual.ndim == 2
    assert np.array_equal(actual, labels)
    assert target.read_bytes() == source_label_bytes
