from pathlib import Path

import numpy as np
import tifffile
from PIL import Image

from core.analysis_v2.head_measurement_service import (
    _prepare_measurement_channel_image,
    prepare_standardized_head_input,
)


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
    project_root = Path(__file__).resolve().parents[1]
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
