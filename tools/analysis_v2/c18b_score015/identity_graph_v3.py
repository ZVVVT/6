"""Frozen identity_graph_v3 clustering and graph-constrained reconstruction."""
from __future__ import annotations

from collections import defaultdict

import networkx as nx
import numpy as np

from graph_constrained_instance_separation import fragment_pair_features, _path_geometry


def node_curvature(path, window=8):
    if len(path) < 2 * window + 1:
        return 0.0
    p, values = path.astype(float), []
    for i in range(window, len(p) - window, window):
        a, b = p[i] - p[i-window], p[i+window] - p[i]
        denom = max(float(np.linalg.norm(a) * np.linalg.norm(b)), 1e-9)
        values.append(np.degrees(np.arccos(np.clip(np.dot(a, b) / denom, -1, 1))))
    return float(np.mean(values)) if values else 0.0


def graph_data(groups, fitc):
    nodes, paths = [], {}
    for parent, group in enumerate(groups, 1):
        for fragment, path in enumerate(group, 1):
            name = f"P{parent}:F{fragment}"
            nodes.append({"fragment_id": name, "curvature": node_curvature(path)})
            paths[name] = path
    edges = []
    for i, a in enumerate(nodes):
        for b in nodes[i + 1:]:
            features = fragment_pair_features(paths[a["fragment_id"]], paths[b["fragment_id"]], fitc)
            endpoint = float(features["endpoint_min_distance"])
            spatial = float(features["minimum_spatial_distance"])
            bridge = float(np.clip(features["bridge_FITC_support"], 0, 1))
            background = float(np.clip(features["bridge_background_ratio"], 0, 1))
            direction = float(features["main_direction_difference"])
            tangent = float(features["endpoint_tangent_angle"])
            curvature = float(np.exp(-abs(a["curvature"] - b["curvature"]) / 25))
            weight = (.22*np.exp(-endpoint/75) + .13*np.exp(-spatial/35) + .18*bridge +
                      .10*(1-background) + .12*max(0, 1-direction/90) +
                      .15*max(0, 1-tangent/180) + .10*curvature)
            edges.append((a["fragment_id"], b["fragment_id"], float(weight)))
    return nodes, edges


def cluster(nodes, edges, resolution, seed):
    graph = nx.Graph()
    graph.add_nodes_from(n["fragment_id"] for n in nodes)
    graph.add_weighted_edges_from(edges)
    communities = nx.community.louvain_communities(
        graph, weight="weight", resolution=resolution, seed=seed)
    groups = sorted((sorted(c) for c in communities), key=lambda c: c[0])
    return {node: cid for cid, group in enumerate(groups, 1) for node in group}, groups


def _streaming_argmin(cost_maps):
    """Return the first minimum index per pixel without stacking cost maps."""
    iterator = iter(cost_maps)
    try:
        best_cost = next(iterator).copy()
    except StopIteration:
        raise ValueError("at least one cost map is required")
    winner = np.zeros(best_cost.shape, dtype=np.intp)
    for index, cost in enumerate(iterator, 1):
        better = cost < best_cost
        best_cost[better] = cost[better]
        winner[better] = index
    return winner


def reconstruct(grown, fitc, groups, membership, intensity_weight, direction_weight):
    """Compete identity communities within each grown parent region."""
    final = np.zeros_like(grown, np.uint16)
    coordinate_grid = np.indices(fitc.shape, dtype=np.float32)
    for parent_id, group in enumerate(groups, 1):
        region = grown == parent_id
        by_community = defaultdict(list)
        for fragment, path in enumerate(group, 1):
            by_community[membership[f"P{parent_id}:F{fragment}"]].append(path)
        ids = sorted(by_community)
        if len(ids) == 1:
            final[region] = ids[0]
            continue
        def community_costs():
            for cid in ids:
                geoms = [
                    _path_geometry(path, fitc.shape, coordinate_grid)
                    for path in by_community[cid]
                ]
                distances = np.stack([g[0] for g in geoms])
                nearest = np.argmin(distances, axis=0)
                distance = np.min(distances, axis=0)
                direction = np.take_along_axis(
                    np.stack([g[1] for g in geoms]), nearest[None], axis=0)[0]
                seed_mask = np.maximum.reduce([g[2] for g in geoms]) > 0
                level = max(float(np.median(fitc[seed_mask])), 1.0)
                intensity = np.abs(fitc-level) / level
                yield (distance + intensity_weight*intensity*np.maximum(distance, 1) +
                       direction_weight*direction*np.maximum(distance, 1))
        winner = _streaming_argmin(community_costs())
        for k, cid in enumerate(ids):
            final[region & (winner == k)] = cid
    dense = np.zeros_like(final)
    for new, old in enumerate(np.unique(final[final > 0]), 1):
        dense[final == old] = new
    return dense
