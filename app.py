from pathlib import Path
import json
import statistics
import streamlit as st
import pandas as pd
import altair as alt

st.set_page_config(page_title="ExplainOpt-X", page_icon="⚙️", layout="wide")

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results" / "counterfactual"
REPORT_FILES = sorted(RESULTS.glob("*.json")) if RESULTS.exists() else []

st.markdown("""
<style>
.block-container {padding-top: 1.5rem; padding-bottom: 2rem; max-width: 1400px;}
.hero {padding: 1.25rem 1.5rem; border-radius: 16px; background: linear-gradient(115deg,#12243a,#244e68); color: white; margin-bottom: 1rem;}
.hero h1 {color: white; margin: 0; font-size: 2.1rem;}
.hero p {color: #dce8f2; margin: .45rem 0 0 0;}
.small-muted {color: #64748b; font-size: .88rem;}
</style>
<div class="hero">
<h1>ExplainOpt-X</h1>
<p>Counterfactual &amp; Semantics-Aware Explainable Compiler Optimization</p>
</div>
""", unsafe_allow_html=True)

st.caption("Research prototype · Experiment report viewer · Runtime measurements are observational, not proof of optimization.")

if not RESULTS.exists():
    st.error(f"Results directory not found: {RESULTS}")
    st.info("Save this app.py in your ExplainOpt project root, next to the src and results folders.")
    st.stop()

if not REPORT_FILES:
    st.warning("No JSON reports found in results/counterfactual.")
    st.write("Run an experiment with your existing experiment_runner, then refresh this page.")
    st.code('python -m src.explainopt.counterfactual.experiment_runner --input "samples\\source\\counterfactual_add_zero.ll" --repeats 15 --clang "C:\\Program Files\\LLVM\\bin\\clang.exe" --output "results\\counterfactual\\add_zero_experiment.json"', language="powershell")
    st.stop()

reports = []
for file in REPORT_FILES:
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
        reports.append((file, data))
    except Exception:
        continue

if not reports:
    st.error("JSON files were found, but none could be parsed.")
    st.stop()

def fmt_ms(value):
    return f"{value * 1000:.3f} ms" if isinstance(value, (int, float)) else "N/A"

def get_median(report, key):
    value = report.get(key, {}).get("median_seconds")
    return value if isinstance(value, (int, float)) else None

labels = [f"{f.name}  ·  {d.get('status', 'unknown')}" for f, d in reports]
chosen = st.selectbox("Select an experiment report", range(len(reports)), format_func=lambda i: labels[i])
file, r = reports[chosen]

original = get_median(r, "original_measurement")
candidate = get_median(r, "candidate_measurement")
speedup = r.get("speedup_original_over_candidate", r.get("speedup"))
reduction = r.get("runtime_reduction_percent")
changes = r.get("applied_changes") or []
applied = [c for c in changes if c.get("applied")]

st.subheader("Experiment overview")
m1, m2, m3, m4 = st.columns(4)
m1.metric("Status", str(r.get("status", "unknown")).replace("_", " ").title())
m2.metric("Transformation", "Applied" if r.get("transformation_applied") else "No candidate")
m3.metric("Reported speedup", f"{speedup:.4f}×" if isinstance(speedup, (int, float)) else "N/A")
m4.metric("Runtime change", f"{reduction:+.2f}%" if isinstance(reduction, (int, float)) else "N/A")

st.caption(f"Report: `{file.relative_to(ROOT)}`")
with st.expander("Input and experiment settings", expanded=False):
    st.write("**Input file:**", r.get("input_file", "Not recorded"))
    st.write("**Compiler:**", r.get("compiler", "Not recorded"))
    st.write("**Compiler flags:**", ", ".join(r.get("compiler_flags", [])))
    st.write("**Repetitions per version:**", r.get("repetitions_per_version", "Not recorded"))

left, right = st.columns([1.15, 1])
with left:
    st.subheader("Runtime comparison")
    if original is not None and candidate is not None:
        chart_df = pd.DataFrame([
            {"Version": "Original", "Median runtime (ms)": original * 1000},
            {"Version": "Candidate", "Median runtime (ms)": candidate * 1000},
        ])
        chart = alt.Chart(chart_df).mark_bar(cornerRadiusEnd=5).encode(
            x=alt.X("Version:N", sort=["Original", "Candidate"], title=None),
            y=alt.Y("Median runtime (ms):Q", title="Median runtime (ms)", scale=alt.Scale(zero=True)),
            tooltip=["Version", alt.Tooltip("Median runtime (ms):Q", format=".3f")]
        ).properties(height=280)
        st.altair_chart(chart, use_container_width=True)
        a, b = st.columns(2)
        a.metric("Original median", fmt_ms(original))
        b.metric("Candidate median", fmt_ms(candidate))
        st.caption("Lower median is faster in this run. Small differences may be measurement noise.")
    else:
        st.info("This report does not contain both runtime measurements.")
with right:
    st.subheader("Correctness evidence")
    validation = r.get("candidate_validation", {})
    checks = [
        ("Structural validation", r.get("candidate_structural_validation_passed", validation.get("structural_validation_passed"))),
        ("LLVM validation", r.get("candidate_compilation_passed", validation.get("llvm_validation_passed"))),
        ("Differential behavior", r.get("differential_behavior_check_passed")),
        ("Paired observations", r.get("paired_observations_match")),
    ]
    for name, value in checks:
        if value is True:
            st.success(f"{name}: Passed")
        elif value is False:
            st.error(f"{name}: Failed")
        else:
            st.info(f"{name}: Not recorded / not applicable")
    formal = r.get("formal_equivalence_proven", False)
    if formal is True:
        st.success("Formal equivalence: Proven (according to report)")
    else:
        st.warning("Formal equivalence: Not proven")

st.subheader("Transformation details")
if applied:
    for i, change in enumerate(applied, 1):
        st.markdown(f"**{i}. {change.get('transformation', 'Transformation')}** · line {change.get('line_number', '?')}")
        st.code(change.get("original_instruction", "(original instruction not recorded)"), language="llvm")
        st.write(change.get("replacement_instruction", "Replacement details not recorded."))
        if change.get("reason"):
            st.caption(change["reason"])
else:
    st.info("No successfully applied transformation is recorded in this report.")

with st.expander("Function-level behavior test", expanded=False):
    st.json(r.get("function_behavior_check", {"message": "No function-level check recorded."}))

with st.expander("Raw JSON report", expanded=False):
    st.json(r)

st.divider()
st.markdown("**Interpretation guardrails**")
st.write("- LLVM acceptance is not a proof of semantic equivalence.")
st.write("- Differential tests cover the tested cases only.")
st.write("- Runtime differences are measurements, not causal proof of a general speedup.")
st.write("- Formal equivalence is marked as not proven unless the report explicitly says otherwise.")

st.caption("ExplainOpt-X · Local presentation dashboard")
