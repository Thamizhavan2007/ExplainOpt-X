
import argparse
import json
import re
from collections import Counter
from pathlib import Path


LABEL_RE = re.compile(r"^\s*([A-Za-z$._][\w$.-]*|\d+):")
FUNCTION_RE = re.compile(r"^\s*define\b.*?@([\w.$-]+)\s*\(")
BRANCH_TARGET_RE = re.compile(r"\blabel\s+%([A-Za-z$._][\w$.-]*|\d+)")
ASSIGNMENT_RE = re.compile(r"^\s*(?:%[-\w.$]+\s*=\s*)?")
OPCODE_PREFIXES = {"tail", "musttail", "notail"}


def remove_comment(line):
    """Remove LLVM IR comments without cutting quoted strings."""
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


def get_opcode(instruction):
    """Return the primary opcode, ignoring an optional SSA assignment."""
    text = ASSIGNMENT_RE.sub("", instruction, count=1).strip()

    if not text:
        return None

    words = text.split()
    while words and words[0] in OPCODE_PREFIXES:
        words.pop(0)

    return words[0] if words else None


def parse_ir(file_path):
    """Parse function definitions, basic blocks, instructions and CFG edges."""
    path = Path(file_path)

    if not path.is_file():
        raise FileNotFoundError(f"LLVM IR file not found: {path}")

    functions = []
    current_function = None
    current_block = None

    def finish_block():
        nonlocal current_block

        if current_function is not None and current_block is not None:
            current_function["blocks"].append(current_block)

        current_block = None

    def finish_function():
        nonlocal current_function

        if current_function is not None:
            finish_block()

            blocks = current_function["blocks"]
            edges = []

            for block in blocks:
                for target in block["branch_targets"]:
                    edges.append({
                        "source": block["name"],
                        "target": target,
                    })

            current_function["edges"] = edges
            current_function["instruction_counts"] = dict(
                Counter(
                    instruction["opcode"]
                    for block in blocks
                    for instruction in block["instructions"]
                )
            )
            functions.append(current_function)

        current_function = None

    for line_number, original_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = remove_comment(original_line)

        if not line:
            continue

        if current_function is None:
            match = FUNCTION_RE.match(line)
            if match:
                current_function = {
                    "name": match.group(1),
                    "blocks": [],
                    "edges": [],
                    "instruction_counts": {},
                }
                current_block = {
                    "name": "entry",
                    "instructions": [],
                    "branch_targets": [],
                }
            continue

        if line == "}":
            finish_function()
            continue

        label_match = LABEL_RE.match(line)

        if label_match:
            label = label_match.group(1)

            if current_block is not None and not current_block["instructions"]:
                current_block["name"] = label
            else:
                finish_block()
                current_block = {
                    "name": label,
                    "instructions": [],
                    "branch_targets": [],
                }
            continue

        # Ignore non-instruction lines such as metadata and attributes.
        if line.startswith(("!", "attributes ", "declare ", "source_filename")):
            continue

        if current_block is None:
            current_block = {
                "name": "entry",
                "instructions": [],
                "branch_targets": [],
            }

        opcode = get_opcode(line)
        if opcode is None:
            continue

        instruction = {
            "line": line_number,
            "opcode": opcode,
            "text": line,
        }

        current_block["instructions"].append(instruction)

        # Branch instructions define the outgoing CFG edges.
        if opcode in {"br", "switch", "indirectbr"}:
            current_block["branch_targets"].extend(
                BRANCH_TARGET_RE.findall(line)
            )

    if current_function is not None:
        finish_function()

    return {
        "file": str(path),
        "functions": functions,
    }


def print_summary(result):
    print(f"\nLLVM IR file: {result['file']}")
    print(f"Functions found: {len(result['functions'])}")

    for function in result["functions"]:
        blocks = function["blocks"]
        instructions = [
            instruction
            for block in blocks
            for instruction in block["instructions"]
        ]

        print(f"\nFunction: {function['name']}")
        print(f"Basic blocks: {len(blocks)}")
        print(f"Instructions: {len(instructions)}")
        print(f"Control-flow edges: {len(function['edges'])}")

        print("Instruction counts:")
        for opcode, count in sorted(
            function["instruction_counts"].items()
        ):
            print(f"  {opcode}: {count}")

        print("Control-flow edges:")
        if function["edges"]:
            for edge in function["edges"]:
                print(f"  {edge['source']} -> {edge['target']}")
        else:
            print("  No branch edges found.")


def main():
    parser = argparse.ArgumentParser(
        description="Extract basic blocks and CFG information from LLVM IR."
    )
    parser.add_argument("ir_file", help="Path to an LLVM .ll file")
    parser.add_argument(
        "--json",
        dest="json_file",
        help="Optional path to save extracted information as JSON",
    )
    args = parser.parse_args()

    try:
        result = parse_ir(args.ir_file)
    except (OSError, ValueError) as error:
        raise SystemExit(f"Error: {error}") from error

    print_summary(result)

    if args.json_file:
        output_path = Path(args.json_file)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(result, indent=2),
            encoding="utf-8",
        )
        print(f"\nJSON results saved to: {output_path}")


if __name__ == "__main__":
    main()
