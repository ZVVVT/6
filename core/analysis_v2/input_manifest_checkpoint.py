"""Formal protein3-tail Input Manifest checkpoint writing for Tail V3 Phase 1C."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

import tifffile
from PIL import Image

from .checkpoint_store import CheckpointStore
from .environment_snapshot import sha256_file
from .input_fingerprint import (
    INPUT_MANIFEST_SCHEMA_VERSION, INPUT_ROLES, STAGE_INPUT_MANIFEST,
    build_input_manifest, canonical_json_bytes, describe_input_file, fingerprint,
    parameter_fingerprint, producer_fingerprint, stage_input_fingerprint,
    stage_input_projection, validate_input_manifest,
)


CHECKPOINT_ID = "input_manifest"
PAYLOAD_NAME = "input_manifest.json"
ALGORITHM_VERSION = "tail-v3-input-manifest-checkpoint-v1"


class InputManifestCheckpointError(RuntimeError):
    """A formal input identity could not be committed and verified."""


def _image_metadata(path: Path) -> Dict[str, Any]:
    """Read only image headers; never copy or decode the original image payload."""
    with Image.open(str(path)) as image:
        width, height = image.size
        image_format = str(image.format or "").upper()
    result = {"width": int(width), "height": int(height)}
    if image_format == "TIFF":
        with tifffile.TiffFile(str(path)) as image:
            series = image.series[0]
            result["shape"] = [int(value) for value in series.shape]
            result["dtype"] = str(series.dtype)
    return result


def _producer_resources() -> Dict[str, str]:
    root = Path(__file__).resolve().parent
    return {
        "input_fingerprint.py": sha256_file(root / "input_fingerprint.py"),
        "input_manifest_checkpoint.py": sha256_file(
            root / "input_manifest_checkpoint.py"
        ),
    }


def _manifest_for_field(field: Mapping[str, Any], protein_key: str) -> Dict[str, Any]:
    field_id = str(field["field_id"])
    paths = {
        "FITC": Path(field["fitc_path"]),
        "TRITC": Path(field["tritc_path"]),
        "Merge": Path(field["merge_path"]),
    }
    metadata = dict((role, _image_metadata(path)) for role, path in paths.items())
    dimensions = set((item["width"], item["height"]) for item in metadata.values())
    if len(dimensions) != 1:
        raise ValueError("field {} 的 FITC/TRITC/Merge 图像尺寸不一致".format(field_id))
    width, height = dimensions.pop()
    records = []
    for role in INPUT_ROLES:
        item = metadata[role]
        records.append(describe_input_file(
            role, paths[role],
            logical_reference="formal_input/{}/{}".format(field_id, role),
            shape=item.get("shape"), dtype=item.get("dtype"),
        ))
    return build_input_manifest(field_id, protein_key, "tail", width, height, records)


def write_and_verify_input_manifest_checkpoint(
        task_root: Path, field: Mapping[str, Any], protein_key: str,
) -> Dict[str, Any]:
    """Create one immutable generation then immediately read and verify it."""
    started = time.perf_counter()
    field_id = str(field.get("field_id") or "")
    checkpoint_root = Path(task_root) / "checkpoints" / "input" / field_id
    try:
        hash_started = time.perf_counter()
        input_manifest = _manifest_for_field(field, protein_key)
        input_fingerprint = stage_input_fingerprint(
            input_manifest, STAGE_INPUT_MANIFEST
        )
        parameter = parameter_fingerprint(STAGE_INPUT_MANIFEST, {
            "input_manifest_schema_version": INPUT_MANIFEST_SCHEMA_VERSION,
            "required_roles": list(INPUT_ROLES),
            "logical_reference_scheme": "formal_input/{field_id}/{role}",
        })
        producer = producer_fingerprint(
            STAGE_INPUT_MANIFEST, ALGORITHM_VERSION, resources=_producer_resources()
        )
        hash_seconds = time.perf_counter() - hash_started
        store = CheckpointStore(checkpoint_root)
        write_started = time.perf_counter()
        attempt = store.begin(
            CHECKPOINT_ID, STAGE_INPUT_MANIFEST, field_id, input_fingerprint,
            parameter, producer,
        )
        attempt.add_json("input_manifest", PAYLOAD_NAME, input_manifest)
        generation = attempt.commit()
        write_seconds = time.perf_counter() - write_started
        validate_started = time.perf_counter()
        expected = {
            "stage": STAGE_INPUT_MANIFEST,
            "field_id": field_id,
            "input_fingerprint": input_fingerprint,
            "parameter_fingerprint": parameter,
            "producer_fingerprint": producer,
        }
        checkpoint_manifest = store.load_checkpoint(generation, expected=expected)
        payload = json.loads((generation / PAYLOAD_NAME).read_text(encoding="utf-8"))
        validate_input_manifest(payload)
        if canonical_json_bytes(payload) != canonical_json_bytes(input_manifest):
            raise ValueError("reader 返回的 input_manifest 与本次正式输入不一致")
        validate_seconds = time.perf_counter() - validate_started
        return {
            "field_id": field_id,
            "checkpoint_root": str(checkpoint_root),
            "generation": str(generation),
            "checkpoint_manifest": checkpoint_manifest,
            "input_manifest": payload,
            "input_fingerprint": input_fingerprint,
            "projections": {
                "head": stage_input_projection(payload, "head"),
                "tail_core": stage_input_projection(payload, "tail_core"),
                "association": stage_input_projection(payload, "association"),
            },
            "hash_seconds": hash_seconds,
            "write_seconds": write_seconds,
            "validate_seconds": validate_seconds,
            "input_checkpoint_seconds": time.perf_counter() - started,
        }
    except Exception as error:
        raise InputManifestCheckpointError(
            "field={} stage=input_manifest checkpoint_root={} cause={}".format(
                field_id, checkpoint_root, error
            )
        ) from error
