import json

from langchain_core.messages import HumanMessage
from langchain_core.output_parsers import JsonOutputParser
from langchain_openai import ChatOpenAI

from backend.app.ai.model_providers import active_provider_credentials
from backend.app.ai.structured_output import CompletionStats, StructuredCompletion
from prompts.job_match import create_job_match_prompt
from schemas.job_match import JobMatchAnalysis

# The parser stays for its format instructions, which shape the prompt; the
# parsing itself goes through StructuredCompletion so a reply with trailing
# prose or a forgotten field is handled the same way as in the interview engine.
parser = JsonOutputParser(pydantic_object=JobMatchAnalysis)
prompt = create_job_match_prompt(parser.get_format_instructions())


def analyze_job_match(
    job_description: str,
    resume: str,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    model_name: str | None = None,
    stats: CompletionStats | None = None,
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

    def send(_system: str, user: str, extra: str | None) -> str:
        messages = prompt.invoke(json.loads(user)).to_messages()
        if extra:
            messages = [*messages, HumanMessage(content=extra)]
        return str(model.invoke(messages).content)

    completion = StructuredCompletion(send, name="job_match", stats=stats)
    analysis = completion.complete(
        "",
        json.dumps({"job_description": job_description, "resume": resume}),
        JobMatchAnalysis,
    )
    return analysis.model_dump()
