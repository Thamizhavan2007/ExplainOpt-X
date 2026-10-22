
"""Export and compare ExplainOpt-X program graphs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.explainopt.ir_parser import parse_ir
from src.explainopt.program_graph import build_program_graph, graph_summary


def export_graph(ir_path: str | Path, output_path: str | Path) -> dict:
    """Parse an IR file, build its instruction graph, and export JSON."""
    module = parse_ir(ir_path)

    if not module.functions:
        raise ValueError(f"No function definitions found in {ir_path}")

    graph = None
    function_summaries = []

    for function in module.functions:
        function_graph = build_program_graph(function)

        function_summaries.append({
            "function": function.name,
            **graph_summary(function_graph),
        })

        if graph is None:
            graph = function_graph
        else:
            # Avoid collisions between identically named blocks in
            # different functions by prefixing node IDs with function names.
            import networkx as nx

            renamed = nx.relabel_nodes(
                function_graph,
                {node: f"{function.name}:{node}" for node in function_graph},
                copy=True,
            )
            graph = nx.compose(graph, renamed)

    data = {
        "schema_version": 1,
        "source_ir": str(ir_path),
        "functions": function_summaries,
        "nodes": [
            {"id": str(node), **attrs}
            for node, attrs in graph.nodes(data=True)
        ],
        "edges": [
            {"source": str(source), "target": str(target), **attrs}
            for source, target, attrs in graph.edges(data=True, keys=False)
        ],
    }

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data, indent=2), encoding="utf-8")

    return data


def compare_exports(left: dict, right: dict) -> dict:
    """Compare structural metrics between two exported graphs."""
    def edge_counts(data):
        counts = {}
        for edge in data["edges"]:
            kind = edge.get("edge_type", "unknown")
            counts[kind] = counts.get(kind, 0) + 1
        return counts

    left_counts = edge_counts(left)
    right_counts = edge_counts(right)

    return {
        "left_ir": left["source_ir"],
        "right_ir": right["source_ir"],
        "left_nodes": len(left["nodes"]),
        "right_nodes": len(right["nodes"]),
        "left_edges": len(left["edges"]),
        "right_edges": len(right["edges"]),
        "left_edge_types": left_counts,
        "right_edge_types": right_counts,
        "node_count_delta": len(right["nodes"]) - len(left["nodes"]),
        "edge_count_delta": len(right["edges"]) - len(left["edges"]),
    }


def main():
    parser = argparse.ArgumentParser(
        description="Export and compare ExplainOpt-X program graphs."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    export_parser = subparsers.add_parser("export")
    export_parser.add_argument("ir_file")
    export_parser.add_argument("output_json")

    compare_parser = subparsers.add_parser("compare")
    compare_parser.add_argument("left_json")
    compare_parser.add_argument("right_json")
    compare_parser.add_argument("--output", default="results/graph_comparison.json")

    args = parser.parse_args()

    if args.command == "export":
        result = export_graph(args.ir_file, args.output_json)
        print(f"Exported: {args.output_json}")
        print(f"Functions: {len(result['functions'])}")
        print(f"Instruction nodes: {len(result['nodes'])}")
        print(f"Edges: {len(result['edges'])}")

    elif args.command == "compare":
        left = json.loads(Path(args.left_json).read_text(encoding="utf-8"))
        right = json.loads(Path(args.right_json).read_text(encoding="utf-8"))
        comparison = compare_exports(left, right)

        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(comparison, indent=2), encoding="utf-8")

        print(json.dumps(comparison, indent=2))
        print(f"\nComparison saved to: {output}")


if __name__ == "__main__":
    main()
