import os
import unittest
from unittest.mock import patch

from backend.app.ai.model_providers import (
    active_provider_credentials,
    create_chat_client,
    list_provider_info,
)
from backend.app.core.config import ModelProviderConfig, Settings, get_settings


class ModelProviderTests(unittest.TestCase):
    def test_active_compatible_provider_uses_env_key_without_storing_it(self) -> None:
        provider = ModelProviderConfig(
            base_url="https://example.test/v1",
            model="example-chat",
            api_key_env="EXAMPLE_LLM_KEY",
        )
        settings = Settings(
            active_chat_provider="example",
            model_providers={"example": provider},
        )
        with patch.dict(os.environ, {"EXAMPLE_LLM_KEY": "test-secret"}):
            with patch(
                "backend.app.ai.model_providers.get_settings",
                return_value=settings,
            ):
                credentials = active_provider_credentials()
                self.assertEqual(
                    credentials,
                    ("test-secret", "https://example.test/v1", "example-chat"),
                )

    def test_provider_listing_never_exposes_api_key(self) -> None:
        provider = ModelProviderConfig(
            base_url="https://example.test/v1",
            model="example-chat",
            api_key_env="EXAMPLE_LLM_KEY",
        )
        settings = Settings(
            active_chat_provider="example",
            model_providers={"example": provider},
        )
        with patch.dict(os.environ, {"EXAMPLE_LLM_KEY": "do-not-return-this"}):
            with patch(
                "backend.app.ai.model_providers.get_settings",
                return_value=settings,
            ):
                info = list_provider_info()

        provider_info = next(item for item in info if item.name == "example")
        self.assertTrue(provider_info.configured)
        self.assertTrue(provider_info.active)
        self.assertNotIn("do-not-return-this", repr(info))

    def test_client_requires_provider_environment_key(self) -> None:
        provider = ModelProviderConfig(
            base_url="https://example.test/v1",
            model="example-chat",
            api_key_env="MISSING_EXAMPLE_KEY",
        )
        settings = Settings(
            active_chat_provider="example",
            model_providers={"example": provider},
        )
        with patch.dict(os.environ, {}, clear=True):
            with patch(
                "backend.app.ai.model_providers.get_settings",
                return_value=settings,
            ):
                with self.assertRaisesRegex(RuntimeError, "MISSING_EXAMPLE_KEY"):
                    create_chat_client()


if __name__ == "__main__":
    unittest.main()
