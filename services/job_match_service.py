from langchain_core.output_parsers import JsonOutputParser
from langchain_openai import ChatOpenAI

from backend.app.ai.model_providers import active_provider_credentials
from prompts.job_match import create_job_match_prompt
from schemas.job_match import JobMatchAnalysis

parser = JsonOutputParser(pydantic_object=JobMatchAnalysis)
prompt = create_job_match_prompt(parser.get_format_instructions())

def analyze_job_match(
    job_description: str,
    resume: str,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    model_name: str | None = None,
) -> dict:
    if api_key is None and base_url is None and model_name is None:
        api_key, base_url, model_name = active_provider_credentials()
    elif api_key is None or base_url is None or model_name is None:
        raise ValueError("API key, base URL, and model must be provided together.")
    model = ChatOpenAI(
        api_key=api_key,
        base_url=base_url,
        model=model_name,
        temperature=0.3,
    )
    result = (prompt | model | parser).invoke(
        {"job_description": job_description, "resume": resume}
    )
    return JobMatchAnalysis.model_validate(result).model_dump()
