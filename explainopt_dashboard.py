from pathlib import Path
import json
import subprocess
import tempfile
import sys

import altair as alt
import pandas as pd
import streamlit as st

st.set_page_config(page_title="ExplainOpt-X", page_icon="⚙️", layout="wide")

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results" / "counterfactual"
RESULTS.mkdir(parents=True, exist_ok=True)
CLANG = r"C:\Program Files\LLVM\bin\clang.exe"

st.markdown("""
<style>
.block-container {padding-top: 1.2rem; padding-bottom: 2rem; max-width: 1450px;}
.hero {padding: 1.25rem 1.5rem; border-radius: 16px; background: linear-gradient(115deg,#12243a,#244e68); color: white; margin-bottom: 1rem;}
.hero h1 {color: white; margin: 0; font-size: 2.1rem;}
.hero p {color: #dce8f2; margin: .45rem 0 0 0;}
</style>
<div class="hero">
<h1>ExplainOpt-X</h1>
<p>Counterfactual &amp; Semantics-Aware Explainable Compiler Optimization</p>
</div>
""", unsafe_allow_html=True)
st.caption("Research prototype · Supports two LLVM IR identity rewrites: add-zero and multiply-by-one.")

def load_engine():
    try:
        from src.explainopt.counterfactual.engine import simplify_supported_identities, validate_candidate
        return simplify_supported_identities, validate_candidate, None
    except Exception as exc:
        return None, None, str(exc)

simplify_supported_identities, validate_candidate, import_error = load_engine()
if import_error:
    st.error("Could not import the existing ExplainOpt-X engine from this project folder.")
    st.code(import_error)
    st.info("Save this file as app.py in the project root, alongside the src folder.")
    st.stop()

tab_run, tab_reports, tab_about = st.tabs(["Analyze & Transform", "Experiment Reports", "Project Scope"])

with tab_run:
    st.subheader("1. Provide program input")
    st.write("Upload an LLVM IR `.ll` file, or a C `.c` file to compile to LLVM IR first. The current optimizer operates on LLVM IR.")
    uploaded = st.file_uploader("Upload source code (.ll or .c)", type=["ll", "c"])
    source_text = ""
    source_name = "pasted_input.ll"
    if uploaded is not None:
        source_name = Path(uploaded.name).name
        try:
            source_text = uploaded.getvalue().decode("utf-8")
        except UnicodeDecodeError:
            st.error("File must be UTF-8 text.")
            source_text = ""
    else:
        source_text = st.text_area(
            "Or paste LLVM IR here",
            height=240,
            placeholder="define i32 @identity(i32 %arg) {\nentry:\n  %value = add i32 %arg, 0\n  ret i32 %value\n}",
        )
        source_name = "pasted_input.ll"

    c1, c2 = st.columns([1, 2])
    with c1:
        analyze_clicked = st.button("Analyze optimization opportunities", type="primary", use_container_width=True)
    with c2:
        st.caption("The analyzer only recommends transformations matching its implemented patterns. It does not yet rank arbitrary optimization passes.")

    if analyze_clicked:
        if not source_text.strip():
            st.warning("Upload a source file or paste LLVM IR first.")
        else:
            ir_text = source_text
            input_kind = Path(source_name).suffix.lower()
            conversion_ok = True
            if input_kind == ".c":
                with tempfile.TemporaryDirectory(prefix="explainopt_frontend_") as td:
                    c_path = Path(td) / "input.c"
                    ir_path = Path(td) / "input.ll"
                    c_path.write_text(source_text, encoding="utf-8")
                    try:
                        proc = subprocess.run(
                            [CLANG, "-S", "-emit-llvm", "-O0", str(c_path), "-o", str(ir_path)],
                            capture_output=True, text=True, timeout=20
                        )
                    except Exception as exc:
                        conversion_ok = False
                        st.error(f"Could not invoke Clang: {exc}")
                    else:
                        if proc.returncode != 0 or not ir_path.exists():
                            conversion_ok = False
                            st.error("C-to-LLVM IR compilation failed.")
                            st.code(proc.stderr or proc.stdout)
                        else:
                            ir_text = ir_path.read_text(encoding="utf-8")
            if conversion_ok:
                st.session_state["explainopt_original_ir"] = ir_text
                st.session_state["explainopt_input_name"] = source_name
                candidate_ir, changes = simplify_supported_identities(ir_text)
                applied = [x for x in changes if x.applied]
                st.session_state["explainopt_candidate_ir"] = candidate_ir
                st.session_state["explainopt_changes"] = [
                    {
                        "transformation": x.transformation,
                        "original_instruction": x.original_instruction,
                        "replacement_instruction": x.replacement_instruction,
                        "line_number": x.line_number,
                        "applied": x.applied,
                        "reason": x.reason,
                    } for x in changes
                ]
                st.session_state["explainopt_applied"] = bool(applied)
                st.session_state["explainopt_analysis_done"] = True

    if st.session_state.get("explainopt_analysis_done"):
        original_ir = st.session_state["explainopt_original_ir"]
        candidate_ir = st.session_state["explainopt_candidate_ir"]
        changes = st.session_state["explainopt_changes"]
        applied = [x for x in changes if x["applied"]]
        st.divider()
        st.subheader("2. Analysis and recommendation")
        a, b, c = st.columns(3)
        a.metric("Supported patterns found", len(changes))
        b.metric("Transformations applied", len(applied))
        c.metric("Input format", Path(st.session_state["explainopt_input_name"]).suffix.upper().lstrip("."))
        if applied:
            for index, item in enumerate(applied, 1):
                if item["transformation"] == "add_zero_identity":
                    label = "Eliminate addition by zero"
                    why = "For integer arithmetic, x + 0 equals x. The intermediate add instruction can be removed and its uses redirected to x."
                elif item["transformation"] == "mul_one_identity":
                    label = "Eliminate multiplication by one"
                    why = "For integer arithmetic, x × 1 equals x. The intermediate multiply instruction can be removed and its uses redirected to x."
                else:
                    label = item["transformation"]
                    why = "This matches a transformation pattern implemented by the current engine."
                with st.container(border=True):
                    st.markdown(f"**Recommendation {index}: {label}**")
                    st.write("**Why selected:**", why)
                    st.write("**Original instruction:**")
                    st.code(item["original_instruction"], language="llvm")
                    st.write("**Transformation action:**", item["replacement_instruction"])
                    st.caption(f"Matched source line: {item['line_number']}. This is a rule-based match, not a learned ranking.")
            st.success("Candidate LLVM IR generated.")
        else:
            st.warning("No supported identity patterns were found. The system cannot currently recommend other optimization techniques for this input.")
            st.write("This does not mean the program has no optimization opportunities—only that none of the currently implemented patterns matched.")

        with st.expander("Original LLVM IR", expanded=False):
            st.code(original_ir, language="llvm")
        st.subheader("3. Candidate LLVM IR")
        st.code(candidate_ir, language="llvm")
        st.download_button(
            "Download candidate LLVM IR",
            data=candidate_ir,
            file_name="explainopt_candidate.ll",
            mime="text/plain",
            use_container_width=False,
        )

        st.subheader("4. Validate candidate")
        if st.button("Run structural and LLVM validation", use_container_width=True):
            with st.spinner("Validating candidate LLVM IR..."):
                try:
                    structural_ok, llvm_ok, err = validate_candidate(candidate_ir, clang_path=CLANG)
                    st.session_state["explainopt_validation"] = {
                        "structural_validation_passed": bool(structural_ok),
                        "llvm_validation_passed": bool(llvm_ok),
                        "error": err,
                        "formal_equivalence_proven": False,
                    }
                except Exception as exc:
                    st.session_state["explainopt_validation"] = {
                        "structural_validation_passed": False,
                        "llvm_validation_passed": False,
                        "error": str(exc),
                        "formal_equivalence_proven": False,
                    }
        validation = st.session_state.get("explainopt_validation")
        if validation:
            v1, v2, v3 = st.columns(3)
            v1.metric("Structural validation", "Passed" if validation["structural_validation_passed"] else "Failed")
            v2.metric("LLVM validation", "Passed" if validation["llvm_validation_passed"] else "Failed")
            v3.metric("Formal equivalence", "Not proven")
            if validation.get("error"):
                st.error(validation["error"])
            st.caption("Validation confirms structural/LLVM acceptance; it is not a formal-equivalence proof.")

        st.subheader("5. Save input for a full experiment")
        st.write("To collect behavior and runtime measurements, save the original LLVM IR and run the existing experiment runner. A C input is first converted to LLVM IR.")
        if st.button("Save original LLVM IR to experiment inputs"):
            input_path = RESULTS / "dashboard_input.ll"
            input_path.write_text(original_ir, encoding="utf-8")
            st.success(f"Saved input: {input_path.relative_to(ROOT)}")
            st.code(
                'python -m src.explainopt.counterfactual.experiment_runner --input "results\\counterfactual\\dashboard_input.ll" --repeats 15 --clang "C:\\Program Files\\LLVM\\bin\\clang.exe" --output "results\\counterfactual\\dashboard_experiment.json"',
                language="powershell",
            )

with tab_reports:
    st.subheader("Existing experiment reports")
    report_files = sorted(RESULTS.glob("*.json")) if RESULTS.exists() else []
    parsed = []
    for path in report_files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                parsed.append((path, data))
        except Exception:
            continue
    if not parsed:
        st.info("No readable JSON experiment reports found yet.")
    else:
        choices = [f"{p.name} · {d.get('status', 'report')}" for p, d in parsed]
        idx = st.selectbox("Choose a report", range(len(parsed)), format_func=lambda i: choices[i], key="report_choice")
        path, report = parsed[idx]
        original = report.get("original_measurement", {}).get("median_seconds")
        candidate = report.get("candidate_measurement", {}).get("median_seconds")
        speedup = report.get("speedup_original_over_candidate", report.get("speedup"))
        reduction = report.get("runtime_reduction_percent")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Status", str(report.get("status", "unknown")).replace("_", " ").title())
        m2.metric("Transformation", "Applied" if report.get("transformation_applied") else "No candidate")
        m3.metric("Speedup", f"{speedup:.4f}×" if isinstance(speedup, (int, float)) else "N/A")
        m4.metric("Runtime change", f"{reduction:+.2f}%" if isinstance(reduction, (int, float)) else "N/A")
        if isinstance(original, (int, float)) and isinstance(candidate, (int, float)):
            df = pd.DataFrame([
                {"Version": "Original", "Median runtime (ms)": original * 1000},
                {"Version": "Candidate", "Median runtime (ms)": candidate * 1000},
            ])
            chart = alt.Chart(df).mark_bar(cornerRadiusEnd=5).encode(
                x=alt.X("Version:N", sort=["Original", "Candidate"], title=None),
                y=alt.Y("Median runtime (ms):Q", scale=alt.Scale(zero=True)),
                tooltip=["Version", alt.Tooltip("Median runtime (ms):Q", format=".3f")]
            ).properties(height=260)
            st.altair_chart(chart, use_container_width=True)
        validation = report.get("candidate_validation", {})
        rows = [
            {"Check": "Structural validation", "Result": report.get("candidate_structural_validation_passed", validation.get("structural_validation_passed"))},
            {"Check": "LLVM validation", "Result": report.get("candidate_compilation_passed", validation.get("llvm_validation_passed"))},
            {"Check": "Differential behavior", "Result": report.get("differential_behavior_check_passed")},
            {"Check": "Paired observations", "Result": report.get("paired_observations_match")},
            {"Check": "Formal equivalence", "Result": report.get("formal_equivalence_proven", False)},
        ]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        with st.expander("Transformation records"):
            st.json(report.get("applied_changes", []))
        with st.expander("Full report JSON"):
            st.json(report)

with tab_about:
    st.subheader("Current implementation scope")
    st.markdown("""
- **Input:** LLVM IR (`.ll`) directly, or C (`.c`) compiled to LLVM IR using the configured Clang executable.
- **Implemented transformations:** remove eligible `add iN %x, 0` and `mul iN %x, 1` instructions and redirect SSA uses.
- **Explanation:** rule-based descriptions for those matched transformations.
- **Validation:** existing structural and LLVM validation; the experiment runner also offers differential behavior checks and runtime measurements.
- **Not yet implemented:** broad optimization-pass recommendation, learned ranking of arbitrary passes, and formal-equivalence proof integration.
""")
    st.warning("A successful validation or differential test is not a formal proof. Runtime changes from small experiments may be noise.")

st.divider()
st.caption("ExplainOpt-X · Local research prototype")
