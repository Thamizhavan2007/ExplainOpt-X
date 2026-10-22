
"""Control-flow graph construction for ExplainOpt-X."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import networkx as nx

from src.explainopt.ir_parser import BasicBlock, Function, IRModule


@dataclass
class CFGResult:
    """Control-flow graph and validation diagnostics."""

    function_name: str
    graph: nx.DiGraph
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "function": self.function_name,
            "nodes": [
                {"id": node, **attrs}
                for node, attrs in self.graph.nodes(data=True)
            ],
            "edges": [
                {"source": source, "target": target, **attrs}
                for source, target, attrs in self.graph.edges(data=True)
            ],
            "warnings": self.warnings,
        }


def _successors(block: BasicBlock) -> list[tuple[str, str]]:
    """Return (target, edge_type) pairs from a block's terminator."""
    terminator = block.terminator
    if terminator is None:
        return []

    opcode = terminator.opcode
    targets = terminator.branch_targets

    if opcode == "br":
        if len(targets) == 1:
            return [(targets[0], "unconditional")]
        if len(targets) == 2:
            return [
                (targets[0], "conditional_true"),
                (targets[1], "conditional_false"),
            ]
        return []

    if opcode in {"switch", "indirectbr"}:
        return [(target, "multiway") for target in targets]

    if opcode in {"invoke", "callbr"}:
        return [(target, "exceptional_or_indirect") for target in targets]

    if opcode in {"catchswitch", "catchret", "cleanupret"}:
        return [(target, "exceptional") for target in targets]

    # ret, resume, and unreachable have no local successor.
    return []


def build_cfg(function: Function) -> CFGResult:
    """Construct a directed CFG for a single LLVM function."""
    graph = nx.DiGraph()
    warnings: list[str] = []

    block_names = [block.name for block in function.blocks]
    if len(block_names) != len(set(block_names)):
        warnings.append(
            f"Function {function.name!r} has duplicate basic-block labels."
        )

    known_blocks = set(block_names)

    # Each node represents one basic block.
    for block in function.blocks:
        instructions = block.instructions
        graph.add_node(
            block.name,
            function=function.name,
            instruction_count=len(instructions),
            opcodes=[item.opcode for item in instructions],
            instructions=[item.text for item in instructions],
            terminator=(
                block.terminator.opcode if block.terminator else None
            ),
        )

        if block.terminator is None:
            warnings.append(
                f"Block {block.name!r} has no recognized final terminator."
            )

    # Only terminators define CFG successors.
    for block in function.blocks:
        for target, edge_type in _successors(block):
            if target not in known_blocks:
                warnings.append(
                    f"Block {block.name!r} targets missing block %{target}."
                )
                continue

            graph.add_edge(
                block.name,
                target,
                edge_type=edge_type,
            )

    if function.blocks:
        entry = function.blocks[0].name
        reachable = nx.descendants(graph, entry) | {entry}
        unreachable = known_blocks - reachable

        if unreachable:
            warnings.append(
                "Unreachable blocks: " + ", ".join(sorted(unreachable))
            )

    return CFGResult(
        function_name=function.name,
        graph=graph,
        warnings=warnings,
    )


def build_module_cfgs(module: IRModule) -> list[CFGResult]:
    """Build one CFG per function in an LLVM module."""
    return [build_cfg(function) for function in module.functions]
