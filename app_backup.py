
from pathlib import Path
import html
import json
import streamlit as st
import pandas as pd
import altair as alt

st.set_page_config(
    page_title="ExplainOpt-X",
    page_icon="⚙️",
    layout="wide",
)

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results" / "counterfactual"
REPORT_FILES = sorted(RESULTS.glob("*.json")) if RESULTS.exists() else []

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
        <h1>ExplainOpt-X</h1>
        <p>Counterfactual &amp; Semantics-Aware Explainable Compiler Optimization</p>
    </div>
    """,
    unsafe_allow_html=True,
)

st.caption(
    "Research prototype · Experiment report viewer · "
    "Runtime measurements are observational, not proof of optimization."
)

# ---------------------------------------------------------------------
# Load experiment reports
# ---------------------------------------------------------------------

if not RESULTS.exists():
    st.error(f"Results directory not found: {RESULTS}")
    st.info(
        "Save app.py in your ExplainOpt-X project root, "
        "next to the src and results folders."
    )
    st.stop()

if not REPORT_FILES:
    st.warning("No JSON reports found in results/counterfactual.")
    st.write(
        "Run an experiment with your existing experiment_runner, "
        "then refresh this page."
    )
    st.code(
        r'python -m src.explainopt.counterfactual.experiment_runner '
        r'--input "samples\source\counterfactual_add_zero.ll" '
        r'--repeats 15 '
        r'--clang "C:\Program Files\LLVM\bin\clang.exe" '
        r'--output "results\counterfactual\add_zero_experiment.json"',
        language="powershell",
    )
    st.stop()

reports = []
load_errors = []

for report_file in REPORT_FILES:
    try:
        report_data = json.loads(
            report_file.read_text(encoding="utf-8")
        )
        if isinstance(report_data, dict):
            reports.append((report_file, report_data))
        else:
            load_errors.append(
                f"{report_file.name}: JSON root is not an object"
            )
    except (OSError, json.JSONDecodeError) as exc:
        load_errors.append(f"{report_file.name}: {exc}")

if load_errors:
    with st.expander("Reports that could not be loaded"):
        for error in load_errors:
            st.warning(error)

if not reports:
    st.error("No valid JSON experiment reports could be loaded.")
    st.stop()

# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def fmt_ms(value):
    """Format seconds as milliseconds."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{value * 1000:.3f} ms"
    return "N/A"


def get_median(report, key):
    measurement = report.get(key, {})
    if not isinstance(measurement, dict):
        return None

    value = measurement.get("median_seconds")
    if (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and value >= 0
    ):
        return value

    return None


def safe(value):
    """HTML-escape values before inserting them into the report."""
    if value is None:
        value = "Not recorded"
    return html.escape(str(value))


def report_value(value):
    """Show unknown checks honestly rather than treating them as passed."""
    if value is True:
        return "Passed"
    if value is False:
        return "Failed"
    return "Not recorded / not applicable"


def metric_text(value, suffix=""):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{value:.4f}{suffix}"
    return "N/A"


def get_validation_checks(report):
    validation = report.get("candidate_validation", {})
    if not isinstance(validation, dict):
        validation = {}

    return [
        (
            "Structural validation",
            report.get(
                "candidate_structural_validation_passed",
                validation.get("structural_validation_passed"),
            ),
        ),
        (
            "LLVM validation",
            report.get(
                "candidate_compilation_passed",
                validation.get("llvm_validation_passed"),
            ),
        ),
        (
            "Differential behavior",
            report.get("differential_behavior_check_passed"),
        ),
        (
            "Paired observations",
            report.get("paired_observations_match"),
        ),
        (
            "Formal equivalence",
            report.get("formal_equivalence_proven"),
        ),
    ]


def build_html_report(report_file, report):
    """Build a self-contained, readable HTML report from saved JSON data."""

    original = get_median(report, "original_measurement")
    candidate = get_median(report, "candidate_measurement")

    speedup = report.get(
        "speedup_original_over_candidate",
        report.get("speedup"),
    )
    reduction = report.get("runtime_reduction_percent")

    changes = report.get("applied_changes") or []
    if not isinstance(changes, list):
        changes = []

    applied_changes = [
        change
        for change in changes
        if isinstance(change, dict) and change.get("applied") is True
    ]

    change_sections = []

    for index, change in enumerate(applied_changes, start=1):
        change_sections.append(
            f"""
            <section class="change">
                <h3>{index}. {safe(change.get('transformation', 'Transformation'))}</h3>
                <p><strong>Line:</strong>
                    {safe(change.get('line_number', 'Not recorded'))}</p>
                <p><strong>Original instruction</strong></p>
                <pre>{safe(change.get('original_instruction', 'Not recorded'))}</pre>
                <p><strong>Replacement details</strong></p>
                <pre>{safe(change.get('replacement_instruction', 'Not recorded'))}</pre>
                <p><strong>Reason:</strong>
                    {safe(change.get('reason', 'Not recorded'))}</p>
            </section>
            """
        )

    if change_sections:
        changes_html = "\n".join(change_sections)
    else:
        changes_html = (
            "<p>No successfully applied transformation was recorded "
            "in this experiment.</p>"
        )

    check_rows = []
    for name, value in get_validation_checks(report):
        check_rows.append(
            f"<tr><td>{safe(name)}</td>"
            f"<td>{safe(report_value(value))}</td></tr>"
        )

    compiler_flags = report.get("compiler_flags", [])
    if isinstance(compiler_flags, list):
        compiler_flags_text = ", ".join(str(flag) for flag in compiler_flags)
    else:
        compiler_flags_text = str(compiler_flags or "Not recorded")

    repetitions = report.get(
        "repetitions_per_version",
        "Not recorded",
    )

    formal_proven = report.get("formal_equivalence_proven") is True
    transformation_applied = report.get("transformation_applied") is True

    if original is not None and candidate is not None:
        performance_conclusion = (
            "The candidate runtime was lower in this measurement."
            if candidate < original
            else "The candidate runtime was higher in this measurement."
            if candidate > original
            else "The measured median runtimes were equal."
        )
    else:
        performance_conclusion = (
            "A performance conclusion cannot be drawn because both "
            "runtime measurements are not available."
        )

    if not transformation_applied:
        transformation_conclusion = (
            "The report does not record a successfully applied candidate "
            "transformation."
        )
    else:
        transformation_conclusion = (
            "The report records that a transformation was applied. "
            "Review the validation evidence before drawing correctness "
            "conclusions."
        )

    formal_conclusion = (
        "The report marks formal equivalence as proven."
        if formal_proven
        else "Formal equivalence is not proven by this report."
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ExplainOpt-X Experiment Report</title>
<style>
    body {{
        font-family: Arial, Helvetica, sans-serif;
        max-width: 1000px;
        margin: 32px auto;
        padding: 0 22px;
        color: #202b3c;
        line-height: 1.6;
    }}
    header {{
        background: #173650;
        color: white;
        padding: 25px;
        border-radius: 12px;
    }}
    header h1 {{ margin-top: 0; }}
    h2 {{
        margin-top: 30px;
        padding-bottom: 7px;
        border-bottom: 1px solid #d9e0e7;
    }}
    table {{
        border-collapse: collapse;
        width: 100%;
        margin: 12px 0 20px;
    }}
    th, td {{
        border: 1px solid #d9e0e7;
        padding: 10px;
        text-align: left;
        overflow-wrap: anywhere;
    }}
    th {{ background: #edf2f7; }}
    pre {{
        background: #f3f5f7;
        padding: 12px;
        border-radius: 6px;
        white-space: pre-wrap;
        overflow-wrap: anywhere;
    }}
    .change {{
        border: 1px solid #d9e0e7;
        padding: 14px;
        margin: 14px 0;
        border-radius: 8px;
    }}
    .note {{
        background: #fff7df;
        padding: 14px;
        border-radius: 8px;
    }}
    footer {{
        margin-top: 35px;
        color: #64748b;
        font-size: 0.9rem;
    }}
    @media print {{
        body {{ margin: 0; max-width: none; }}
        .change, table {{ break-inside: avoid; }}
    }}
</style>
</head>
<body>
<header>
    <h1>ExplainOpt-X</h1>
    <p>Counterfactual &amp; Semantics-Aware Explainable Compiler Optimization</p>
    <p>Experiment report: {safe(report_file.name)}</p>
</header>

<h2>1. Experiment overview</h2>
<table>
<tr><th>Field</th><th>Result</th></tr>
<tr><td>Status</td><td>{safe(report.get('status', 'unknown'))}</td></tr>
<tr><td>Input file</td><td>{safe(report.get('input_file', 'Not recorded'))}</td></tr>
<tr><td>Transformation applied</td>
    <td>{'Yes' if transformation_applied else 'No / not recorded'}</td></tr>
<tr><td>Compiler</td><td>{safe(report.get('compiler', 'Not recorded'))}</td></tr>
<tr><td>Compiler flags</td><td>{safe(compiler_flags_text)}</td></tr>
<tr><td>Repetitions per version</td><td>{safe(repetitions)}</td></tr>
</table>

<h2>2. Performance comparison</h2>
<table>
<tr><th>Metric</th><th>Result</th></tr>
<tr><td>Original median runtime</td><td>{safe(fmt_ms(original))}</td></tr>
<tr><td>Candidate median runtime</td><td>{safe(fmt_ms(candidate))}</td></tr>
<tr><td>Reported speedup</td><td>{safe(metric_text(speedup, 'x'))}</td></tr>
<tr><td>Reported runtime reduction</td>
    <td>{safe(metric_text(reduction, '%'))}</td></tr>
</table>
<p>{safe(performance_conclusion)}</p>
<p>Runtime differences are observational. Small differences may reflect
measurement noise and do not establish a general speedup.</p>

<h2>3. Transformation details</h2>
<p>{safe(transformation_conclusion)}</p>
{changes_html}

<h2>4. Validation evidence</h2>
<table>
<tr><th>Check</th><th>Result</th></tr>
{''.join(check_rows)}
</table>

<h2>5. Conclusion and limitations</h2>
<p>{safe(formal_conclusion)}</p>
<div class="note">
    Compilation acceptance and behavioral tests are not, by themselves,
    proof of semantic equivalence. Results apply only to the checks and
    measurements recorded in this experiment. This report does not establish
    support for arbitrary source code or automatic optimization of every input.
</div>

<footer>
    Generated by ExplainOpt-X from an existing experiment JSON report.
</footer>
</body>
</html>
"""


# ---------------------------------------------------------------------
# Select experiment
# ---------------------------------------------------------------------

labels = [
    f"{report_file.name} · {data.get('status', 'unknown')}"
    for report_file, data in reports
]

chosen = st.selectbox(
    "Select an experiment report",
    range(len(reports)),
    format_func=lambda index: labels[index],
)

file, r = reports[chosen]

original = get_median(r, "original_measurement")
candidate = get_median(r, "candidate_measurement")

speedup = r.get(
    "speedup_original_over_candidate",
    r.get("speedup"),
)
reduction = r.get("runtime_reduction_percent")

changes = r.get("applied_changes") or []
if not isinstance(changes, list):
    changes = []

applied = [
    change
    for change in changes
    if isinstance(change, dict) and change.get("applied") is True
]

# ---------------------------------------------------------------------
# Downloadable readable report
# ---------------------------------------------------------------------

st.subheader("Readable experiment report")
st.write(
    "Download a standalone HTML report containing the experiment "
    "summary, performance measurements, transformation details, "
    "validation evidence, and limitations."
)

html_report = build_html_report(file, r)

st.download_button(
    label="Download readable HTML report",
    data=html_report,
    file_name=f"explainopt_report_{file.stem}.html",
    mime="text/html",
    key=f"download_html_{file.name}",
)

# ---------------------------------------------------------------------
# Experiment overview
# ---------------------------------------------------------------------

st.subheader("Experiment overview")

m1, m2, m3, m4 = st.columns(4)

m1.metric(
    "Status",
    str(r.get("status", "unknown")).replace("_", " ").title(),
)

m2.metric(
    "Transformation",
    "Applied" if r.get("transformation_applied") is True else "No candidate",
)

m3.metric(
    "Reported speedup",
    f"{speedup:.4f}×"
    if isinstance(speedup, (int, float)) and not isinstance(speedup, bool)
    else "N/A",
)

m4.metric(
    "Runtime change",
    f"{reduction:+.2f}%"
    if isinstance(reduction, (int, float)) and not isinstance(reduction, bool)
    else "N/A",
)

st.caption(f"Report: `{file.relative_to(ROOT)}`")

with st.expander("Input and experiment settings", expanded=False):
    st.write("**Input file:**", r.get("input_file", "Not recorded"))
    st.write("**Compiler:**", r.get("compiler", "Not recorded"))

    flags = r.get("compiler_flags", [])
    if isinstance(flags, list):
        st.write("**Compiler flags:**", ", ".join(map(str, flags)))
    else:
        st.write("**Compiler flags:**", flags or "Not recorded")

    st.write(
        "**Repetitions per version:**",
        r.get("repetitions_per_version", "Not recorded"),
    )

# ---------------------------------------------------------------------
# Runtime chart and correctness evidence
# ---------------------------------------------------------------------

left, right = st.columns([1.15, 1])

with left:
    st.subheader("Runtime comparison")

    if original is not None and candidate is not None:
        chart_df = pd.DataFrame(
            [
                {
                    "Version": "Original",
                    "Median runtime (ms)": original * 1000,
                },
                {
                    "Version": "Candidate",
                    "Median runtime (ms)": candidate * 1000,
                },
            ]
        )

        chart = (
            alt.Chart(chart_df)
            .mark_bar(cornerRadiusEnd=5)
            .encode(
                x=alt.X(
                    "Version:N",
                    sort=["Original", "Candidate"],
                    title=None,
                ),
                y=alt.Y(
                    "Median runtime (ms):Q",
                    title="Median runtime (ms)",
                    scale=alt.Scale(zero=True),
                ),
                tooltip=[
                    "Version",
                    alt.Tooltip(
                        "Median runtime (ms):Q",
                        format=".3f",
                    ),
                ],
            )
            .properties(height=280)
        )

        st.altair_chart(chart, width="stretch")

        a, b = st.columns(2)
        a.metric("Original median", fmt_ms(original))
        b.metric("Candidate median", fmt_ms(candidate))

        if candidate < original:
            st.success("Candidate median runtime was lower in this run.")
        elif candidate > original:
            st.warning("Candidate median runtime was higher in this run.")
        else:
            st.info("The measured median runtimes were equal.")

        st.caption(
            "Lower median is faster in this run. Small differences may "
            "be measurement noise."
        )
    else:
        st.info(
            "This report does not contain both valid runtime measurements."
        )

with right:
    st.subheader("Correctness evidence")

    for name, value in get_validation_checks(r):
        if value is True:
            st.success(f"{name}: Passed")
        elif value is False:
            st.error(f"{name}: Failed")
        else:
            st.info(f"{name}: Not recorded / not applicable")

# ---------------------------------------------------------------------
# Transformation details
# ---------------------------------------------------------------------

st.subheader("Transformation details")

if applied:
    for index, change in enumerate(applied, start=1):
        st.markdown(
            f"**{index}. {change.get('transformation', 'Transformation')}** "
            f"· line {change.get('line_number', '?')}"
        )

        st.code(
            change.get(
                "original_instruction",
                "(original instruction not recorded)",
            ),
            language="llvm",
        )

        st.write(
            change.get(
                "replacement_instruction",
                "Replacement details not recorded.",
            )
        )

        if change.get("reason"):
            st.caption(change["reason"])
else:
    st.info(
        "No successfully applied transformation is recorded in this report."
    )

with st.expander("Function-level behavior test", expanded=False):
    st.json(
        r.get(
            "function_behavior_check",
            {"message": "No function-level check recorded."},
        )
    )

with st.expander("Raw JSON report", expanded=False):
    st.json(r)

# ---------------------------------------------------------------------
# Interpretation guardrails
# ---------------------------------------------------------------------

st.divider()
st.markdown("**Interpretation guardrails**")

st.write(
    "- LLVM acceptance is not proof of semantic equivalence."
)
st.write(
    "- Differential tests cover only the tested cases."
)
st.write(
    "- Runtime differences are measurements, not proof of a general speedup."
)
st.write(
    "- Formal equivalence is proven only when the report explicitly records it."
)

st.caption("ExplainOpt-X · Local presentation dashboard")
