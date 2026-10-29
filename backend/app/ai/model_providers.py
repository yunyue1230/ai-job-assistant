import os
from dataclasses import dataclass

from openai import OpenAI

from backend.app.core.config import ModelProviderConfig, Settings, get_settings


@dataclass(frozen=True)
class ProviderInfo:
    name: str
    base_url: str
    model: str
    configured: bool
    active: bool


def _provider_config(
    settings: Settings, name: str
) -> tuple[ModelProviderConfig, str | None]:
    if name in settings.model_providers:
        configuration = settings.model_providers[name]
        return configuration, os.getenv(configuration.api_key_env)
    if name == "deepseek":
        configuration = ModelProviderConfig(
            base_url=settings.deepseek_base_url,
            model=settings.model_name,
            api_key_env="DEEPSEEK_API_KEY",
        )
        return configuration, settings.deepseek_api_key or os.getenv("DEEPSEEK_API_KEY")
    raise ValueError(f"Unknown chat model provider {name!r}.")


def create_chat_client(name: str | None = None) -> tuple[OpenAI, str]:
    settings = get_settings()
    provider_name = name or settings.active_chat_provider
    configuration, api_key = _provider_config(settings, provider_name)
    if not api_key:
        raise RuntimeError(
            f"Set {configuration.api_key_env} to enable provider {provider_name!r}."
        )
    return (
        OpenAI(api_key=api_key, base_url=configuration.base_url),
        configuration.model,
    )


def active_provider_credentials() -> tuple[str, str, str]:
    settings = get_settings()
    configuration, api_key = _provider_config(
        settings, settings.active_chat_provider
    )
    if not api_key:
        raise RuntimeError(
            f"Set {configuration.api_key_env} to enable provider "
            f"{settings.active_chat_provider!r}."
        )
    return api_key, configuration.base_url, configuration.model


def list_provider_info() -> list[ProviderInfo]:
    settings = get_settings()
    names = sorted(settings.configured_provider_names)
    if not names:
        return []
    output: list[ProviderInfo] = []
    for name in names:
        configuration, api_key = _provider_config(settings, name)
        output.append(
            ProviderInfo(
                name=name,
                base_url=configuration.base_url,
                model=configuration.model,
                configured=bool(api_key),
                active=name == settings.active_chat_provider,
            )
        )
    return output
