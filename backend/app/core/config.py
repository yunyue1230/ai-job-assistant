from functools import lru_cache

from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ModelProviderConfig(BaseModel):
    base_url: str
    model: str
    api_key_env: str


class EmbeddingProviderConfig(BaseModel):
    base_url: str
    model: str
    api_key_env: str


class Settings(BaseSettings):
    app_name: str = "Interview Guide API"
    environment: str = "development"
    database_url: str = "sqlite+aiosqlite:///./interview_guide.db"
    jwt_secret_key: str = Field(
        default="development-only-secret-key-change-before-deploying",
        min_length=32,
    )
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    cors_origins: list[str] = ["http://localhost:5173"]
    redis_url: str = "redis://localhost:6379/0"
    upload_max_bytes: int = 10 * 1024 * 1024
    storage_backend: str = "local"
    upload_dir: str = "uploads"
    s3_bucket: str | None = None
    s3_endpoint_url: str | None = None
    s3_region: str = "us-east-1"
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None
    deepseek_api_key: str | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    model_name: str = "deepseek-chat"
    active_chat_provider: str = "deepseek"
    model_providers: dict[str, ModelProviderConfig] = Field(default_factory=dict)
    embedding_providers: dict[str, EmbeddingProviderConfig] = Field(
        default_factory=dict
    )
    embedding_api_key: str | None = None
    embedding_provider: str = "custom-openai-compatible"
    embedding_base_url: str = "https://api.openai.com/v1"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = Field(default=1536, gt=0, le=2000)
    ai_bailian_api_key: str | None = None
    qwen_asr_model: str = "qwen3-asr-flash-realtime"
    qwen_tts_model: str = "qwen3-tts-flash-realtime"
    qwen_tts_voice: str = "Cherry"
    rag_chunk_size: int = Field(default=1000, gt=0)
    rag_chunk_overlap: int = Field(default=150, ge=0)
    rag_top_k: int = Field(default=5, ge=1, le=20)

    @model_validator(mode="after")
    def validate_chunk_overlap(self) -> "Settings":
        if self.rag_chunk_overlap >= self.rag_chunk_size:
            raise ValueError("RAG_CHUNK_OVERLAP must be smaller than RAG_CHUNK_SIZE.")
        if self.active_chat_provider not in self.configured_provider_names:
            raise ValueError(
                "ACTIVE_CHAT_PROVIDER must refer to a configured model provider."
            )
        return self

    @property
    def configured_provider_names(self) -> set[str]:
        names = set(self.model_providers)
        if "deepseek" not in names and (
            self.deepseek_api_key or self.deepseek_base_url
        ):
            names.add("deepseek")
        return names

    @property
    def embedding_provider_config(self) -> EmbeddingProviderConfig:
        configured = self.embedding_providers.get(self.embedding_provider)
        if configured is not None:
            return configured
        return EmbeddingProviderConfig(
            base_url=self.embedding_base_url,
            model=self.embedding_model,
            api_key_env="EMBEDDING_API_KEY",
        )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
