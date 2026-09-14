"""Exact pre-P3A-3 grow oracle and priority lower-bound safety checks."""
import heapq
import hashlib
import importlib
import json
import sys
import tempfile
import warnings
from pathlib import Path

import numpy as np
import pytest


C18B_DIR = Path(__file__).resolve().parents[1] / "tools" / "analysis_v2" / "c18b_score015"
sys.path.insert(0, str(C18B_DIR))
try:
    region = importlib.import_module("graph_seeded_region_growing")
finally:
    sys.path.remove(str(C18B_DIR))


def old_grow(fitc, groups, max_distance, intensity_weight, direction_weight,
             decisions=None):
    """Frozen implementation before the lower-bound change."""
    labels,tx,ty=region.seed_geometry(groups,fitc.shape)
    seed_mask=labels>0
    seed_values=[]; thresholds=np.zeros(len(groups)+1,np.float32); levels=np.ones(len(groups)+1,np.float32)
    for iid in range(1,len(groups)+1):
        vals=fitc[labels==iid]; seed_values.extend(vals.tolist())
        levels[iid]=max(float(np.median(vals)),1.)
        thresholds[iid]=max(6.,float(np.percentile(vals,10))*.42)
    cost=np.full(fitc.shape,np.inf,np.float32); dist=np.full(fitc.shape,np.inf,np.float32)
    heap=[]
    ys,xs=np.where(seed_mask)
    for y,x in zip(ys,xs):
        cost[y,x]=0.; dist[y,x]=0.; heapq.heappush(heap,(0.,int(labels[y,x]),int(y),int(x)))
    while heap:
        d,iid,y,x=heapq.heappop(heap)
        if iid!=labels[y,x] or d>float(cost[y,x])+1e-5: continue
        for dx,dy,step in region.N8:
            xx,yy=x+dx,y+dy
            if xx<0 or yy<0 or xx>=fitc.shape[1] or yy>=fitc.shape[0]: continue
            if seed_mask[yy,xx] and labels[yy,xx]!=iid: continue
            ndist=float(dist[y,x])+step
            if ndist>max_distance or fitc[yy,xx]<thresholds[iid]: continue
            if decisions is not None:
                decisions["candidates"]+=1
                early=bool(d+step+1e-5>=cost[yy,xx])
                decisions["early"]+=int(early)
            jump=abs(float(fitc[yy,xx]-fitc[y,x]))/levels[iid]
            deficit=max(0.,float(levels[iid]-fitc[yy,xx]))/levels[iid]
            align=abs((dx*float(tx[y,x])+dy*float(ty[y,x]))/step)
            nd=d+step*(1.+intensity_weight*(jump+.35*deficit)+direction_weight*align)
            improves=bool(nd+1e-5<cost[yy,xx])
            if decisions is not None:
                false_reject=early and improves
                decisions["false_reject"]+=int(false_reject)
                decisions["mismatch"]+=int(improves != (not early and improves))
            if improves:
                cost[yy,xx]=nd; dist[yy,xx]=ndist; labels[yy,xx]=iid
                tx[yy,xx]=tx[y,x]; ty[yy,xx]=ty[y,x]
                heapq.heappush(heap,(nd,iid,yy,xx))
    return labels,thresholds,cost,dist,tx,ty


def run_new(fitc, groups, max_distance=8., intensity_weight=3., direction_weight=.8):
    """Capture local result arrays without changing the public return contract."""
    captured = {}
    def trace(frame, event, arg):
        if frame.f_code is region.grow.__code__ and event == "return":
            captured.update({key: frame.f_locals[key].copy()
                             for key in ("cost", "dist", "tx", "ty")})
            captured["eligible"] = frame.f_locals["use_priority_lower_bound"]
        return trace
    previous = sys.gettrace()
    sys.settrace(trace)
    try:
        labels, thresholds = region.grow(
            fitc, groups, max_distance, intensity_weight, direction_weight)
    finally:
        sys.settrace(previous)
    return (labels, thresholds, captured["cost"], captured["dist"],
            captured["tx"], captured["ty"]), captured["eligible"]


def sample_groups():
    return [[np.asarray([[2, 2], [3, 2]], np.int32)]]


@pytest.mark.parametrize("array,iw,dw,expected", [
    (np.full((5, 6), 75, np.float32), 3., .8, True),
    (np.asarray([[0, 150], [75, 100]], np.float32), 3., .8, True),
    (np.asarray([[np.nan, 75]], np.float32), 3., .8, False),
    (np.asarray([[np.inf, 75]], np.float32), 3., .8, False),
    (np.asarray([[-np.inf, 75]], np.float32), 3., .8, False),
    (np.asarray([[np.finfo(np.float32).max, 75]], np.float32), 3., .8, False),
    (np.asarray([[-np.finfo(np.float32).max, 75]], np.float32), 3., .8, False),
    (np.full((5, 6), 75, np.float32), -1., .8, False),
    (np.full((5, 6), 75, np.float32), 3., -1., False),
    (np.full((5, 6), 75, np.float32), np.nan, .8, False),
    (np.full((5, 6), 75, np.float32), np.inf, .8, False),
    (np.full((5, 6), 75, np.float32), 3., np.nan, False),
    (np.full((5, 6), 75, np.float32), 3., np.inf, False),
    (np.empty((0, 0), np.float32), 3., .8, False),
    (np.empty((0,), np.float32), 3., .8, False),
])
def test_eligibility(array, iw, dw, expected):
    # A trace at the first existing statement reads the call-level decision,
    # even for shapes that the original grow subsequently rejects.
    observed = []
    def trace(frame, event, arg):
        if (frame.f_code is region.grow.__code__ and event == "line"
                and "use_priority_lower_bound" in frame.f_locals):
            observed.append(frame.f_locals["use_priority_lower_bound"])
        return trace
    previous = sys.gettrace()
    sys.settrace(trace)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            try:
                region.grow(array, [], 8., iw, dw)
            except (IndexError, ValueError, TypeError):
                pass
    finally:
        sys.settrace(previous)
    assert observed
    assert observed[-1] is expected


@pytest.mark.parametrize("value,iw,dw", [
    (np.nan, 3., .8), (np.inf, 3., .8), (-np.inf, 3., .8),
    (np.finfo(np.float32).max, 3., .8),
    (-np.finfo(np.float32).max, 3., .8),
    (75., -1., .8), (75., 3., -1.),
    (75., np.nan, .8), (75., np.inf, .8),
    (75., 3., np.nan), (75., 3., np.inf),
])
def test_unsafe_old_path_exact(value, iw, dw):
    fitc = np.full((7, 8), 75, np.float32)
    fitc[0, 0] = value
    groups = sample_groups()
    with warnings.catch_warnings(record=True) as old_warnings:
        warnings.simplefilter("always")
        old = old_grow(fitc, groups, 8., iw, dw)
    with warnings.catch_warnings(record=True) as new_warnings:
        warnings.simplefilter("always")
        new, eligible = run_new(fitc, groups, 8., iw, dw)
    assert eligible is False
    for expected, actual in zip(old, new):
        np.testing.assert_array_equal(actual, expected)
    assert [w.category for w in new_warnings] == [w.category for w in old_warnings]
    assert [str(w.message) for w in new_warnings] == [str(w.message) for w in old_warnings]


@pytest.mark.parametrize("weight", [1 + 0j, "invalid"])
def test_unsafe_exception_type_message_and_warnings_exact(weight):
    fitc = np.full((7, 8), 75, np.float32)
    groups = sample_groups()
    def outcome(function):
        with warnings.catch_warnings(record=True) as seen:
            warnings.simplefilter("always")
            try:
                function(fitc, groups, 8., weight, .8)
            except Exception as error:
                return (type(error), str(error),
                        [(item.category, str(item.message)) for item in seen])
        return None
    assert outcome(region.grow) == outcome(old_grow)


def test_safe_grow_exact_on_crossing_and_zero_penalties():
    rng = np.random.RandomState(6024)
    fitc = rng.randint(0, 151, (19, 23)).astype(np.float32)
    groups = [
        [np.asarray([[2, 2], [3, 3], [4, 4], [5, 5]], np.int32)],
        [np.asarray([[17, 2], [16, 3], [15, 4], [14, 5]], np.int32)],
    ]
    for iw, dw in ((3., .8), (0., 0.), (1e-12, 1e-12)):
        old = old_grow(fitc, groups, 10., iw, dw)
        new, eligible = run_new(fitc, groups, 10., iw, dw)
        assert eligible is True
        for expected, actual in zip(old, new):
            np.testing.assert_array_equal(actual, expected)


@pytest.mark.parametrize("relation", ["below", "equal", "above"])
def test_lower_bound_comparison_boundary(relation):
    d = -1e-5
    step = region.N8[3][2]
    lower = d + step
    boundary = lower + 1e-5
    cost = np.float32(1.)
    assert boundary == float(cost)
    if relation == "below":
        cost = np.nextafter(cost, np.float32(np.inf))
    elif relation == "above":
        cost = np.nextafter(cost, np.float32(-np.inf))
    assert bool(lower + 1e-5 >= cost) is (relation != "below")
    for extra in (0., 1e-12):
        nd = d + step * (1. + extra)
        if lower + 1e-5 >= cost:
            assert not (nd + 1e-5 < cost)


def formal_decision_probe(field):
    """Compare every frozen candidate decision in the formal C18B runtime."""
    cases = {
        "023": ("CASE20260908102941", "20260908_103450_acad3c"),
        "022": ("CASE20260908104656", "20260908_104716_af7f2c"),
    }
    case, run = cases[field]
    root = Path(__file__).resolve().parents[1]
    field_id = "ZBFY{}-C-1".format(field)
    run_root = (root / "workspace" / "cases" / case / "analysis_v2" /
                "protein3" / "runs" / run)
    input_path = run_root / "input" / (field_id + "_FITC.tif")
    gold = (run_root / "segmentation" / "c18b_score015" /
            field_id / (field_id + "_FITC"))
    sys.path.insert(0, str(C18B_DIR))
    try:
        pipeline = importlib.import_module("run_pipeline")
        filter_module = importlib.import_module("extreme_fragment_filter")
    finally:
        sys.path.remove(str(C18B_DIR))
    cfg = json.loads((C18B_DIR / "config" / "frozen_parameters.json").read_text(
        encoding="utf-8"))
    decisions = {key: 0 for key in ("candidates", "early", "mismatch", "false_reject")}
    original = pipeline.grow
    def compared_grow(fitc, groups, max_distance, intensity_weight, direction_weight):
        expected = old_grow(fitc, groups, max_distance, intensity_weight,
                            direction_weight, decisions)
        actual = original(fitc, groups, max_distance, intensity_weight,
                          direction_weight)
        np.testing.assert_array_equal(actual[0], expected[0])
        np.testing.assert_array_equal(actual[1], expected[1])
        return actual
    pipeline.grow = compared_grow
    output_root = Path(tempfile.mkdtemp(prefix="p3a3_decision_{}_".format(field),
                                        dir=str(root / "workspace")))
    try:
        output, stats = pipeline.run_one(input_path, output_root, cfg,
                                         candidate_path_mode="graph_preserving")
    finally:
        pipeline.grow = original
    filter_module.apply_extreme_fragment_filter(output)
    for name in ("06_final_tail_instances.tif", "07_extreme_fragment_filtered_labels.tif"):
        assert hashlib.sha256((output / name).read_bytes()).digest() == hashlib.sha256(
            (gold / name).read_bytes()).digest()
    assert decisions["mismatch"] == decisions["false_reject"] == 0
    return {"field": field, "decisions": decisions, "stats": stats,
            "output": str(output)}
