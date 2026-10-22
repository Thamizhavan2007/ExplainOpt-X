
import argparse
import json
from pathlib import Path

import networkx as nx


def build_graph(ir_data):
    """Build a directed control-flow graph from parsed LLVM IR."""
    graph = nx.DiGraph()

    for function in ir_data.get("functions", []):
        function_name = function["name"]

        # Create one node for every basic block.
        for block in function.get("blocks", []):
            instructions = block.get("instructions", [])

            graph.add_node(
                f"{function_name}:{block['name']}",
                function=function_name,
                block=block["name"],
                instruction_count=len(instructions),
                opcodes=[item["opcode"] for item in instructions],
                instruction_text=[item["text"] for item in instructions],
            )

        # Add directed control-flow edges.
        for edge in function.get("edges", []):
            source = f"{function_name}:{edge['source']}"
            target = f"{function_name}:{edge['target']}"

            if source in graph and target in graph:
                graph.add_edge(source, target, edge_type="control_flow")

    return graph


def save_graph(graph, output_path):
    """Save graph nodes and edges to a JSON file."""
    data = {
        "nodes": [
            {"id": node, **attributes}
            for node, attributes in graph.nodes(data=True)
        ],
        "edges": [
            {"source": source, "target": target, **attributes}
            for source, target, attributes in graph.edges(data=True)
        ],
    }

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(
        description="Build a control-flow graph from parsed LLVM IR JSON."
    )
    parser.add_argument("input_json", help="Parser JSON input")
    parser.add_argument(
        "--output",
        default="results/sum_O0_graph.json",
        help="Path for graph JSON output",
    )
    args = parser.parse_args()

    input_path = Path(args.input_json)
    if not input_path.is_file():
        raise SystemExit(f"Input file not found: {input_path}")

    ir_data = json.loads(input_path.read_text(encoding="utf-8"))
    graph = build_graph(ir_data)

    save_graph(graph, args.output)

    print(f"Graph saved to: {args.output}")
    print(f"Nodes (basic blocks): {graph.number_of_nodes()}")
    print(f"Edges (control flow): {graph.number_of_edges()}")

    for node, attributes in graph.nodes(data=True):
        print(
            f"{node}: {attributes['instruction_count']} instructions"
        )


if __name__ == "__main__":
    main()
