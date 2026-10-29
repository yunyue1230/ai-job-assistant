import hashlib
import uuid
from pathlib import PurePath

import streamlit as st

from services.job_match_service import analyze_job_match
from services.resume_parser import parse_resume_file
from services.storage import create_storage

st.set_page_config(page_title="AI Job Assistant", page_icon="💼")
st.title("AI Job Application Assistant")

storage = create_storage()

with st.sidebar:
    st.header("Instructions")
    st.markdown(
        "- Paste a job description and your resume\n"
        "- The app will identify skill matches, gaps, and interview prep\n"
        "- Suitable for AI / ML / product internship applications"
    )

job_description = st.text_area(
    "Job Description",
    height=250,
    value=(
        "We are looking for an AI Product Intern.\n\nRequirements:\n"
        "- Python\n- LLM applications\n- RAG and prompt engineering\n"
        "- Good communication skills\n- Experience building AI applications\n\n"
        "Responsibilities:\n- Build AI product demos\n- Work with product and engineering teams\n"
        "- Help prototype ML and LLM features"
    ),
)

uploaded_resume = st.file_uploader(
    "Upload Resume (TXT, Markdown, or PDF)",
    type=["txt", "md", "pdf"],
)

resume_default = (
    "Education: Computer Science\n\nProjects:\n"
    "- Built a diffusion model for image super-resolution using Python and PyTorch.\n"
    "- Fine-tuned a custom model and evaluated model quality.\n"
    "- Built a chatbot prototype in Python using prompt engineering and LLM APIs.\n"
    "- Built a data analysis pipeline to explore user behavior and present findings.\n\n"
    "Skills:\n- Python, PyTorch, Machine Learning, Data Analysis\n"
    "- Basic LLM API integration\n- Teamwork and communication\n"
)

if uploaded_resume is not None:
    try:
        file_bytes = uploaded_resume.getvalue()
        digest = hashlib.sha256(file_bytes).hexdigest()
        stored_resume = st.session_state.get("stored_resume")
        if stored_resume is None or stored_resume[0] != digest:
            extension = PurePath(uploaded_resume.name).suffix.lower()
            key = f"resumes/{uuid.uuid4().hex}{extension}"
            storage.save(key, file_bytes)
            stored_resume = (digest, key)
            st.session_state["stored_resume"] = stored_resume

        resume_default = parse_resume_file(
            uploaded_resume.name,
            storage.read(stored_resume[1]),
        )
        st.success(f"Loaded resume: {uploaded_resume.name}")
    except (UnicodeDecodeError, ValueError) as error:
        st.error(str(error))

resume = st.text_area(
    "Resume Text (you can edit the extracted content)",
    height=250,
    value=resume_default,
)

if st.button("Analyze Match"):
    if not job_description.strip() or not resume.strip():
        st.warning("Please provide both the job description and your resume.")
    else:
        with st.spinner("Analyzing your fit..."):
            result = analyze_job_match(job_description, resume)

        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Match Score", f"{result['match_score']} / 100")
        with col2:
            st.metric("Matched Skills", len(result.get("matched_skills", [])))
        with col3:
            st.metric("Missing Skills", len(result.get("missing_skills", [])))

        st.subheader("Matched Skills")
        st.write(", ".join(result.get("matched_skills", [])) or "None")

        st.subheader("Missing Skills")
        st.write(", ".join(result.get("missing_skills", [])) or "None")

        st.subheader("Strengths")
        for item in result.get("strengths", []):
            st.markdown(f"- {item}")

        st.subheader("Gaps")
        for item in result.get("gaps", []):
            st.markdown(f"- {item}")

        st.subheader("Resume Improvement Tips")
        for item in result.get("resume_improvement_tips", []):
            st.markdown(f"- {item}")

        st.subheader("Interview Questions")
        for i, item in enumerate(result.get("interview_questions", []), start=1):
            st.markdown(f"{i}. {item}")
