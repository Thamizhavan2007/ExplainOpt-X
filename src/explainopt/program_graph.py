
"""Instruction-level program graph for ExplainOpt-X."""

from __future__ import annotations

import re

import networkx as nx

from src.explainopt.cfg import build_cfg
from src.explainopt.ir_parser import Function


_SSA_RE = re.compile(r"%([-A-Za-z$._0-9]+)")
_PHI_INCOMING_RE = re.compile(
    r"\[\s*([^,\]]+)\s*,\s*%([-A-Za-z$._0-9]+)\s*\]"
)


def _ssa_name(value: str) -> str | None:
    """Normalize an SSA operand to its %name representation."""
    match = _SSA_RE.search(value.strip())
    if match:
        return f"%{match.group(1)}"
    return None


def _phi_uses(instruction) -> list[tuple[str, str]]:
    """Extract (SSA value, incoming block) pairs from a phi instruction."""
    uses = []

    for value_text, block_name in _PHI_INCOMING_RE.findall(instruction.text):
        value_text = value_text.strip()
        value = _ssa_name(value_text)

        if value is None:
            # Constants are not SSA definitions.
            continue

        uses.append((value, block_name))

    return uses


def _dominators(cfg: nx.DiGraph, entry: str) -> dict[str, set[str]]:
    """Compute dominator sets for blocks reachable from entry."""
    reachable = set(nx.descendants(cfg, entry)) | {entry}
    subgraph = cfg.subgraph(reachable).copy()

    if entry not in subgraph:
        return {}

    immediate = nx.immediate_dominators(subgraph, entry)
    result: dict[str, set[str]] = {}

    for block in subgraph.nodes:
        dominators = {block}
        current = block

        while current != entry:
            parent = immediate.get(current)

            if parent is None or parent == current:
                break

            dominators.add(parent)
            current = parent

        result[block] = dominators

    return result


def build_program_graph(function: Function) -> nx.MultiDiGraph:
    """Build instruction nodes with SSA data and control-flow edges."""
    graph = nx.MultiDiGraph()
    definitions: dict[str, str] = {}
    block_nodes: dict[str, list[str]] = {}
    node_locations: dict[str, tuple[str, int]] = {}

    # Create instruction nodes and index SSA definitions.
    for block in function.blocks:
        block_nodes[block.name] = []

        for index, instruction in enumerate(block.instructions):
            node_id = f"{block.name}:i{index}"

            graph.add_node(
                node_id,
                function=function.name,
                block=block.name,
                index=index,
                line_number=instruction.line_number,
                opcode=instruction.opcode,
                text=instruction.text,
                result=instruction.result,
            )

            block_nodes[block.name].append(node_id)
            node_locations[node_id] = (block.name, index)

            if instruction.result:
                definitions[instruction.result] = node_id

    if not function.blocks:
        return graph

    cfg = build_cfg(function)
    entry = function.blocks[0].name
    dominators = _dominators(cfg.graph, entry)

    def definition_reaches_use(
        producer: str,
        consumer_block: str,
        consumer_index: int,
        incoming_block: str | None = None,
    ) -> bool:
        producer_block, producer_index = node_locations[producer]
        use_block = incoming_block or consumer_block

        # Same-block ordinary uses must follow their definitions.
        if producer_block == use_block:
            if incoming_block is None:
                return producer_index < consumer_index

            # A phi incoming value is used at the end of its predecessor.
            return True

        if use_block not in dominators:
            return False

        return producer_block in dominators[use_block]

    # Add SSA data-dependency edges.
    for block in function.blocks:
        for index, instruction in enumerate(block.instructions):
            consumer = block_nodes[block.name][index]

            if instruction.opcode == "phi":
                for value, incoming_block in _phi_uses(instruction):
                    producer = definitions.get(value)

                    if producer is None or producer == consumer:
                        continue

                    if incoming_block not in cfg.graph:
                        continue

                    if definition_reaches_use(
                        producer,
                        block.name,
                        index,
                        incoming_block=incoming_block,
                    ):
                        graph.add_edge(
                            producer,
                            consumer,
                            edge_type="data_dependency",
                            value=value,
                            use_kind="phi_incoming",
                            incoming_block=incoming_block,
                        )

            else:
                for operand in instruction.local_operands:
                    raw_operand = str(operand).strip()

                    # Support operands stored with or without '%'.
                    if raw_operand.startswith("%"):
                        value = _ssa_name(raw_operand)
                    else:
                        value = _ssa_name(f"%{raw_operand}")

                    if value is None:
                        continue

                    producer = definitions.get(value)

                    if producer is None or producer == consumer:
                        continue

                    if definition_reaches_use(
                        producer,
                        block.name,
                        index,
                    ):
                        graph.add_edge(
                            producer,
                            consumer,
                            edge_type="data_dependency",
                            value=value,
                            use_kind="ordinary",
                        )

    # Add control-flow edges between instructions at block boundaries.
    for source_block, target_block, attrs in cfg.graph.edges(data=True):
        source = block_nodes.get(source_block, [])
        target = block_nodes.get(target_block, [])

        if source and target:
            graph.add_edge(
                source[-1],
                target[0],
                edge_type="control_flow",
                branch_type=attrs.get("edge_type", "control_flow"),
            )

    return graph


def graph_summary(graph: nx.MultiDiGraph) -> dict:
    """Summarize instruction nodes and typed edges."""
    edge_counts: dict[str, int] = {}

    for _, _, attrs in graph.edges(data=True):
        kind = attrs.get("edge_type", "unknown")
        edge_counts[kind] = edge_counts.get(kind, 0) + 1

    return {
        "instruction_nodes": graph.number_of_nodes(),
        "total_edges": graph.number_of_edges(),
        "edge_counts": dict(sorted(edge_counts.items())),
    }
