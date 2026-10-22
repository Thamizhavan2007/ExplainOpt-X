
"""Hierarchical aggregation of instruction-level attribution scores."""

from __future__ import annotations

from collections import defaultdict
from typing import Mapping, Any

import networkx as nx


def build_hierarchical_explanation(
    graph: nx.MultiDiGraph,
    instruction_scores: Mapping[str, float],
    *,
    aggregation: str = "sum",
) -> dict[str, Any]:
    """Aggregate instruction attribution scores by block and function.

    Scores must be keyed by graph node IDs. Missing node scores are reported
    explicitly rather than silently treated as zero.
    """
    if aggregation not in {"sum", "mean"}:
        raise ValueError("aggregation must be 'sum' or 'mean'")

    graph_nodes = set(graph.nodes)
    unknown = set(instruction_scores) - graph_nodes
    if unknown:
        raise ValueError(
            f"Scores supplied for unknown graph nodes: {sorted(unknown)}"
        )

    missing = sorted(graph_nodes - set(instruction_scores))
    grouped: dict[tuple[str, str], list[tuple[str, float]]] = defaultdict(list)
    function_groups: dict[str, list[tuple[str, float]]] = defaultdict(list)

    instructions = []

    for node_id, attrs in graph.nodes(data=True):
        if node_id not in instruction_scores:
            continue

        score = float(instruction_scores[node_id])
        if not __import__("math").isfinite(score):
            raise ValueError(f"Non-finite score for node {node_id!r}")

        function = str(attrs.get("function", "<unknown>"))
        block = str(attrs.get("block", "<unknown>"))

        instructions.append({
            "node_id": str(node_id),
            "function": function,
            "block": block,
            "index": attrs.get("index"),
            "line_number": attrs.get("line_number"),
            "opcode": attrs.get("opcode"),
            "text": attrs.get("text"),
            "score": score,
        })
        grouped[(function, block)].append((str(node_id), score))
        function_groups[function].append((str(node_id), score))

    def aggregate(items: list[tuple[str, float]]) -> float:
        total = sum(score for _, score in items)
        return total if aggregation == "sum" else total / len(items)

    blocks = []
    for (function, block), items in sorted(grouped.items()):
        blocks.append({
            "function": function,
            "block": block,
            "instruction_count": len(items),
            "score": aggregate(items),
            "instructions": [
                {"node_id": node_id, "score": score}
                for node_id, score in items
            ],
        })

    functions = []
    for function, items in sorted(function_groups.items()):
        functions.append({
            "function": function,
            "instruction_count": len(items),
            "block_count": len({
                (str(graph.nodes[node_id].get("function", "<unknown>")),
                 str(graph.nodes[node_id].get("block", "<unknown>")))
                for node_id, _ in items
            }),
            "score": aggregate(items),
        })

    instructions.sort(key=lambda item: (-abs(item["score"]), item["node_id"]))
    blocks.sort(key=lambda item: (-abs(item["score"]), item["function"], item["block"]))
    functions.sort(key=lambda item: (-abs(item["score"]), item["function"]))

    return {
        "schema_version": 1,
        "aggregation": aggregation,
        "score_semantics": (
            "Aggregated input attribution scores; not independently "
            "validated causal effects."
        ),
        "coverage": {
            "graph_node_count": graph.number_of_nodes(),
            "scored_instruction_count": len(instructions),
            "missing_score_node_ids": missing,
            "complete": not missing,
        },
        "instructions": instructions,
        "basic_blocks": blocks,
        "functions": functions,
    }
