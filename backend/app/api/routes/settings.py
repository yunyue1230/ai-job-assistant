from fastapi import APIRouter
from pydantic import BaseModel

from backend.app.ai.embeddings import embedding_provider_is_configured
from backend.app.ai.model_providers import ProviderInfo, list_provider_info
from backend.app.api.deps import CurrentUser
from backend.app.core.config import get_settings

router = APIRouter(prefix="/settings", tags=["model settings"])


class EmbeddingProviderInfo(BaseModel):
    provider: str
    base_url: str
    model: str
    dimensions: int
    configured: bool


class SpeechProviderInfo(BaseModel):
    provider: str
    configured: bool
    asr_model: str
    tts_model: str
    voice: str


class ProviderList(BaseModel):
    active_provider: str
    providers: list[ProviderInfo]
    embedding: EmbeddingProviderInfo
    speech: SpeechProviderInfo


@router.get("/models", response_model=ProviderList)
async def list_model_providers(_: CurrentUser) -> ProviderList:
    settings = get_settings()
    embedding = settings.embedding_provider_config
    return ProviderList(
        active_provider=settings.active_chat_provider,
        providers=list_provider_info(),
        embedding=EmbeddingProviderInfo(
            provider=settings.embedding_provider,
            base_url=embedding.base_url,
            model=embedding.model,
            dimensions=settings.embedding_dimensions,
            configured=embedding_provider_is_configured(),
        ),
        speech=SpeechProviderInfo(
            provider="Alibaba Cloud Bailian (DashScope)",
            configured=bool(settings.ai_bailian_api_key),
            asr_model=settings.qwen_asr_model,
            tts_model=settings.qwen_tts_model,
            voice=settings.qwen_tts_voice,
        ),
    )
