
"""Extract transparent features for LLVM instruction-level analysis."""

from __future__ import annotations

from collections import Counter
from typing import Any

import networkx as nx


def extract_instruction_features(
    graph: nx.MultiDiGraph,
) -> list[dict[str, Any]]:
    """Return one feature record per graph instruction node."""
    records = []

    for node_id, attrs in graph.nodes(data=True):
        incoming = list(graph.in_edges(node_id, data=True))
        outgoing = list(graph.out_edges(node_id, data=True))

        incoming_data = sum(
            edge.get("edge_type") == "data_dependency"
            for _, _, edge in incoming
        )
        outgoing_data = sum(
            edge.get("edge_type") == "data_dependency"
            for _, _, edge in outgoing
        )
        incoming_control = sum(
            edge.get("edge_type") == "control_flow"
            for _, _, edge in incoming
        )
        outgoing_control = sum(
            edge.get("edge_type") == "control_flow"
            for _, _, edge in outgoing
        )

        records.append({
            "node_id": str(node_id),
            "function": str(attrs.get("function", "<unknown>")),
            "block": str(attrs.get("block", "<unknown>")),
            "index": attrs.get("index"),
            "line_number": attrs.get("line_number"),
            "opcode": str(attrs.get("opcode", "unknown")),
            "in_data_degree": incoming_data,
            "out_data_degree": outgoing_data,
            "in_control_degree": incoming_control,
            "out_control_degree": outgoing_control,
            "text": attrs.get("text"),
        })

    return records


def summarize_instruction_features(
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    """Summarize the instruction records without making causal claims."""
    opcode_counts = Counter(record["opcode"] for record in records)

    return {
        "instruction_count": len(records),
        "opcode_counts": dict(sorted(opcode_counts.items())),
        "instructions": records,
        "interpretation": (
            "Structural instruction features; not learned attribution scores."
        ),
    }
