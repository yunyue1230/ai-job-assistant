from pydantic import BaseModel, Field


class JobMatchAnalysis(BaseModel):
    match_score: int = Field(ge=0, le=100)
    matched_skills: list[str]
    missing_skills: list[str]
    strengths: list[str]
    gaps: list[str]
    resume_improvement_tips: list[str]
    interview_questions: list[str]
