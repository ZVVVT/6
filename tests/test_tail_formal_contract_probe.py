import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import tifffile

from experiments.tail_formal_contract_probe import build_candidate


class TailFormalContractProbeTests(unittest.TestCase):
    def test_unresolved_tail_is_measured_without_fabricated_head(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workset = np.asarray([[1, 1, 0], [0, 2, 2]], dtype=np.uint16)
            heads = np.asarray([[0, 7, 0], [0, 0, 0]], dtype=np.uint16)
            fitc = np.asarray([[10, 20, 0], [0, 30, 50]], dtype=np.uint8)
            tifffile.imwrite(str(root / "TailWorksetLabels.tif"), workset)
            tifffile.imwrite(str(root / "heads.tif"), heads)
            tifffile.imwrite(str(root / "fitc.tif"), fitc)
            data = {"objects": [
                {"tail_object_id": 1, "workset_label_id": 1, "accepted": True,
                 "association_status": "associated", "head_label_id": 7},
                {"tail_object_id": 2, "workset_label_id": 2, "accepted": True,
                 "association_status": "unresolved", "head_label_id": None},
            ]}
            (root / "tail_workset.json").write_text(
                json.dumps(data), encoding="utf-8"
            )
            result = build_candidate(
                root / "TailWorksetLabels.tif", root / "tail_workset.json",
                root / "heads.tif", root / "fitc.tif", root / "out"
            )
            positive = tifffile.imread(str(root / "out/probe_TailPositiveHeadLabels.tif"))
            unresolved = result["objects"][1]
            self.assertEqual(set(np.unique(positive)), {0, 1})
            self.assertIsNone(unresolved["head_label_id"])
            self.assertEqual(unresolved["probe_tail_mask_mean_intensity255"], 40.0)
            self.assertTrue(result["not_for_measurement"])
            self.assertTrue(result["not_for_publication"])


if __name__ == "__main__":
    unittest.main()
