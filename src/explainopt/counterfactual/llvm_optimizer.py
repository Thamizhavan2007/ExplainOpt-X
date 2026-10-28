from pathlib import Path
import subprocess
import tempfile

from .llvm_config import get_opt_path


def optimize_llvm_ir(
    ir_text: str,
    level: str = "O2",
    timeout: int = 30,
) -> tuple[str, str | None]:
    """Optimize LLVM IR and return (optimized_ir, error_message)."""

    allowed_levels = {"O0", "O1", "O2", "O3"}
    if level not in allowed_levels:
        return ir_text, f"Unsupported optimization level: {level}"

    opt = get_opt_path()
    if opt is None:
        return ir_text, "LLVM opt.exe was not found."

    with tempfile.TemporaryDirectory(prefix="explainopt_llvm_") as tmp:
        input_path = Path(tmp) / "input.ll"
        output_path = Path(tmp) / "output.ll"

        try:
            input_path.write_text(ir_text, encoding="utf-8")

            result = subprocess.run(
                [
                    opt,
                    f"-passes=default<{level}>",
                    str(input_path),
                    "-S",
                    "-o",
                    str(output_path),
                ],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )

            if result.returncode != 0:
                detail = result.stderr.strip() or result.stdout.strip()
                return ir_text, detail or "LLVM optimization failed."

            optimized_ir = output_path.read_text(encoding="utf-8")
            return optimized_ir, None

        except (OSError, UnicodeError, subprocess.TimeoutExpired) as exc:
            return ir_text, str(exc)
