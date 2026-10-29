from langchain_core.prompts import ChatPromptTemplate


def create_job_match_prompt(format_instructions: str) -> ChatPromptTemplate:
    return ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "You are an AI career assistant. Compare a job description and a resume. "
                "Return only valid JSON.\n{format_instructions}",
            ),
            (
                "human",
                "Job Description:\n{job_description}\n\nResume:\n{resume}",
            ),
        ]
    ).partial(format_instructions=format_instructions)
