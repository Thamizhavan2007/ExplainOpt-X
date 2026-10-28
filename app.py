from pathlib import Path

import difflib

import html

import inspect

import os

import re

import subprocess

import shutil

import sys

import tempfile

import traceback
import time

from datetime import datetime

import streamlit as st

import pandas as pd

import altair as alt

# ---------------------------------------------------------------------

# Page configuration (must be the first Streamlit call)

# ---------------------------------------------------------------------

st.set_page_config(

    page_title="ExplainOpt-X",

    page_icon="⚙️",

    layout="wide",

)

ROOT = Path(__file__).resolve().parent

if str(ROOT) not in sys.path:

    sys.path.insert(0, str(ROOT))

# Tool locations. The environment variables are optional overrides.

CLANG = Path(

    os.environ.get(

        "EXPLAINOPT_CLANG",

        r"C:\Program Files\LLVM\bin\clang.exe",

    )

)

OPT = Path(

    os.environ.get(

        "EXPLAINOPT_OPT",

        r"C:\Users\Kamesh Shivan\Downloads"

        r"\clang+llvm-23.1.4-x86_64-pc-windows-msvc\bin\opt.exe",

    )

)

# Optional LLVM IR-to-C backend. Set EXPLAINOPT_LLVM_CBE to the full path
# of llvm-cbe.exe, or place llvm-cbe on PATH. LLVM-CBE is a separate tool.
LLVM_CBE = os.environ.get("EXPLAINOPT_LLVM_CBE", "").strip() or shutil.which("llvm-cbe") or shutil.which("llvm-cbe.exe")


def decompile_optimized_ir_to_c(ir_text):
    """Convert optimized LLVM IR to C with LLVM-CBE when available."""
    if not isinstance(ir_text, str) or not ir_text.strip():
        return "", "No optimized LLVM IR was available to convert."
    executable = LLVM_CBE
    if not executable:
        return "", (
            "LLVM-CBE was not found. Install/build LLVM-CBE compatible with your LLVM IR, "
            "then set EXPLAINOPT_LLVM_CBE to its executable path or add it to PATH."
        )
    try:
        with tempfile.TemporaryDirectory(prefix="explainopt_cbe_") as temp_dir:
            ir_path = Path(temp_dir) / "optimized.ll"
            c_path = Path(temp_dir) / "optimized.c"
            ir_path.write_text(ir_text, encoding="utf-8")
            proc = subprocess.run(
                [str(executable), str(ir_path), "-o", str(c_path)],
                capture_output=True, text=True, timeout=60, check=False,
            )
            if proc.returncode != 0:
                detail = (proc.stderr or proc.stdout or "No diagnostic was returned.").strip()
                return "", f"LLVM-CBE failed (exit {proc.returncode}): {detail}"
            c_text = c_path.read_text(encoding="utf-8", errors="replace") if c_path.is_file() else (proc.stdout or "")
            if not c_text.strip():
                return "", "LLVM-CBE completed but did not produce readable C output."
            return c_text, "Converted optimized LLVM IR to C using LLVM-CBE."
    except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
        return "", f"LLVM-CBE could not run: {type(exc).__name__}: {exc}"


# -disable-O0-optnone stops Clang from marking every function "optnone" at

# -O0. Without it, opt's O2 pipeline skips those functions and cannot

# optimize them. Remove the last two flags to restore the previous behaviour.

IR_GEN_FLAGS = [

    "-S", "-emit-llvm", "-O0",

    "-Xclang", "-disable-O0-optnone",

]

COMPILE_TIMEOUT_SECONDS = 30

MAX_SOURCE_BYTES = 1_000_000

# ---------------------------------------------------------------------

# Existing ExplainOpt-X project modules (imported defensively so that a

# broken module is reported on the page instead of crashing it)

# ---------------------------------------------------------------------

PROJECT_IMPORT_ERRORS = {}

simplify_supported_identities = None

optimize_llvm_ir = None

validate_ir = None

try:

    from src.explainopt.counterfactual.engine import (

        simplify_supported_identities,

    )

except Exception:

    PROJECT_IMPORT_ERRORS[

        "src.explainopt.counterfactual.engine.simplify_supported_identities"

    ] = traceback.format_exc()

try:

    from src.explainopt.counterfactual.llvm_optimizer import (

        optimize_llvm_ir,

    )

except Exception:

    PROJECT_IMPORT_ERRORS[

        "src.explainopt.counterfactual.llvm_optimizer.optimize_llvm_ir"

    ] = traceback.format_exc()

try:

    from src.explainopt.validation import validate_ir

except Exception:

    PROJECT_IMPORT_ERRORS[

        "src.explainopt.validation.validate_ir"

    ] = traceback.format_exc()

# ---------------------------------------------------------------------

# Styling and page header

# ---------------------------------------------------------------------

st.markdown(

    """

    <style>

    .block-container {

        padding-top: 1.5rem;

        padding-bottom: 2rem;

        max-width: 1400px;

    }

    .hero {

        padding: 1.25rem 1.5rem;

        border-radius: 16px;

        background: linear-gradient(115deg, #12243a, #244e68);

        color: white;

        margin-bottom: 1rem;

    }

    .hero h1 {

        color: white;

        margin: 0;

        font-size: 2.1rem;

    }

    .hero p {

        color: #dce8f2;

        margin: .45rem 0 0 0;

    }

    .small-muted {

        color: #64748b;

        font-size: .88rem;

    }

    </style>

    <div class="hero">

        ExplainOpt-X

        Counterfactual & Semantics-Aware Explainable Compiler Optimization

    </div>

    """,

    unsafe_allow_html=True,

)

st.caption(

    "C compiler and optimization explainer · "

    "Everything on this page is generated from the current C source only · "

    "Validation is not proof of semantic equivalence."

)

# ---------------------------------------------------------------------

# Constants: the 12 optimization checks

# ---------------------------------------------------------------------

CUSTOM_IMPL = (

    "ExplainOpt-X custom identity rewrite exists "

    "(applied only if the engine reports it)"

)

CANDIDATE_IMPL = (

    "Source-pattern candidate only — no custom ExplainOpt-X "

    "transformation is implemented for it"

)

# The first four checks correspond to the "four supported identity

# rewrites" described by the original application.

OPTIMIZATION_CHECKS = [

    {

        "name": "add_zero_identity",

        "pattern": r"\+\s*0\b",

        "description": "Add zero: x + 0",

        "custom": True,

        "meaning": "The source contains an addition of the literal 0, "

                   "a candidate for the identity x + 0 → x.",

    },

    {

        "name": "sub_zero_identity",

        "pattern": r"-\s*0\b",

        "description": "Subtract zero: x - 0",

        "custom": True,

        "meaning": "The source contains a subtraction of the literal 0, "

                   "a candidate for the identity x - 0 → x.",

    },

    {

        "name": "mul_one_identity",

        "pattern": r"\*\s*1\b",

        "description": "Multiply by one: x * 1",

        "custom": True,

        "meaning": "The source contains a multiplication by the literal 1, "

                   "a candidate for the identity x * 1 → x.",

    },

    {

        "name": "div_one_identity",

        "pattern": r"/\s*1\b",

        "description": "Divide by one: x / 1",

        "custom": True,

        "meaning": "The source contains a division by the literal 1, "

                   "a candidate for the identity x / 1 → x.",

    },

    {

        "name": "constant_folding",

        "pattern": r"\b\d+\s*[*+/-]\s*\d+\b",

        "description": "Constant folding",

        "custom": False,

        "meaning": "Arithmetic on two literals appears in the source. "

                   "Clang or LLVM may fold it; this is not reported per pass.",

    },

    {

        "name": "constant_propagation",

        "pattern": r"\bint\s+\w+\s*=\s*\d+",

        "description": "Constant propagation candidate",

        "custom": False,

        "meaning": "An int variable is initialised with a literal, which "

                   "may allow constant propagation.",

    },

    {

        "name": "dead_assignment",

        "pattern": r"\b\w+\s*=\s*\d+\s*;",

        "description": "Dead assignment candidate",

        "custom": False,

        "meaning": "A variable is assigned a literal. Whether the value is "

                   "ever used was not analysed, so it is only a candidate.",

    },

    {

        "name": "common_subexpression",

        "pattern": r"(\w+\s*[*+]\s*\w+).*\n.*\1",

        "description": "Common subexpression candidate",

        "custom": False,

        "meaning": "The same simple expression appears on consecutive "

                   "lines, a candidate for common-subexpression elimination.",

    },

    {

        "name": "strength_reduction",

        "pattern": r"\*\s*(2|4|8|16)\b",

        "description": "Strength reduction candidate",

        "custom": False,

        "meaning": "A multiplication by a small power of two appears, "

                   "a candidate for replacement by a shift.",

    },

    {

        "name": "conditional_simplification",

        "pattern": r"\bif\s*\(",

        "description": "Conditional simplification candidate",

        "custom": False,

        "meaning": "An if statement is present. Whether its condition can "

                   "be simplified was not analysed.",

    },

    {

        "name": "loop_optimization",

        "pattern": r"\b(for|while)\s*\(",

        "description": "Loop optimization candidate",

        "custom": False,

        "meaning": "A loop is present. Loop transformations were not "

                   "analysed by this check.",

    },

    {

        "name": "function_inlining",

        "pattern": r"\bstatic\s+\w+\s+\w+\s*\(",

        "description": "Function inlining candidate",

        "custom": False,

        "meaning": "A static function is defined, a typical candidate for "

                   "inlining.",

    },

]

# Matches comments, string literals and character literals so the checks

# are not triggered by text inside them.

_C_NOISE = re.compile(

    r"//[^\n]*"

    r"|/\*.*?\*/"

    r'|"(?:\\.|[^"\\\n])*"'

    r"|'(?:\\.|[^'\\\n])*'",

    re.DOTALL,

)

_LABEL_RE = re.compile(r'^[\w.$"-]+:(\s*;.*)?$')

STATUS_ICON = {

    "Passed": "✅ Passed",

    "Failed": "❌ Failed",

    "Skipped": "⏭️ Skipped",

    "Unavailable": "⚠️ Unavailable",

    "Not run": "➖ Not run",

}

GENERAL_BENEFIT = (

    "Intended benefit: an identity rewrite removes a redundant instruction, "

    "which can make the IR smaller and simpler for later passes. Any "

    "runtime benefit is not measured by this application."

)

GENERAL_LIMITATIONS = [

    "Identity rewrites are exact for integer operands. For floating point, "

    "x + 0.0 can change the sign of a zero (-0.0 + 0.0 is +0.0), so "

    "validity depends on the operand type and compiler flags.",

    "Structural and LLVM validation only show that the IR is well-formed. "

    "They are not proof of semantic equivalence.",

    "LLVM's O2 pipeline may already perform the same simplification, so "

    "the custom rewrite may add nothing beyond O2.",

    "A pattern found in the C source does not guarantee the same pattern "

    "exists in the generated IR.",

]

RUNTIME_NOT_MEASURED = (

    "Runtime was not measured. Measuring it would require executing the "

    "submitted program, which ExplainOpt-X never does automatically. "

    "Only static IR size is shown."

)

# ---------------------------------------------------------------------

# General helpers

# ---------------------------------------------------------------------

def safe(value):

    """HTML-escape a value for the standalone HTML report."""

    if value is None:

        value = "Not recorded"

    return html.escape(str(value))

def status_label(status):

    return STATUS_ICON.get(status, str(status))

def show_table(rows):

    """Render a list of dicts as a table without depending on the exact

    Streamlit version (width= versus use_container_width=)."""

    frame = pd.DataFrame(rows).astype(str)

    try:

        st.dataframe(frame, width="stretch", hide_index=True)

    except Exception:

        st.dataframe(frame, use_container_width=True, hide_index=True)

def show_chart(chart):

    try:

        st.altair_chart(chart, width="stretch")

    except Exception:

        st.altair_chart(chart, use_container_width=True)

def scroll_container(height=520):

    try:

        return st.container(height=height)

    except TypeError:

        return st.container()

def strip_c_comments_and_strings(source):

    """Blank out comments and string/char literals, keeping line layout."""

    return _C_NOISE.sub(

        lambda match: re.sub(r"[^\n]", " ", match.group(0)),

        source,

    )

def validate_source(source):

    """Return a message if the source cannot be processed, else None."""

    if not source.strip():

        return "Enter or upload C source code first."

    if "\x00" in source:

        return "The source contains NUL bytes and is not valid C text."

    if len(source.encode("utf-8", errors="replace")) > MAX_SOURCE_BYTES:

        return "The source is larger than 1 MB, which is not supported."

    return None

def run_tool(args, timeout=COMPILE_TIMEOUT_SECONDS, cwd=None):

    """Run an external tool; never raises. Never runs compiled programs."""

    result = {

        "returncode": None,

        "stdout": "",

        "stderr": "",

        "error": None,

    }

    try:

        completed = subprocess.run(

            args,

            capture_output=True,

            text=True,

            encoding="utf-8",

            errors="replace",

            timeout=timeout,

            check=False,

            cwd=cwd,

        )

        result["returncode"] = completed.returncode

        result["stdout"] = completed.stdout or ""

        result["stderr"] = completed.stderr or ""

    except subprocess.TimeoutExpired:

        result["error"] = f"The tool timed out after {timeout} seconds."

    except FileNotFoundError:

        result["error"] = f"Executable not found: {args[0]}"

    except OSError as exc:

        result["error"] = f"Could not start the tool: {exc}"

    return result

def tool_version(path):

    """First lines of `<tool> --version`, or a short explanation."""

    if not path.is_file():

        return "Executable not found at the configured path"

    result = run_tool([str(path), "--version"], timeout=15)

    if result["error"]:

        return result["error"]

    text = (result["stdout"] or result["stderr"]).strip()

    if not text:

        return "No version text returned"

    return " | ".join(text.splitlines()[:2])

# ---------------------------------------------------------------------

# C compilation (executable is built but never run)

# ---------------------------------------------------------------------

def compile_c_source(source):

    """Compile with the local Clang. Returns a result dict."""

    result = {

        "source": source,

        "status": "Failed",

        "message": "",

        "stdout": "",

        "stderr": "",

        "returncode": None,

        "executable": None,

    }

    problem = validate_source(source)

    if problem:

        result["status"] = "Skipped"

        result["message"] = problem

        return result

    if not CLANG.is_file():

        result["status"] = "Unavailable"

        result["message"] = (

            "Clang was not found at the configured path. "

            "Update CLANG near the top of app.py."

        )

        return result

    try:

        with tempfile.TemporaryDirectory(prefix="explainopt_") as temp_dir:

            temp_path = Path(temp_dir)

            (temp_path / "input.c").write_text(source, encoding="utf-8")

            run = run_tool(

                [str(CLANG), "input.c", "-o", "input.exe"],

                cwd=str(temp_path),

            )

            result["stdout"] = run["stdout"]

            result["stderr"] = run["stderr"]

            result["returncode"] = run["returncode"]

            executable_path = temp_path / "input.exe"

            if run["error"]:

                result["message"] = run["error"]

            elif run["returncode"] == 0 and executable_path.is_file():

                # Read before the temporary directory is removed.

                result["executable"] = executable_path.read_bytes()

                result["status"] = "Passed"

                result["message"] = "Compilation succeeded."

            else:

                result["message"] = (

                    f"Compilation failed (exit code {run['returncode']})."

                )

    except OSError as exc:

        result["status"] = "Failed"

        result["message"] = f"File error during compilation: {exc}"

    return result

# ---------------------------------------------------------------------

# Optimization pipeline helpers

# ---------------------------------------------------------------------

def run_pattern_checks(source):

    """Evaluate all 12 checks. One failing pattern cannot affect others."""

    cleaned = strip_c_comments_and_strings(source)

    rows = []

    for number, spec in enumerate(OPTIMIZATION_CHECKS, start=1):

        row = {

            "number": number,

            "name": spec["name"],

            "description": spec["description"],

            "custom": spec["custom"],

            "implementation": CUSTOM_IMPL if spec["custom"] else CANDIDATE_IMPL,

            "detected": None,

            "location": "—",

            "state": "Error",

            "meaning": "",

        }

        try:

            compiled = re.compile(spec["pattern"], re.MULTILINE)

            match = compiled.search(cleaned)

            if match:

                row["detected"] = True

                row["state"] = "Detected"

                row["location"] = (

                    f"line {cleaned.count(chr(10), 0, match.start()) + 1}"

                )

                row["meaning"] = (

                    spec["meaning"]

                    + " Detection in the source is not evidence that any "

                      "optimization was applied."

                )

            else:

                row["detected"] = False

                row["state"] = "Not detected"

                row["meaning"] = "No match in the submitted source."

        except re.error as exc:

            row["state"] = "Pattern error"

            row["meaning"] = (

                f"This check's regular expression is invalid ({exc}); "

                "the check was skipped. The other checks are unaffected."

            )

        except Exception as exc:  # defensive: never abort the workflow

            row["state"] = "Pattern error"

            row["meaning"] = f"This check failed unexpectedly: {exc}"

        rows.append(row)

    return rows

def get_field(obj, name, default=None):

    if isinstance(obj, dict):

        return obj.get(name, default)

    return getattr(obj, name, default)

def normalize_change(change):

    """Read a transformation result object without assuming extra fields."""

    return {

        "transformation": str(

            get_field(change, "transformation", "Unnamed transformation")

        ),

        "applied": get_field(change, "applied", None),

        "line_number": get_field(change, "line_number", None),

        "reason": get_field(change, "reason", None),

        "original_instruction": get_field(

            change, "original_instruction", None

        ),

        "replacement_instruction": get_field(

            change, "replacement_instruction", None

        ),

    }

def find_opt_parameter(function):

    """Return the name of the helper's opt-path parameter, if it has one."""

    try:

        parameters = inspect.signature(function).parameters

    except (TypeError, ValueError):

        return None

    for name in (

        "opt_path", "opt_exe", "opt_executable",

        "opt_binary", "opt_bin", "opt",

    ):

        if name in parameters:

            return name

    return None

def ir_metrics(ir_text):

    """Static IR size. Instruction count is approximate."""

    functions = 0

    instructions = 0

    in_function = False

    for raw in ir_text.splitlines():

        line = raw.strip()

        if line.startswith("define "):

            functions += 1

            in_function = True

            continue

        if not in_function:

            continue

        if line == "}":

            in_function = False

            continue

        if not line or line.startswith(";") or _LABEL_RE.match(line):

            continue

        instructions += 1

    return {

        "lines": len(ir_text.splitlines()),

        "characters": len(ir_text),

        "functions": functions,

        "instructions": instructions,

    }

def ir_diff(before, after, before_name, after_name, max_lines=400):

    diff = list(

        difflib.unified_diff(

            before.splitlines(),

            after.splitlines(),

            fromfile=before_name,

            tofile=after_name,

            lineterm="",

        )

    )

    if not diff:

        return "No line differences."

    if len(diff) > max_lines:

        diff = diff[:max_lines] + [f"... diff truncated at {max_lines} lines"]

    return "\n".join(diff)

def empty_validation(status, message):

    return {

        "status": status,

        "structural": status,

        "llvm": status,

        "message": message,

        "raw": None,

        "trace": None,

    }

def _flag_to_status(value):

    if value is True:

        return "Passed"

    if value is False:

        return "Failed"

    return "Unavailable"

def run_validation(ir_text, file_name, temp_path):

    """Run the project's validate_ir on one IR text."""

    if validate_ir is None:

        return empty_validation(

            "Unavailable",

            "validate_ir could not be imported from the project.",

        )

    ir_path = temp_path / file_name

    try:

        ir_path.write_text(ir_text, encoding="utf-8")

        raw = validate_ir(ir_path, clang_path=str(CLANG))

    except Exception as exc:

        outcome = empty_validation(

            "Unavailable",

            "Validation could not be completed (this is not a "

            f"validation failure): {type(exc).__name__}: {exc}",

        )

        outcome["trace"] = traceback.format_exc()

        return outcome

    if not isinstance(raw, dict):

        return empty_validation(

            "Unavailable",

            "validate_ir returned an unexpected result type "

            f"({type(raw).__name__}).",

        )

    structural = _flag_to_status(raw.get("structural_validation_passed"))

    llvm = _flag_to_status(raw.get("llvm_validation_passed"))

    if "Failed" in (structural, llvm):

        overall = "Failed"

    elif structural == "Passed" and llvm == "Passed":

        overall = "Passed"

    else:

        overall = "Unavailable"

    return {

        "status": overall,

        "structural": structural,

        "llvm": llvm,

        "message": (

            "Structural validation: "

            f"{structural}. LLVM validation: {llvm}."

        ),

        "raw": raw,

        "trace": None,

    }

def stage_generate_ir(run, temp_path):

    stage = run["ir_generation"]

    if not CLANG.is_file():

        stage["status"] = "Unavailable"

        stage["message"] = (

            f"Clang was not found at the configured path ({CLANG}). "

            "Update CLANG near the top of app.py."

        )

        return

    (temp_path / "input.c").write_text(run["source"], encoding="utf-8")

    result = run_tool(

        [str(CLANG), *IR_GEN_FLAGS, "input.c", "-o", "original.ll"],

        cwd=str(temp_path),

    )

    stage["stdout"] = result["stdout"]

    stage["stderr"] = result["stderr"]

    ir_path = temp_path / "original.ll"

    if result["error"]:

        stage["status"] = "Failed"

        stage["message"] = result["error"]

    elif result["returncode"] != 0 or not ir_path.is_file():

        stage["status"] = "Failed"

        stage["message"] = (

            "Clang could not convert the C source to LLVM IR "

            f"(exit code {result['returncode']})."

        )

    else:

        try:

            run["original_ir"] = ir_path.read_text(

                encoding="utf-8", errors="replace"

            )

        except OSError as exc:

            stage["status"] = "Failed"

            stage["message"] = f"Could not read the generated IR: {exc}"

            return

        if not run["original_ir"].strip():

            run["original_ir"] = None

            stage["status"] = "Failed"

            stage["message"] = "Clang produced an empty IR file."

        else:

            stage["status"] = "Passed"

            stage["message"] = "LLVM IR was generated by Clang."

def stage_custom_transformations(run, temp_path):

    stage = run["custom"]

    if simplify_supported_identities is None:

        stage["status"] = "Unavailable"

        stage["message"] = (

            "The ExplainOpt-X identity-rewrite engine could not be "

            "imported. See the import details below."

        )

        return

    try:

        outcome = simplify_supported_identities(run["original_ir"])

        candidate_ir, changes = outcome

    except Exception as exc:

        stage["status"] = "Failed"

        stage["message"] = (

            "The ExplainOpt-X engine raised an error: "

            f"{type(exc).__name__}: {exc}"

        )

        stage["trace"] = traceback.format_exc()

        return

    try:

        normalized = [normalize_change(change) for change in (changes or [])]

    except Exception as exc:

        stage["status"] = "Failed"

        stage["message"] = (

            f"The engine's result objects could not be read: {exc}"

        )

        stage["trace"] = traceback.format_exc()

        return

    if not isinstance(candidate_ir, str):

        stage["status"] = "Failed"

        stage["message"] = (

            "The engine returned a candidate IR that is not text."

        )

        return

    applied = [c for c in normalized if c["applied"] is True]

    stage["changes"] = normalized

    stage["applied_count"] = len(applied)

    stage["status"] = "Passed"

    if applied:

        run["candidate_ir"] = candidate_ir

        stage["message"] = (

            f"The engine ran; {len(applied)} transformation(s) applied."

        )

    else:

        run["candidate_ir"] = None

        stage["message"] = (

            "The engine ran; no transformation was applied. "

            "The original IR is retained."

        )

    if run["candidate_ir"] is not None:

        run["candidate_validation"] = run_validation(

            run["candidate_ir"], "explainopt_candidate.ll", temp_path

        )

    else:

        run["candidate_validation"] = empty_validation(

            "Skipped",

            "No candidate IR exists because no custom transformation "

            "was applied.",

        )

def stage_llvm_o2(run, temp_path):

    stage = run["o2"]

    if optimize_llvm_ir is None:

        stage["status"] = "Unavailable"

        stage["message"] = (

            "The project's LLVM optimizer helper could not be imported. "

            "See the import details below."

        )

        return

    keyword_arguments = {"level": "O2"}

    opt_parameter = find_opt_parameter(optimize_llvm_ir)

    if opt_parameter:

        keyword_arguments[opt_parameter] = str(OPT)

        stage["opt_note"] = (

            f"The configured opt.exe path was passed as '{opt_parameter}'."

        )

    else:

        stage["opt_note"] = (

            "The project's helper has no opt-path parameter, so it uses "

            "its own opt.exe configuration; the OPT path in app.py is "

            "only used to report the opt version."

        )

    try:

        outcome = optimize_llvm_ir(run["original_ir"], **keyword_arguments)

        optimized_ir, error = outcome

    except Exception as exc:

        stage["status"] = "Failed"

        stage["message"] = (

            "The LLVM optimizer helper raised an error: "

            f"{type(exc).__name__}: {exc}"

        )

        stage["trace"] = traceback.format_exc()

        return

    if error:

        stage["status"] = "Failed"

        stage["message"] = f"LLVM optimization failed: {error}"

        return

    if not isinstance(optimized_ir, str) or not optimized_ir.strip():

        stage["status"] = "Failed"

        stage["message"] = "LLVM returned empty optimized IR."

        return

    run["o2_ir"] = optimized_ir

    stage["status"] = "Passed"

    stage["message"] = "LLVM O2 produced optimized IR."

    stage["differs"] = (

        optimized_ir.strip() != run["original_ir"].strip()

    )

    run["o2_validation"] = run_validation(

        optimized_ir, "llvm_optimized.ll", temp_path

    )

def run_optimization(source):

    """Run the whole workflow for the current source only."""

    run = {

        "source": source,

        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),

        "checks": [],

        "ir_generation": {

            "status": "Not run", "message": "", "stdout": "", "stderr": "",

        },

        "original_ir": None,

        "custom": {

            "status": "Not run", "message": "", "changes": [],

            "applied_count": 0, "trace": None,

        },

        "candidate_ir": None,

        "candidate_validation": empty_validation("Not run", ""),

        "o2": {

            "status": "Not run", "message": "", "differs": None,

            "opt_note": "", "trace": None,

        },

        "o2_ir": None,

        "o2_validation": empty_validation("Not run", ""),

        "tool_versions": {},

        "metrics": {},

    }

    run["checks"] = run_pattern_checks(source)

    run["tool_versions"] = {

        "Clang": tool_version(CLANG),

        "opt": tool_version(OPT),

    }

    try:

        with tempfile.TemporaryDirectory(

            prefix="explainopt_optimize_"

        ) as temp_dir:

            temp_path = Path(temp_dir)

            stage_generate_ir(run, temp_path)

            if run["original_ir"]:

                stage_custom_transformations(run, temp_path)

                stage_llvm_o2(run, temp_path)

            else:

                reason = (

                    "Skipped because LLVM IR was not generated."

                )

                run["custom"]["status"] = "Skipped"

                run["custom"]["message"] = reason

                run["candidate_validation"] = empty_validation(

                    "Skipped", reason

                )

                run["o2"]["status"] = "Skipped"

                run["o2"]["message"] = reason

                run["o2_validation"] = empty_validation("Skipped", reason)

    except OSError as exc:

        run["ir_generation"]["status"] = "Failed"

        run["ir_generation"]["message"] = (

            f"File error during optimization: {exc}"

        )

    if run["original_ir"]:

        run["metrics"]["Original IR (clang -O0)"] = ir_metrics(

            run["original_ir"]

        )

    if run["candidate_ir"]:

        run["metrics"]["ExplainOpt-X candidate IR"] = ir_metrics(

            run["candidate_ir"]

        )

    if run["o2_ir"]:

        run["metrics"]["LLVM O2 IR"] = ir_metrics(run["o2_ir"])

    return run

# ---------------------------------------------------------------------

# Status summary shared by the page and the HTML report

# ---------------------------------------------------------------------

def build_stage_rows(run, compile_state):

    checks = run["checks"]

    errored = [c for c in checks if c["state"] == "Pattern error"]

    detected = [c for c in checks if c["detected"] is True]

    if compile_state is not None and compile_state["source"] == run["source"]:

        compile_status = compile_state["status"]

        compile_detail = compile_state["message"]

    else:

        compile_status = "Not run"

        compile_detail = (

            "Press 'Compile C code' to build an executable for this source."

        )

    custom = run["custom"]

    o2 = run["o2"]

    if o2["status"] == "Passed":

        o2_detail = o2["message"]

        if o2["differs"] is True:

            o2_detail += " The output text differs from the input IR."

        elif o2["differs"] is False:

            o2_detail += " The output text is identical to the input IR."

    else:

        o2_detail = o2["message"]

    cand = run["candidate_validation"]

    o2v = run["o2_validation"]

    return [

        ("C compilation (executable)", compile_status, compile_detail),

        (

            "LLVM IR generation",

            run["ir_generation"]["status"],

            run["ir_generation"]["message"],

        ),

        (

            "Source-pattern checks (12)",

            "Passed" if errored else "Passed",

            f"{len(detected)} of {len(checks)} patterns detected"

            + (f"; {len(errored)} check(s) had invalid patterns"

               if errored else "")

            + ". Detection is not optimization.",

        ),

        (

            "ExplainOpt-X transformation processing",

            custom["status"],

            custom["message"],

        ),

        ("Candidate structural validation", cand["structural"], cand["message"]),

        ("Candidate LLVM validation", cand["llvm"], cand["message"]),

        ("LLVM O2 optimization", o2["status"], o2_detail),

        ("O2 structural validation", o2v["structural"], o2v["message"]),

        ("O2 LLVM validation", o2v["llvm"], o2v["message"]),

        ("Runtime measurement", "Not run", RUNTIME_NOT_MEASURED),

    ]


def build_overall_summary(run):
    lines = []

    gen = run["ir_generation"]
    custom = run["custom"]
    o2 = run["o2"]

    # 1. Source-to-IR conversion
    if gen["status"] == "Passed":
        lines.append(
            "The submitted C source code was successfully converted "
            "into LLVM Intermediate Representation (IR)."
        )
    else:
        lines.append(
            f"LLVM IR generation was unsuccessful: "
            f"{gen.get('message', gen['status'])}."
        )

    # 2. ExplainOpt-X custom optimization
    if custom["status"] == "Passed":
        count = custom.get("applied_count", 0)

        if count > 0:
            lines.append(
                f"ExplainOpt-X applied {count} custom identity "
                "transformation(s) to simplify eligible IR expressions."
            )
        else:
            lines.append(
                "ExplainOpt-X completed its custom optimization stage, "
                "but no applicable identity transformations were made."
            )
    else:
        lines.append(
            "The ExplainOpt-X custom optimization stage did not complete "
            f"successfully (status: {custom['status']})."
        )

    # 3. LLVM O2 optimization
    if o2["status"] == "Passed":
        lines.append(
            "LLVM's O2 optimization pipeline completed successfully "
            "and generated optimized LLVM IR."
        )
    else:
        lines.append(
            f"LLVM O2 optimization did not complete successfully "
            f"(status: {o2['status']})."
        )

    # 4. IR validation
    validations = [
        ("Candidate IR", run["candidate_validation"]),
        ("LLVM O2 IR", run["o2_validation"]),
    ]

    passed = [
        name for name, result in validations
        if result.get("status") == "Passed"
    ]
    failed = [
        name for name, result in validations
        if result.get("status") != "Passed"
    ]

    if not failed:
        lines.append(
            "Validation passed for both the candidate IR and "
            "the LLVM O2 output IR."
        )
    else:
        lines.append(
            f"IR validation passed for {len(passed)} of "
            f"{len(validations)} stages."
        )
        for name, result in validations:
            if result.get("status") != "Passed":
                lines.append(
                    f"{name} validation: {result.get('status', 'Unknown')}."
                )

    # 5. Source-pattern analysis
    checks = run.get("checks", [])
    detected = sum(
        1 for check in checks
        if check.get("detected") is True
    )

    lines.append(
        f"Source analysis identified 12 supported "
        "optimization patterns. Pattern detection indicates potential "
        "opportunities and does not, by itself, prove that a "
        "transformation was applied."
    )

    

    return lines


# ---------------------------------------------------------------------

# Standalone HTML report (current run only)

# ---------------------------------------------------------------------

REPORT_CSS = """

body { font-family: Arial, Helvetica, sans-serif; max-width: 1000px;

       margin: 32px auto; padding: 0 22px; color: #202b3c;

       line-height: 1.6; }

header { background: #173650; color: white; padding: 25px;

         border-radius: 12px; }

header h1 { margin-top: 0; }

h2 { margin-top: 30px; padding-bottom: 7px;

     border-bottom: 1px solid #d9e0e7; }

table { border-collapse: collapse; width: 100%; margin: 12px 0 20px; }

th, td { border: 1px solid #d9e0e7; padding: 10px; text-align: left;

         overflow-wrap: anywhere; vertical-align: top; }

th { background: #edf2f7; }

pre { background: #f3f5f7; padding: 12px; border-radius: 6px;

      white-space: pre-wrap; overflow-wrap: anywhere; }

details { margin: 10px 0; }

summary { cursor: pointer; font-weight: bold; }

.change { border: 1px solid #d9e0e7; padding: 14px; margin: 14px 0;

          border-radius: 8px; }

.note { background: #fff7df; padding: 14px; border-radius: 8px; }

footer { margin-top: 35px; color: #64748b; font-size: 0.9rem; }

@media print { body { margin: 0; max-width: none; }

               .change, table { break-inside: avoid; } }

"""

def html_table(headers, rows):

    head = "".join(f"<th>{safe(h)}</th>" for h in headers)

    body = "".join(

        "<tr>" + "".join(f"<td>{safe(cell)}</td>" for cell in row) + "</tr>"

        for row in rows

    )

    return f"<table><tr>{head}</tr>{body}</table>"

def build_current_run_report(run, compile_state):

    parts = []

    # 1. Summary

    summary_items = "".join(

        f"<li>{safe(line)}</li>" for line in build_overall_summary(run)

    )

    parts.append(f"<h2>1. Summary of this run</h2><ul>{summary_items}</ul>")

    # 2. Source

    parts.append(

        "<h2>2. Submitted C source</h2>"

        f"<pre>{safe(run['source'])}</pre>"

    )

    # 3. Stage status

    stage_rows = [

        (name, status, detail)

        for name, status, detail in build_stage_rows(run, compile_state)

    ]

    parts.append(

        "<h2>3. Stage status</h2>"

        + html_table(["Stage", "Status", "Details"], stage_rows)

    )

    gen = run["ir_generation"]

    if gen["stderr"].strip():

        parts.append(

            "<details><summary>Clang diagnostics during IR generation"

            f"</summary><pre>{safe(gen['stderr'])}</pre></details>"

        )

    # 4. The 12 checks

    check_rows = [

        (

            c["number"], c["name"], c["description"], c["state"],

            c["location"], c["implementation"], c["meaning"],

        )

        for c in run["checks"]

    ]

    parts.append(

        "<h2>4. The 12 optimization checks</h2>"

        "<p>This table reports source-pattern detection only. It does not "

        "mean LLVM or ExplainOpt-X applied the optimization.</p>"

        + html_table(

            [

                "#", "Check", "Description", "Result", "First match",

                "Implementation", "Meaning",

            ],

            check_rows,

        )

    )

    # 5. Transformations

    custom = run["custom"]

    section = [

        "<h2>5. ExplainOpt-X transformations</h2>",

        f"<p>Status: {safe(custom['status'])}. {safe(custom['message'])}</p>",

    ]

    for index, change in enumerate(custom["changes"], start=1):

        applied_text = {True: "Yes", False: "No"}.get(

            change["applied"], "Not reported"

        )

        section.append(

            '<section class="change">'

            f"<h3>{index}. {safe(change['transformation'])}</h3>"

            f"<p><strong>Applied:</strong> {safe(applied_text)}</p>"

            "<p><strong>Line:</strong> "

            f"{safe(change['line_number'] if change['line_number'] is not None else 'Not recorded')}</p>"

            "<p><strong>Original instruction</strong></p>"

            f"<pre>{safe(change['original_instruction'])}</pre>"

            "<p><strong>Replacement details</strong></p>"

            f"<pre>{safe(change['replacement_instruction'])}</pre>"

            "<p><strong>Why it is valid (as reported by the engine):"

            f"</strong> {safe(change['reason'] or 'No reason recorded.')}</p>"

            "</section>"

        )

    section.append(f"<p>{safe(GENERAL_BENEFIT)}</p>")

    parts.append("".join(section))

    # 6. IR

    metric_rows = [

        (name, m["lines"], m["functions"], m["instructions"], m["characters"])

        for name, m in run["metrics"].items()

    ]

    ir_section = ["<h2>6. LLVM IR</h2>"]

    if metric_rows:

        ir_section.append(

            "<p>Static IR size (instruction count is approximate). "

            "This is not a runtime measurement.</p>"

            + html_table(

                ["Version", "Lines", "Functions", "Approx. instructions",

                 "Characters"],

                metric_rows,

            )

        )

    else:

        ir_section.append("<p>No IR is available for this run.</p>")

    for title, text in (

        ("Original LLVM IR", run["original_ir"]),

        ("ExplainOpt-X candidate IR", run["candidate_ir"]),

        ("LLVM O2 optimized IR", run["o2_ir"]),

    ):

        if text:

            ir_section.append(

                f"<details><summary>{safe(title)}</summary>"

                f"<pre>{safe(text)}</pre></details>"

            )

        else:

            ir_section.append(f"<p>{safe(title)}: not available.</p>")

    parts.append("".join(ir_section))

    # 7. Validation

    validation_rows = []

    for label, validation in (

        ("ExplainOpt-X candidate", run["candidate_validation"]),

        ("LLVM O2 output", run["o2_validation"]),

    ):

        validation_rows.append(

            (

                label, validation["structural"], validation["llvm"],

                validation["message"],

            )

        )

    parts.append(

        "<h2>7. Validation</h2>"

        + html_table(

            ["IR", "Structural validation", "LLVM validation", "Message"],

            validation_rows,

        )

        + '<div class="note">Structural and LLVM validation show that the '

        "IR is well-formed. They are not proof of semantic equivalence, "

        "and this report does not claim formal correctness.</div>"

    )

    # 8. Runtime

    parts.append(

        "<h2>8. Runtime measurements</h2>"

        f"<p>{safe(RUNTIME_NOT_MEASURED)}</p>"

    )

    # 9. Benefits and limitations

    limitation_items = "".join(

        f"<li>{safe(item)}</li>" for item in GENERAL_LIMITATIONS

    )

    parts.append(

        "<h2>9. Expected benefits and limitations</h2>"

        f"<p>{safe(GENERAL_BENEFIT)}</p>"

        "<p>General notes (written for this application, not produced by "

        "the engine):</p>"

        f"<ul>{limitation_items}</ul>"

    )

    # 10. Tool versions

    version_rows = list(run["tool_versions"].items())

    parts.append(

        "<h2>10. Tool versions</h2>"

        + html_table(["Tool", "Version output"], version_rows)

    )

    # 11. Convert LLVM O2 IR to C when LLVM-CBE is installed.
    decompiled_c, decompiler_status = decompile_optimized_ir_to_c(run.get("o2_ir") or "")
    if decompiled_c.strip():
        final_c = decompiled_c
        final_c_note = (
            "This C-like output was generated from the actual LLVM O2 IR by LLVM-CBE. "
            "Decompilation can change names and structure; review and compile it before relying on it."
        )
    else:
        final_c = ""
        final_c_note = (
            "A true LLVM IR-to-C conversion was unavailable for this run. " + decompiler_status +
            " The actual LLVM O2 IR remains available in section 6."
        )
    parts.append(
        "<h2>11. Final Optimized Code (LLVM IR to readable C)</h2>"
        + f"<p>{safe(final_c_note)}</p>"
        + (f"<pre>{safe(final_c)}</pre>" if final_c.strip()
           else "<p>Readable C output is not available until LLVM-CBE is installed and compatible with the emitted IR.</p>")
    )

    # 12. Explanation of recorded transformations
    explanation_rows = []
    for change in run.get("custom", {}).get("changes", []):
        if change.get("applied") is not True:
            continue
        explanation_rows.append((
            change.get("line_number") if change.get("line_number") is not None
            else "Not recorded",
            change.get("original_instruction") or "Not recorded",
            change.get("replacement_instruction") or "Not recorded",
            change.get("reason") or change.get("transformation")
            or "The engine did not record a reason.",
        ))

    explanation_intro = (
        "<h2>12. Explanation</h2>"
        "<p>These rows describe transformations explicitly reported by the "
        "ExplainOpt-X engine. Its line number refers to transformation metadata "
        "(typically LLVM IR), not necessarily a line in the submitted C source. "
        "LLVM-CBE converts IR to C but does not itself provide a reliable explanation "
        "for each optimization, so only recorded transformations are explained here.</p>"
    )
    if explanation_rows:
        parts.append(
            explanation_intro
            + html_table(
                [
                    "Recorded line",
                    "Original expression / instruction",
                    "Replacement",
                    "Why this decision was taken",
                ],
                explanation_rows,
            )
        )
    else:
        parts.append(
            explanation_intro
            + "<p>No applied ExplainOpt-X transformations with explanation "
              "metadata were recorded for this run. LLVM O2 may still have "
              "optimized the IR; this app does not infer source-level edits "
              "from LLVM IR.</p>"
        )

    body = "\n".join(parts)

    return (

        "<!DOCTYPE html>\n"

        '<html lang="en">\n<head>\n<meta charset="UTF-8">\n'

        '<meta name="viewport" content="width=device-width, '

        'initial-scale=1">\n'

        "<title>ExplainOpt-X Current-Run Report</title>\n"

        f"<style>{REPORT_CSS}</style>\n</head>\n<body>\n"

        "<header>ExplainOpt-X"

        "Counterfactual & Semantics-Aware Explainable Compiler "

        "Optimization"

        f"<p>Report for the current run only · generated "

        f"{safe(run['generated_at'])}</p></header>\n"

        f"{body}\n"

        "<footer>Generated by ExplainOpt-X from the current C source and "

        "current optimization run. No previously saved reports are "

        "included.</footer>\n</body>\n</html>\n"

    )

# ---------------------------------------------------------------------

# Rendering helpers

# ---------------------------------------------------------------------

def render_stage_message(stage, label):

    status = stage["status"]

    message = stage["message"] or ""

    if status == "Passed":

        st.success(f"{label}: {message}")

    elif status == "Failed":

        st.error(f"{label}: {message}")

    elif status == "Unavailable":

        st.warning(f"{label}: {message}")

    elif status == "Skipped":

        st.info(f"{label}: {message}")

    if stage.get("trace"):

        with st.expander(f"Technical details for {label}"):

            st.code(stage["trace"], language="text")

def render_validation(validation, label):

    left, right = st.columns(2)

    left.metric(f"{label} structural validation", status_label(validation["structural"]))

    right.metric(f"{label} LLVM validation", status_label(validation["llvm"]))

    if validation["status"] in ("Unavailable", "Skipped") and validation["message"]:

        st.caption(validation["message"])

    if validation.get("trace"):

        with st.expander(f"Technical details for {label} validation"):

            st.code(validation["trace"], language="text")

    if validation.get("raw") is not None:

        with st.expander(f"Raw validate_ir result ({label})"):

            st.json(validation["raw"])

def render_compile_result(state):

    status = state["status"]

    if state["stdout"].strip():

        st.markdown("Compiler output")

        st.code(state["stdout"], language="text")

    if state["stderr"].strip():

        st.markdown("Compiler diagnostics")

        st.code(state["stderr"], language="text")

    if status == "Passed":

        if state["stderr"].strip():

            st.success("Compilation succeeded (see the diagnostics above).")

        else:

            st.success("Compilation succeeded.")

        st.download_button(

            "Download compiled executable",

            data=state["executable"],

            file_name="explainopt_program.exe",

            mime="application/octet-stream",

            key="download_c_executable",

        )

        st.caption(

            "The executable is available for download only. "

            "ExplainOpt-X does not run it."

        )

    elif status == "Failed":

        st.error(state["message"])

        if not state["stderr"].strip() and not state["stdout"].strip():

            st.info(

                "Clang returned no diagnostic text. Check the compiler "

                "installation and the Windows linker configuration."

            )

    elif status == "Unavailable":

        st.error(state["message"])

    else:

        st.warning(state["message"])

def render_checks(run):

    st.subheader("1. All 12 optimization checks")

    show_table(

        [

            {

                "#": c["number"],

                "Check": c["name"],

                "Description": c["description"],

                "Detected in C source": c["state"],

                "First match": c["location"],

                "Implementation status": c["implementation"],

                "Meaning of the result": c["meaning"],

            }

            for c in run["checks"]

        ]

    )

    st.caption(

        "This table reports source-pattern detection only. A detected "

        "pattern is not an applied optimization: custom rewrites are "

        "reported in section 2 and LLVM O2 output in section 3. "

        "Comments and string literals are ignored by the checks."

    )

def render_custom(run):

    st.subheader("2. ExplainOpt-X custom transformations (Candidate A)")

    custom = run["custom"]

    render_stage_message(custom, "Transformation processing")

    if custom["status"] != "Passed":

        return

    if custom["changes"]:

        show_table(

            [

                {

                    "Transformation": c["transformation"],

                    "Applied": {True: "Yes", False: "No"}.get(

                        c["applied"], "Not reported"

                    ),

                    "Line": (

                        c["line_number"]

                        if c["line_number"] is not None else "—"

                    ),

                    "Reason": c["reason"] or "No reason recorded",

                }

                for c in custom["changes"]

            ]

        )

        for index, change in enumerate(custom["changes"], start=1):

            applied = change["applied"] is True

            title = (

                f"{index}. {change['transformation']} — "

                f"{'applied' if applied else 'not applied'}"

            )

            with st.expander(title, expanded=applied):

                if change["line_number"] is not None:

                    st.write(f"Location: line {change['line_number']}")

                if change["original_instruction"]:

                    st.markdown("Original instruction")

                    st.code(str(change["original_instruction"]), language="llvm")

                if change["replacement_instruction"]:

                    st.markdown("Replacement details")

                    st.write(str(change["replacement_instruction"]))

                st.markdown(

                    "Why it is valid (as reported by the engine): "

                    + (change["reason"] or "No reason was recorded.")

                )

        st.info(GENERAL_BENEFIT)

        with st.expander("Limitations of identity rewrites"):

            for item in GENERAL_LIMITATIONS:

                st.write(f"- {item}")

    else:

        st.info("No supported identity patterns were reported.")

    if run["candidate_ir"] is not None:

        st.markdown("Candidate IR validation")

        render_validation(run["candidate_validation"], "Candidate")

        st.download_button(

            "Download ExplainOpt-X candidate IR",

            data=run["candidate_ir"],

            file_name="explainopt_candidate.ll",

            mime="text/plain",

            key="download_candidate_ir",

        )

        with st.expander("View ExplainOpt-X candidate LLVM IR"):

            st.code(run["candidate_ir"], language="llvm")

    else:

        st.info(

            "No custom identity rewrite was applied; the original IR is "

            "retained."

        )

def render_o2(run):

    st.subheader("3. LLVM O2 optimization (Candidate B)")

    o2 = run["o2"]

    render_stage_message(o2, "LLVM O2")

    if o2.get("opt_note"):

        st.caption(o2["opt_note"])

    if o2["status"] != "Passed":

        return

    if o2["differs"] is False:

        st.info(

            "The O2 output text is identical to the input IR. LLVM left "

            "this IR unchanged."

        )

    elif o2["differs"] is True:

        st.caption(

            "The O2 output text differs from the input IR. This does not "

            "identify which passes ran."

        )

    render_validation(run["o2_validation"], "LLVM O2")

    st.download_button(

        "Download LLVM O2 optimized IR",

        data=run["o2_ir"],

        file_name="explainopt_llvm_o2.ll",

        mime="text/plain",

        key="download_llvm_optimized_ir",

    )

def render_ir_comparison(run):

    st.subheader("4. Original vs LLVM O2 IR")

    if not run["original_ir"]:

        st.info("No IR is available to display.")

        return

    st.download_button(

        "Download original LLVM IR",

        data=run["original_ir"],

        file_name="original.ll",

        mime="text/plain",

        key="download_original_ir",

    )

    if run["o2_ir"]:

        left, right = st.columns(2)

        with left:

            st.markdown("Original LLVM IR")

            with scroll_container():

                st.code(run["original_ir"], language="llvm")

        with right:

            st.markdown("LLVM O2 optimized IR")

            with scroll_container():

                st.code(run["o2_ir"], language="llvm")

        with st.expander("Line differences: original → LLVM O2"):

            st.code(

                ir_diff(

                    run["original_ir"], run["o2_ir"],

                    "original.ll", "llvm_o2.ll",

                ),

                language="diff",

            )

    else:

        st.info(

            "LLVM O2 IR is not available, so only the original IR is shown."

        )

        with st.expander("View original LLVM IR", expanded=True):

            st.code(run["original_ir"], language="llvm")

    if run["candidate_ir"]:

        with st.expander("Line differences: original → ExplainOpt-X candidate"):

            st.code(

                ir_diff(

                    run["original_ir"], run["candidate_ir"],

                    "original.ll", "explainopt_candidate.ll",

                ),

                language="diff",

            )

def render_metrics(run):

    st.subheader("5. Static IR size and runtime")

    if run["metrics"]:

        show_table(

            [

                {

                    "Version": name,

                    "Lines": m["lines"],

                    "Functions": m["functions"],

                    "Approx. instructions": m["instructions"],

                    "Characters": m["characters"],

                }

                for name, m in run["metrics"].items()

            ]

        )

        frame = pd.DataFrame(

            [

                {"Version": name, "Approx. instructions": m["instructions"]}

                for name, m in run["metrics"].items()

            ]

        )

        chart = (

            alt.Chart(frame)

            .mark_bar(cornerRadiusEnd=5)

            .encode(

                x=alt.X("Version:N", sort=None, title=None),

                y=alt.Y(

                    "Approx. instructions:Q",

                    title="Approx. IR instructions",

                    scale=alt.Scale(zero=True),

                ),

                tooltip=["Version", "Approx. instructions"],

            )

            .properties(height=260)

        )

        show_chart(chart)

        st.caption(

            "Static IR size from the current run (approximate instruction "

            "count). It is not a runtime measurement."

        )

    else:

        st.info("No IR metrics are available for this run.")

    st.info(RUNTIME_NOT_MEASURED)

def render_validation_summary(run, compile_state):

    st.subheader("Run summary")

    rows = build_stage_rows(run, compile_state)

    show_table(

        [

            {

                "Stage": name,

                "Status": status_label(status),

                "Details": detail,

            }

            for name, status, detail in rows

        ]

    )

    st.caption(

        "Structural validation and LLVM validation do not prove semantic "

        "equivalence. 'Unavailable' means the check could not be "

        "completed, which is different from failing."

    )

def render_run(run, compile_state):

    gen = run["ir_generation"]

    render_validation_summary(run, compile_state)

    if PROJECT_IMPORT_ERRORS:

        with st.expander("Project modules that could not be imported"):

            for name, trace in PROJECT_IMPORT_ERRORS.items():

                st.warning(name)

                st.code(trace, language="text")

    if gen["status"] != "Passed":

        render_stage_message(gen, "LLVM IR generation")

        if gen["stderr"].strip():

            st.markdown("Clang diagnostics")

            st.code(gen["stderr"], language="text")

        st.info(

            "The 12 source-pattern checks below do not need LLVM IR and "

            "are still available."

        )

    elif gen["stderr"].strip():

        with st.expander("Clang diagnostics during IR generation"):

            st.code(gen["stderr"], language="text")

    st.divider()

    render_checks(run)

    if run["original_ir"]:

        st.divider()

        render_custom(run)

        st.divider()

        render_o2(run)

        st.divider()

        render_ir_comparison(run)

        st.divider()

        render_metrics(run)

    with st.expander("Tool versions used in this run"):

        for tool, version in run["tool_versions"].items():

            st.write(f"{tool}: {version}")

        st.caption(

            "If opt cannot read the IR produced by Clang, check that "

            "Clang and opt come from compatible LLVM versions."

        )

    st.divider()

    st.subheader("Readable report for this run")

    st.write(

        "Download a standalone HTML report generated from the current "

        "C source and current run only."

    )

    st.download_button(

        label="Download readable HTML report",

        data=build_current_run_report(run, compile_state),

        file_name="explainopt_current_run_report.html",

        mime="text/html",

        key="download_current_run_report",

    )

    st.markdown("Overall summary")

    for line in build_overall_summary(run):

        st.write(f"- {line}")

# ---------------------------------------------------------------------

# C source editor and compiler

# ---------------------------------------------------------------------

st.header("C Compiler")

st.write(

    "Write C code or upload a .c file, then compile it with your "

    "local Clang installation. The app does not execute compiled programs."

)

uploaded_c = st.file_uploader(

    "Upload C source file",

    type=["c"],

    key="c_source_upload",

)

# Raw string: the C escape sequence \n must reach Clang as backslash + n.

DEFAULT_C_SOURCE = r'''#include <stdio.h>

int main(void) {

    printf("Hello from ExplainOpt-X!\n");

    return 0;

}

'''

initial_c = DEFAULT_C_SOURCE

if uploaded_c is not None:

    if uploaded_c.size > MAX_SOURCE_BYTES:

        st.error("The uploaded file is larger than 1 MB and was ignored.")

    else:

        try:

            initial_c = uploaded_c.getvalue().decode("utf-8-sig")

        except UnicodeDecodeError:

            st.error("The uploaded file is not valid UTF-8 text.")

# A new widget key allows a newly selected file to populate the editor.

source_key = (

    f"c_source_editor_{uploaded_c.name}_{uploaded_c.size}"

    if uploaded_c is not None

    else "c_source_editor_default"

)

c_source = st.text_area(

    "C source code",

    value=initial_c,

    height=280,

    key=source_key,

)

compile_col, compiler_col = st.columns([1, 2])

with compile_col:

    compile_button = st.button(

        "Compile C code",

        type="primary",

        key="compile_c_button",

    )

with compiler_col:

    st.caption(f"Clang executable: `{CLANG}`")

if compile_button:

    st.session_state["compile_run"] = compile_c_source(c_source)

compile_state = st.session_state.get("compile_run")

if compile_state is not None:

    if compile_state["source"] == c_source:

        render_compile_result(compile_state)

    else:

        st.info(

            "The source changed since the last compilation. "

            "Press 'Compile C code' to compile the current source."

        )

st.divider()

# ---------------------------------------------------------------------

# C source -> LLVM IR -> one-click optimization checks

# ---------------------------------------------------------------------

st.header("Optimize C code")

st.caption(

    "One click runs the 12 source-pattern checks, ExplainOpt-X's custom "

    "identity rewrites and LLVM's standard O2 pipeline. Pattern detection "

    "does not prove that any optimization was applied."

)

st.caption(f"opt executable: `{OPT}`")

optimize_button = st.button(

    "Analyze and optimize C code",

    type="primary",

    key="optimize_c_button",

)

if optimize_button:

    problem = validate_source(c_source)

    if problem:

        st.session_state["current_run"] = None

        st.warning(problem)

    else:

        with st.spinner("Generating IR, applying rewrites and running LLVM O2..."):

            try:

                st.session_state["current_run"] = run_optimization(c_source)

            except Exception as exc:

                st.session_state["current_run"] = None

                st.error(

                    "The optimization workflow stopped unexpectedly: "

                    f"{type(exc).__name__}: {exc}"

                )

                with st.expander("Technical details"):

                    st.code(traceback.format_exc(), language="text")

current_run = st.session_state.get("current_run")

if current_run is not None:
    if current_run["source"] == c_source:
        render_run(current_run, st.session_state.get("compile_run"))

        st.divider()
        st.divider()
        st.header("Local C Source Optimization")
        st.caption(
            "Applies local, rule-based C source transformations and reports all 12 "
            "optimization techniques. The results are added after the existing "
            "ExplainOpt-X and LLVM analysis sections."
        )

        if st.button(
            "Run local C optimizer on current source",
            key="run_local_c_optimizer",
            type="primary",
        ):
            try:
                import importlib.util

                local_optimizer_path = Path(__file__).resolve().parent / "localoptimiser.py"
                if not local_optimizer_path.is_file():
                    raise FileNotFoundError(
                        "Missing localoptimiser.py. Put it in the same folder as app.py."
                    )

                local_spec = importlib.util.spec_from_file_location(
                    "localoptimiser", str(local_optimizer_path)
                )
                if local_spec is None or local_spec.loader is None:
                    raise ImportError(f"Could not load {local_optimizer_path}")

                local_module = importlib.util.module_from_spec(local_spec)
                sys.modules[local_spec.name] = local_module
                local_spec.loader.exec_module(local_module)

                with st.spinner("Applying local C optimization rules..."):
                    optimization_start = time.perf_counter()
                    optimizer_result = local_module.optimize_c_code(c_source)
                    optimization_latency_seconds = time.perf_counter() - optimization_start
                    dashboard_data = local_module.build_dashboard_data(
                        c_source, optimizer_result
                    )

                st.session_state["explainopt_local_result"] = {
                    "result": optimizer_result,
                    "dashboard": dashboard_data,
                    "optimization_latency_seconds": optimization_latency_seconds,
                }
                st.session_state["explainopt_local_source"] = c_source

            except Exception as exc:
                st.error(f"Local C optimization failed: {type(exc).__name__}: {exc}")
                with st.expander("Technical details"):
                    st.code(traceback.format_exc(), language="text")

        local_result = st.session_state.get("explainopt_local_result")
        local_source = st.session_state.get("explainopt_local_source")

        if local_result and local_source == c_source:
            dashboard = local_result["dashboard"]
            summary = dashboard["summary"]

            st.subheader("Optimized C code")
            st.code(dashboard["optimized_code"], language="c")
            st.download_button(
                "Download optimized C",
                data=dashboard["optimized_code"].rstrip() + "\\n",
                file_name="optimized.c",
                mime="text/x-c",
                key="download_local_optimized_c",
            )

            st.subheader("Optimization summary")
            metric_columns = st.columns(4)
            metric_columns[0].metric(
                "Techniques analyzed", summary["techniques_analyzed"]
            )
            metric_columns[1].metric(
                "Successfully applied", summary["successfully_applied"]
            )
            metric_columns[2].metric(
                "Not applicable", summary["not_applicable"]
            )
            metric_columns[3].metric(
                "Lines removed", summary["lines_removed"]
            )

            line_columns = st.columns(3)
            line_columns[0].metric("Original lines", summary["original_lines"])
            line_columns[1].metric("Optimized lines", summary["optimized_lines"])
            line_columns[2].metric("Lines added", summary["lines_added"])

            breakdown = dashboard["breakdown"]
            chart_data = pd.DataFrame(
                [
                    {"Status": "Applied", "Count": breakdown.get("applied", 0)},
                    {
                        "Status": "Not applicable",
                        "Count": breakdown.get("not_applicable", 0),
                    },
                ]
            )
            st.subheader("Optimization breakdown")
            chart = (
                alt.Chart(chart_data)
                .mark_bar(cornerRadiusEnd=4)
                .encode(
                    x=alt.X("Status:N", sort=None, title=None),
                    y=alt.Y(
                        "Count:Q",
                        title="Number of techniques",
                        scale=alt.Scale(zero=True),
                    ),
                    color=alt.Color("Status:N", legend=None),
                    tooltip=["Status", "Count"],
                )
                .properties(height=220)
            )
            show_chart(chart)

            st.subheader("Optimization highlights")
            if dashboard["highlights"]:
                for highlight in dashboard["highlights"]:
                    with st.expander(
                        f"{highlight['label']} — "
                        f"lines {highlight['original_lines']} → "
                        f"{highlight['optimized_lines']}"
                    ):
                        st.write(highlight["change"])
                        st.caption(highlight["reason"])
            else:
                st.info(
                    "No supported transformation was applied to this input. "
                    "The complete results for all techniques are shown below."
                )

            if dashboard["observations"]:
                st.subheader("Key observations")
                for observation in dashboard["observations"]:
                    st.write(f"- {observation}")

            # Keep this report after the pre-existing compiler analysis sections.
            st.subheader("Detailed explanation table — all 12 techniques")
            explanation_df = pd.DataFrame(
                [
                    {
                        "Technique": item["technique"],
                        "Status": item["status"].replace("_", " ").title(),
                        "Original lines": item["original_lines"],
                        "Optimized lines": item["optimized_lines"],
                        "Change": item["change"],
                        "Reason": item["reason"],
                    }
                    for item in dashboard["optimizations"]
                ]
            )
            st.dataframe(
                explanation_df,
                use_container_width=True,
                hide_index=True,
            )

            st.divider()
            st.header("Performance Metrics")
            st.caption(
                "Benchmarks the current source and optimized output locally. Install GCC or Clang "
                "and make it available on PATH for compilation and runtime measurements. "
                "Results apply only to the test input and environment used here."
            )

            source_lines = len(c_source.splitlines())
            optimized_lines = len(dashboard["optimized_code"].splitlines())
            line_reduction = ((source_lines - optimized_lines) / source_lines * 100) if source_lines else 0.0
            latency_ms = local_result.get("optimization_latency_seconds")
            metric_cols = st.columns(4)
            metric_cols[0].metric("Optimization latency", f"{latency_ms * 1000:.3f} ms" if latency_ms is not None else "N/A")
            metric_cols[1].metric("Source-line reduction", f"{line_reduction:.2f}%")
            metric_cols[2].metric("Source lines removed", max(0, source_lines - optimized_lines))
            metric_cols[3].metric("Applied techniques", summary["successfully_applied"])

            st.markdown("**Benchmark settings**")
            compiler_choice = st.selectbox(
                "C compiler", ["Auto", "GCC", "Clang"], index=0,
                help="Auto selects GCC first, then Clang. LLVM opt.exe is not required for these benchmarks.",
                key="explainopt_benchmark_compiler",
            )
            benchmark_stdin = st.text_area(
                "Standard input for both programs (leave blank if not required)",
                value="", key="explainopt_benchmark_stdin", height=90,
            )
            benchmark_repeats = st.selectbox(
                "Runtime repetitions", [3, 5, 10], index=1,
                key="explainopt_benchmark_repeats",
                help="Each execution is limited to 5 seconds. Very short programs may have noisy timings.",
            )
            if st.button("Calculate performance metrics", key="calculate_explainopt_performance", type="primary"):
                try:
                    from performance_metrics import run_performance_benchmarks
                    with st.spinner("Compiling and benchmarking the original and optimized C programs..."):
                        performance = run_performance_benchmarks(
                            c_source, dashboard["optimized_code"], benchmark_stdin,
                            compiler_choice, int(benchmark_repeats),
                        )
                    st.session_state["explainopt_performance"] = {
                        "source": c_source,
                        "optimized_source": dashboard["optimized_code"],
                        "result": performance,
                    }
                except Exception as exc:
                    st.error(f"Performance measurement failed: {type(exc).__name__}: {exc}")
                    with st.expander("Technical details"):
                        st.code(traceback.format_exc(), language="text")

            saved_performance = st.session_state.get("explainopt_performance")
            if saved_performance and saved_performance.get("source") == c_source and saved_performance.get("optimized_source") == dashboard["optimized_code"]:
                performance = saved_performance["result"]
                if not performance.get("available"):
                    st.warning(performance.get("message", "Performance measurements are unavailable."))
                else:
                    st.caption(f"Compiler used: {performance['compiler']} · Measurements are local to this machine.")
                    runtime = performance.get("runtime_comparison")
                    if runtime:
                        perf_cols = st.columns(3)
                        improvement = runtime.get("execution_time_improvement_percent")
                        perf_cols[0].metric("Execution-time improvement", f"{improvement:.2f}%" if improvement is not None else "N/A", help="Positive means faster; negative means slower. Based on median of repeated -O0 runs.")
                        correctness = performance.get("correctness")
                        perf_cols[1].metric("Single-input correctness", f"{correctness['rate_percent']:.0f}%" if correctness else "N/A", help="Compares stdout, stderr, and exit status for one stdin scenario only.")
                        robustness = performance.get("robustness", {})
                        perf_cols[2].metric("Compile success rate", f"{robustness.get('configuration_compile_success_rate_percent', 0):.0f}%")
                        st.caption(f"Original median runtime: {runtime['original_median_seconds'] * 1000:.3f} ms · Optimized median runtime: {runtime['optimized_median_seconds'] * 1000:.3f} ms · Repeated-run timing variation: original {runtime['original_runtime_variation_percent']:.2f}%, optimized {runtime['optimized_runtime_variation_percent']:.2f}%.")
                        st.info(correctness["note"] if correctness else "Correctness could not be evaluated for this input.")
                    elif performance.get("runtime_error"):
                        st.warning(f"Runtime and correctness metrics unavailable: {performance['runtime_error']}")

                    st.subheader("Compiler comparison")
                    st.dataframe(pd.DataFrame(performance["configurations"]), use_container_width=True, hide_index=True)
                    st.caption("Configurations: original -O0, ExplainOpt-X output -O0, original -O2, original -O3, and ExplainOpt-X output -O2. Compilation times are reported only when compilation succeeds.")

                    st.subheader("Six-metric evaluation status")
                    correctness = performance.get("correctness")
                    robustness = performance.get("robustness", {})
                    metric_rows = [
                        {"Metric": "1. Execution-time improvement", "Result": (f"{runtime['execution_time_improvement_percent']:.2f}%" if runtime and runtime.get("execution_time_improvement_percent") is not None else "Not measured"), "Interpretation": "Median runtime comparison at -O0; can be noisy for short programs."},
                        {"Metric": "2. Optimization latency", "Result": f"{latency_ms * 1000:.3f} ms" if latency_ms is not None else "Not measured", "Interpretation": "Time spent in the local source optimizer only; excludes UI and benchmark compilation."},
                        {"Metric": "3. Code correctness rate", "Result": (f"{correctness['rate_percent']:.0f}% (1 test scenario)" if correctness else "Not measured"), "Interpretation": "One configured stdin scenario, output, stderr and exit status; not a formal equivalence proof."},
                        {"Metric": "4. Source code size change", "Result": f"{line_reduction:.2f}% reduction", "Interpretation": f"{source_lines} original physical lines versus {optimized_lines} optimized physical lines; line reduction does not prove speedup."},
                        {"Metric": "5. LLVM/compiler comparison", "Result": f"{sum(1 for row in performance['configurations'] if row['Compile success'] == 'Yes')}/5 configurations compiled", "Interpretation": "Comparison table above reports compile time and runtime for available configurations."},
                        {"Metric": "6. Optimization robustness", "Result": f"{robustness.get('configuration_compile_success_rate_percent', 0):.0f}% configuration compile success; {performance.get('repeats', 0)} repeats", "Interpretation": robustness.get("scope_note", "A multi-program benchmark suite is needed to assess robustness across categories.")},
                    ]
                    st.dataframe(pd.DataFrame(metric_rows), use_container_width=True, hide_index=True)
            else:
                st.info("Run **Calculate performance metrics** to compile both versions and measure runtime, correctness for the supplied input, and compiler comparison configurations.")

            st.caption(
                "These measurements are experimental and specific to the current source, test input, compiler, and machine. Compile and test the output before relying on it; a single passing input does not prove correctness for all inputs."
            )
        elif local_result and local_source != c_source:
            st.info(
                "The C source changed. Run the local C optimizer again to refresh its output."
            )
    else:
        st.info(
            "The C source changed since the last analysis. "
            "Press 'Analyze and optimize C code' to analyse the current "
            "source."
        )

st.caption("ExplainOpt-X · Local compiler optimization explainer")

