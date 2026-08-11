from pathlib import Path

import numpy as np
import tifffile
from PIL import Image

from core.analysis_v2.tail_measurement_service import (
    prepare_standardized_tail_input,
)


def _prepare(tmp_path, channel_path):
    labels = {
        "head_labels": np.array([[0, 1, 1], [0, 513, 0]], dtype=np.uint16),
        "tail_labels": np.array([[0, 7, 7], [0, 4097, 0]], dtype=np.uint16),
        "positive_labels": np.array(
            [[0, 0, 23], [0, 65535, 0]],
            dtype=np.uint16,
        ),
    }
    label_paths = {}
    label_bytes = {}
    for key, array in labels.items():
        path = tmp_path / "{}.tif".format(key)
        tifffile.imwrite(str(path), array)
        label_paths[key] = path
        label_bytes[key] = path.read_bytes()

    output = tmp_path / "measurement"
    prepare_standardized_tail_input(
        [{
            "field_id": "xxx",
            "fitc": Path(channel_path),
            "tritc": Path(channel_path),
            "head_labels": label_paths["head_labels"],
            "tail_labels": label_paths["tail_labels"],
            "positive_labels": label_paths["positive_labels"],
            "objects": tmp_path / "objects.json",
            "expected_object_count": 2,
            "head_object_count": 2,
        }],
        output,
    )
    return output, labels, label_bytes


def _assert_true_tiff(path, expected):
    assert path.read_bytes()[:3] != b"\xff\xd8\xff"
    with Image.open(path) as image:
        assert image.format == "TIFF"
        actual = np.asarray(image)
    tifffile_array = tifffile.imread(str(path))
    assert actual.shape == expected.shape
    assert np.array_equal(actual, expected)
    assert np.array_equal(tifffile_array, expected)


def test_tail_rgb_jpeg_with_tif_suffix_is_reencoded_as_real_tiff(tmp_path):
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


def test_tail_png_is_reencoded_without_pixel_change(tmp_path):
    pixels = np.arange(7 * 13 * 3, dtype=np.uint8).reshape(7, 13, 3)
    source = tmp_path / "source.png"
    Image.fromarray(pixels, mode="RGB").save(source, format="PNG")

    output, _, _ = _prepare(tmp_path, source)

    _assert_true_tiff(output / "xxx_G.tif", pixels)
    _assert_true_tiff(output / "xxx_R.tif", pixels)


def test_tail_real_tiff_is_preserved_without_unnecessary_change(tmp_path):
    pixels = np.arange(8 * 10 * 3, dtype=np.uint8).reshape(8, 10, 3)
    source = tmp_path / "source.tif"
    tifffile.imwrite(str(source), pixels)
    source_bytes = source.read_bytes()

    output, _, _ = _prepare(tmp_path, source)

    for name in ("xxx_G.tif", "xxx_R.tif"):
        target = output / name
        _assert_true_tiff(target, pixels)
        assert target.read_bytes() == source_bytes


def test_all_tail_uint16_label_inputs_are_copied_without_change(tmp_path):
    pixels = np.arange(6 * 8 * 3, dtype=np.uint8).reshape(6, 8, 3)
    source = tmp_path / "source.png"
    Image.fromarray(pixels, mode="RGB").save(source, format="PNG")

    output, labels, label_bytes = _prepare(tmp_path, source)

    destinations = {
        "head_labels": "xxx_HeadFinalLabels.tif",
        "tail_labels": "xxx_TailFinalLabels.tif",
        "positive_labels": "xxx_TailPositiveHeadLabels.tif",
    }
    for key, name in destinations.items():
        target = output / name
        actual = tifffile.imread(str(target))
        assert actual.dtype == np.uint16
        assert actual.ndim == 2
        assert np.array_equal(actual, labels[key])
        assert target.read_bytes() == label_bytes[key]
