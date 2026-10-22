
"""Structured LLVM IR text parser for ExplainOpt-X.

This module extracts functions, basic blocks, instructions, and branch
targets. It is intentionally independent of graph construction so that
the parsed representation can be reused by multiple downstream modules.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


_FUNCTION_RE = re.compile(
    r"^\s*define\b.*?@(?P<name>[-\w.$]+)\s*\("
)
_LABEL_RE = re.compile(r"^\s*(?P<label>[-\w.$]+):")
_ASSIGNMENT_RE = re.compile(r"^\s*(?P<result>%[-\w.$]+)\s*=\s*")
_LOCAL_RE = re.compile(r"%([-a-zA-Z$._0-9]+)")
_GLOBAL_RE = re.compile(r"@([-a-zA-Z$._0-9]+)")
_BRANCH_TARGET_RE = re.compile(
    r"\blabel\s+%(?P<target>[-\w.$]+)"
)

_TERMINATORS = {
    "br",
    "switch",
    "indirectbr",
    "invoke",
    "callbr",
    "ret",
    "resume",
    "catchswitch",
    "catchret",
    "cleanupret",
    "unreachable",
}

_PREFIXES = {"tail", "musttail", "notail"}
_IGNORED_PREFIXES = (
    "attributes ",
    "declare ",
    "source_filename",
    "target ",
    "module asm",
)


@dataclass
class Instruction:
    """One LLVM IR instruction."""

    line_number: int
    text: str
    opcode: str
    result: str | None = None
    local_operands: list[str] = field(default_factory=list)
    global_operands: list[str] = field(default_factory=list)
    is_terminator: bool = False
    branch_targets: list[str] = field(default_factory=list)


@dataclass
class BasicBlock:
    """A basic block and its ordered instructions."""

    name: str
    instructions: list[Instruction] = field(default_factory=list)

    @property
    def terminator(self) -> Instruction | None:
        if self.instructions and self.instructions[-1].is_terminator:
            return self.instructions[-1]
        return None


@dataclass
class Function:
    """A function definition in the LLVM module."""

    name: str
    blocks: list[BasicBlock] = field(default_factory=list)


@dataclass
class IRModule:
    """Parsed representation of an LLVM IR file."""

    source_path: str
    functions: list[Function] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return asdict(self)


def _strip_comment(line: str) -> str:
    """Strip LLVM semicolon comments while respecting quoted strings."""
    in_string = False
    escaped = False

    for index, char in enumerate(line):
        if escaped:
            escaped = False
            continue

        if char == "\\" and in_string:
            escaped = True
        elif char == '"':
            in_string = not in_string
        elif char == ";" and not in_string:
            return line[:index].strip()

    return line.strip()


def _parse_instruction(line: str, line_number: int) -> Instruction | None:
    """Parse the leading opcode and common operands of one IR instruction."""
    assignment = _ASSIGNMENT_RE.match(line)
    result = assignment.group("result") if assignment else None

    body = line[assignment.end():].strip() if assignment else line.strip()
    words = body.split()
    if not words:
        return None

    # Calls can have tail-call markers before the actual instruction.
    while words and words[0] in _PREFIXES:
        words.pop(0)

    if not words:
        return None

    opcode = words[0]
    return Instruction(
        line_number=line_number,
        text=line,
        opcode=opcode,
        result=result,
        local_operands=_LOCAL_RE.findall(body),
        global_operands=_GLOBAL_RE.findall(body),
        is_terminator=opcode in _TERMINATORS,
        branch_targets=[
            match.group("target")
            for match in _BRANCH_TARGET_RE.finditer(body)
        ],
    )


def parse_ir(file_path: str | Path) -> IRModule:
    """Parse an LLVM IR file into functions, blocks, and instructions.

    This is a structured text parser, not a replacement for LLVM's parser
    or verifier. Run LLVM verification separately before research use.
    """
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"LLVM IR file not found: {path}")

    module = IRModule(source_path=str(path))
    current_function: Function | None = None
    current_block: BasicBlock | None = None

    def finish_block() -> None:
        nonlocal current_block
        if current_function is not None and current_block is not None:
            if not current_block.instructions:
                module.warnings.append(
                    f"Empty basic block {current_block.name!r} "
                    f"in function {current_function.name!r}"
                )
            current_function.blocks.append(current_block)
        current_block = None

    def finish_function() -> None:
        nonlocal current_function
        finish_block()

        if current_function is not None:
            if not current_function.blocks:
                module.warnings.append(
                    f"Function {current_function.name!r} has no parsed blocks"
                )
            module.functions.append(current_function)

        current_function = None

    lines = path.read_text(encoding="utf-8").splitlines()

    for line_number, raw_line in enumerate(lines, start=1):
        line = _strip_comment(raw_line)
        if not line:
            continue

        if current_function is None:
            match = _FUNCTION_RE.match(line)
            if match:
                current_function = Function(name=match.group("name"))
                # LLVM permits an implicit entry block without a label.
                current_block = BasicBlock(name="entry")
            continue

        if line == "}":
            finish_function()
            continue

        if line.startswith(_IGNORED_PREFIXES) or line.startswith("!"):
            continue

        label_match = _LABEL_RE.match(line)
        if label_match:
            label = label_match.group("label")

            if current_block is not None and not current_block.instructions:
                current_block.name = label
            else:
                finish_block()
                current_block = BasicBlock(name=label)
            continue

        # Ignore common function-level metadata lines, but retain ordinary
        # instruction lines even when they contain metadata annotations.
        if current_block is None:
            current_block = BasicBlock(name="entry")

        instruction = _parse_instruction(line, line_number)
        if instruction is not None:
            current_block.instructions.append(instruction)

    if current_function is not None:
        module.warnings.append(
            f"Function {current_function.name!r} was not closed with '}}'"
        )
        finish_function()

    _validate_parsed_structure(module)
    return module


def _validate_parsed_structure(module: IRModule) -> None:
    """Report missing terminators and unresolved local branch targets."""
    for function in module.functions:
        block_names = {block.name for block in function.blocks}

        for block in function.blocks:
            if not block.terminator:
                module.warnings.append(
                    f"Block {block.name!r} in function {function.name!r} "
                    "has no recognized final terminator"
                )

            for instruction in block.instructions:
                for target in instruction.branch_targets:
                    if target not in block_names:
                        module.warnings.append(
                            f"Unresolved branch target %{target} in "
                            f"function {function.name!r}, block {block.name!r}"
                        )


def summarize_ir(module: IRModule) -> dict[str, Any]:
    """Compute deterministic summary metrics for an IR module."""
    functions = []

    for function in module.functions:
        instruction_count = sum(
            len(block.instructions) for block in function.blocks
        )
        functions.append(
            {
                "name": function.name,
                "basic_block_count": len(function.blocks),
                "instruction_count": instruction_count,
                "opcode_counts": _opcode_counts(function),
            }
        )

    return {
        "source_path": module.source_path,
        "function_count": len(module.functions),
        "functions": functions,
        "warnings": module.warnings,
    }


def _opcode_counts(function: Function) -> dict[str, int]:
    counts: dict[str, int] = {}
    for block in function.blocks:
        for instruction in block.instructions:
            counts[instruction.opcode] = (
                counts.get(instruction.opcode, 0) + 1
            )
    return dict(sorted(counts.items()))
