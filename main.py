import json
import os

from services.job_match_service import analyze_job_match


if __name__ == "__main__":
    base_dir = os.path.dirname(__file__)
    jd_path = os.path.join(base_dir, "sample_jd.txt")
    resume_path = os.path.join(base_dir, "sample_resume.txt")

    with open(jd_path, "r", encoding="utf-8") as f:
        job_description = f.read()
    with open(resume_path, "r", encoding="utf-8") as f:
        resume = f.read()

    result = analyze_job_match(job_description, resume)
    print(json.dumps(result, ensure_ascii=False, indent=2))
