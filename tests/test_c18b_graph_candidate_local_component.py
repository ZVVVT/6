"""Tight component crops preserve the frozen C18B graph-candidate contract."""

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import cv2
import numpy as np


C18B = (Path(__file__).resolve().parents[1] / "tools" / "analysis_v2" /
        "c18b_score015")
sys.path.insert(0, str(C18B))

import candidate_validation as validation
from candidate_scoring import candidate_path, path_length
from fitc_processing import enhanced_mask, read_fitc, skeleton
from tail_graph_experiment import (
    branch_cut_mask, candidate_groups, connection_candidates, crossing_number,
    ordered_segment, select_links,
)


def _paths(labels, stats, count, local):
    paths = []
    crops = []
    for component_id in range(1, count):
        if local:
            left = int(stats[component_id, cv2.CC_STAT_LEFT])
            top = int(stats[component_id, cv2.CC_STAT_TOP])
            width = int(stats[component_id, cv2.CC_STAT_WIDTH])
            height = int(stats[component_id, cv2.CC_STAT_HEIGHT])
            component = labels[top:top + height, left:left + width] == component_id
            local_path = ordered_segment(component)
            path = [(x + left, y + top) for x, y in local_path]
            crops.append(component)
        else:
            path = ordered_segment(labels == component_id)
        paths.append(path)
    return paths, crops


def _connected(mask):
    return cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)


def _downstream(paths, mask, fitc):
    proposals = connection_candidates(paths, 4, 5, .12)
    links = select_links(proposals, len(paths))
    groups = candidate_groups(len(paths), links)
    distance = cv2.distanceTransform(mask.astype(np.uint8), cv2.DIST_L2, 5)
    rows = []
    polylines = []
    for candidate_id, group in enumerate(groups, 1):
        points = candidate_path(group, paths, links, mode="ordered")
        polyline = np.asarray(points, np.int32)
        group_links = [edge for edge in links
                       if edge.a in group and edge.b in group]
        if group_links:
            local_scores = np.asarray([
                validation.connection_scores(edge, paths, fitc, mask, distance)
                for edge in group_links
            ])
            scores = .8 * local_scores.min(axis=0) + .2 * local_scores.mean(axis=0)
        else:
            xy = polyline.astype(int)
            values = fitc[xy[:, 1], xy[:, 0]]
            intensity = float(np.percentile(values, 10) /
                              max(np.median(values), 1e-6))
            widths = 2.0 * distance[xy[:, 1], xy[:, 0]]
            positive = widths[widths > 0]
            width = validation.ratio_score(np.percentile(positive, 25),
                                           np.percentile(positive, 75))
            scores = np.asarray([np.clip(intensity, 0, 1), width, 1.0])
        intensity_score, width_score, curvature_score = np.clip(scores, 0, 1)
        final = float((intensity_score * width_score * curvature_score) ** (1 / 3))
        if validation.FINAL_SCORE_THRESHOLD <= final < .20:
            final = .20
        rows.append({
            "candidate_id": candidate_id,
            "length": path_length(points),
            "intensity_score": float(intensity_score),
            "width_score": float(width_score),
            "curvature_score": float(curvature_score),
            "final_score": final,
        })
        polylines.append(polyline)
    return proposals, links, groups, rows, polylines


class TightComponentPathTests(unittest.TestCase):
    def test_connected_component_shapes_and_all_image_borders_are_exact(self):
        cases = {}

        cases["single_pixel"] = np.pad(np.ones((1, 1), bool), ((3, 4), (4, 5)))
        short_line = np.zeros((9, 11), bool)
        short_line[4, 3:8] = True
        cases["short_line"] = short_line
        branch = np.zeros((9, 11), bool)
        branch[2:7, 5] = True
        branch[4, 3:8] = True
        cases["branch"] = branch
        loop = np.zeros((9, 11), bool)
        loop[2, 3:8] = loop[6, 3:8] = True
        loop[2:7, 3] = loop[2:7, 7] = True
        cases["loop_hole"] = loop
        touching_bbox = np.zeros((9, 11), bool)
        touching_bbox[2, 3:8] = True
        touching_bbox[2:7, 3] = True
        touching_bbox[6, 3:8] = True
        cases["touching_bbox"] = touching_bbox
        top = np.zeros((9, 11), bool); top[0, 2:7] = True
        bottom = np.zeros((9, 11), bool); bottom[-1, 2:7] = True
        left = np.zeros((9, 11), bool); left[2:7, 0] = True
        right = np.zeros((9, 11), bool); right[2:7, -1] = True
        cases.update(top_border=top, bottom_border=bottom,
                     left_border=left, right_border=right)

        for name, mask in cases.items():
            with self.subTest(name=name):
                count, labels, stats, _ = _connected(mask)
                self.assertEqual(count, 2)
                old_paths, _ = _paths(labels, stats, count, False)
                new_paths, crops = _paths(labels, stats, count, True)
                self.assertEqual(new_paths, old_paths)
                self.assertEqual(int(stats[1, cv2.CC_STAT_AREA]),
                                 int(crops[0].sum()))
                self.assertEqual(crops[0].shape,
                                 (int(stats[1, cv2.CC_STAT_HEIGHT]),
                                  int(stats[1, cv2.CC_STAT_WIDTH])))

    def test_adjacent_different_labels_inside_tight_bbox_are_excluded(self):
        labels = np.zeros((7, 8), np.int32)
        labels[1, 1:6] = 1
        labels[1:6, 1] = 1
        labels[5, 1:6] = 1
        labels[2:5, 2] = 2
        stats = np.zeros((3, 5), np.int32)
        stats[1] = (1, 1, 5, 5, int(np.count_nonzero(labels == 1)))
        stats[2] = (2, 2, 1, 3, int(np.count_nonzero(labels == 2)))

        old_paths, _ = _paths(labels, stats, 3, False)
        new_paths, crops = _paths(labels, stats, 3, True)
        self.assertEqual(new_paths, old_paths)
        self.assertTrue(np.any(labels[1:6, 1:6] == 2))
        self.assertFalse(np.any(crops[0] & (labels[1:6, 1:6] == 2)))

    def test_downstream_graph_rows_and_candidate_polylines_are_exact(self):
        skeleton = np.zeros((15, 24), bool)
        skeleton[7, 1:6] = True
        skeleton[7, 8:13] = True
        skeleton[7, 15:20] = True
        count, labels, stats, _ = _connected(skeleton)
        old_paths, _ = _paths(labels, stats, count, False)
        new_paths, _ = _paths(labels, stats, count, True)
        mask = cv2.dilate(skeleton.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
        yy, xx = np.indices(skeleton.shape)
        fitc = (50 + yy * 3 + xx).astype(np.float32)

        old = _downstream(old_paths, mask, fitc)
        new = _downstream(new_paths, mask, fitc)
        self.assertEqual(new_paths, old_paths)
        self.assertEqual(new[0], old[0])
        self.assertEqual(new[1], old[1])
        self.assertEqual(new[2], old[2])
        self.assertEqual(new[3], old[3])
        self.assertEqual(len(new[4]), len(old[4]))
        for new_polyline, old_polyline in zip(new[4], old[4]):
            self.assertTrue(np.array_equal(new_polyline, old_polyline))

    def test_formal_reconstruct_passes_only_tight_masks_to_ordered_segment(self):
        skeleton = np.zeros((15, 24), np.uint8)
        skeleton[7, 1:6] = 255
        skeleton[7, 8:13] = 255
        skeleton[7, 15:20] = 255
        mask = cv2.dilate(skeleton, np.ones((5, 5), np.uint8))
        yy, xx = np.indices(skeleton.shape)
        fitc = (50 + yy * 3 + xx).astype(np.float32)
        count, labels, stats, _ = _connected(skeleton > 0)
        old_paths, _ = _paths(labels, stats, count, False)
        expected = _downstream(old_paths, mask > 0, fitc)
        received_shapes = []
        real_ordered_segment = validation.ordered_segment

        def captured(component):
            received_shapes.append(component.shape)
            return real_ordered_segment(component)

        with tempfile.TemporaryDirectory(prefix="c18b_local_component_") as temp_name:
            temp = Path(temp_name)
            mask_path = temp / "mask.png"
            skeleton_path = temp / "skeleton.png"
            self.assertTrue(cv2.imwrite(str(mask_path), mask))
            self.assertTrue(cv2.imwrite(str(skeleton_path), skeleton))
            args = argparse.Namespace(
                mask=mask_path, skeleton=skeleton_path,
                branch_cut_mode="dilate3", min_segment_length=1,
                min_candidate_length=1, max_gap=4, max_angle=5,
                max_curvature_delta=.12, candidate_path_mode="ordered",
            )
            with mock.patch.object(validation, "ordered_segment", captured):
                rows, polylines = validation.reconstruct(args, fitc)

        expected_shapes = [
            (int(stats[index, cv2.CC_STAT_HEIGHT]),
             int(stats[index, cv2.CC_STAT_WIDTH]))
            for index in range(1, count)
        ]
        self.assertEqual(received_shapes, expected_shapes)
        self.assertTrue(all(shape != skeleton.shape for shape in received_shapes))
        self.assertEqual(rows, expected[3])
        self.assertEqual(len(polylines), len(expected[4]))
        for polyline, expected_polyline in zip(polylines, expected[4]):
            self.assertTrue(np.array_equal(polyline, expected_polyline))

    @unittest.skipUnless(os.environ.get("C18B_C1_REAL_GATE") == "1",
                         "explicit three-field component comparator")
    def test_all_real_field_components_and_downstream_are_exact(self):
        root = C18B.parents[2]
        fields = (
            ("023", "CASE20260908102941", "20260908_103450_acad3c", 387),
            ("016", "CASE20260908104300", "20260908_104320_f4c8fc", 1020),
            ("022", "CASE20260908104656", "20260908_104716_af7f2c", 626),
        )
        config = json.loads((C18B / "config" / "frozen_parameters.json").read_text(
            encoding="utf-8"))
        graph = config["graph"]
        for field, case, run, expected_components in fields:
            with self.subTest(field=field):
                field_id = "ZBFY{}-C-1".format(field)
                green = (root / "workspace" / "cases" / case / "analysis_v2" /
                         "protein3" / "runs" / run / "input" /
                         (field_id + "_FITC.tif"))
                _, green8 = read_fitc(green)
                enhanced = enhanced_mask(green8)
                skel = skeleton(enhanced)
                branch = skel & (crossing_number(skel) >= 3)
                cut = branch_cut_mask(branch, graph.get("branch_cut_mode", "dilate3"))
                count, labels, stats, _ = cv2.connectedComponentsWithStats(
                    (skel & ~cut).astype(np.uint8), connectivity=8)
                retained_ids = [
                    index for index in range(1, count)
                    if stats[index, cv2.CC_STAT_AREA] >= graph["min_segment_length"]
                ]
                self.assertEqual(len(retained_ids), expected_components)
                old_paths = [ordered_segment(labels == index)
                             for index in retained_ids]
                new_paths = []
                for index in retained_ids:
                    left = int(stats[index, cv2.CC_STAT_LEFT])
                    top = int(stats[index, cv2.CC_STAT_TOP])
                    width = int(stats[index, cv2.CC_STAT_WIDTH])
                    height = int(stats[index, cv2.CC_STAT_HEIGHT])
                    component = labels[top:top + height,
                                       left:left + width] == index
                    local_path = ordered_segment(component)
                    new_paths.append([(x + left, y + top)
                                      for x, y in local_path])
                ordered_mismatch = sum(old != new
                                       for old, new in zip(old_paths, new_paths))
                self.assertEqual(ordered_mismatch, 0)
                old_proposals = connection_candidates(
                    old_paths, graph["max_gap"], graph["max_angle"],
                    graph["max_curvature_delta"])
                new_proposals = connection_candidates(
                    new_paths, graph["max_gap"], graph["max_angle"],
                    graph["max_curvature_delta"])
                old_links = select_links(old_proposals, len(old_paths))
                new_links = select_links(new_proposals, len(new_paths))
                old_groups = candidate_groups(len(old_paths), old_links)
                new_groups = candidate_groups(len(new_paths), new_links)
                downstream_mismatch = int(
                    old_proposals != new_proposals or
                    old_links != new_links or
                    old_groups != new_groups
                )
                self.assertEqual(downstream_mismatch, 0)
                print("C1 component exact field={} components={} ordered_mismatch={} "
                      "downstream_mismatch={}".format(
                          field, len(retained_ids), ordered_mismatch,
                          downstream_mismatch))


if __name__ == "__main__":
    unittest.main()
