import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from backend.app.ai.embeddings import (
    EmbeddingService,
    embedding_provider_credentials,
)
from backend.app.core.config import EmbeddingProviderConfig, Settings


class EmbeddingServiceTests(unittest.TestCase):
    def test_batches_embedding_requests_and_preserves_order(self) -> None:
        client = Mock()

        def create_embeddings(*, model, input, dimensions):
            return SimpleNamespace(
                data=[
                    SimpleNamespace(embedding=[float(index)] * dimensions)
                    for index, _ in enumerate(input)
                ]
            )

        client.embeddings.create.side_effect = create_embeddings
        service = EmbeddingService(client, "test-embedding", dimensions=3)
        texts = [f"chunk-{index}" for index in range(65)]

        vectors = service.embed(texts)

        self.assertEqual(len(vectors), 65)
        self.assertEqual(len(vectors[0]), 3)
        self.assertEqual(client.embeddings.create.call_count, 2)
        self.assertEqual(
            client.embeddings.create.call_args_list[0].kwargs["input"],
            texts[:64],
        )
        self.assertEqual(
            client.embeddings.create.call_args_list[1].kwargs["input"],
            texts[64:],
        )

    def test_rejects_embedding_provider_dimension_mismatch(self) -> None:
        client = Mock()
        client.embeddings.create.return_value = SimpleNamespace(
            data=[SimpleNamespace(embedding=[0.1, 0.2])]
        )

        with self.assertRaisesRegex(RuntimeError, "dimensions"):
            EmbeddingService(client, "test-embedding", dimensions=3).embed(["text"])

    def test_selected_embedding_provider_uses_its_own_env_key(self) -> None:
        settings = Settings(
            active_chat_provider="deepseek",
            embedding_provider="test",
            embedding_providers={
                "test": EmbeddingProviderConfig(
                    base_url="https://embeddings.example/v1",
                    model="test-embedding",
                    api_key_env="TEST_EMBEDDING_KEY",
                )
            },
        )
        with (
            patch.dict(
                os.environ,
                {
                    "TEST_EMBEDDING_KEY": "provider-key",
                    "EMBEDDING_API_KEY": "unselected-key",
                },
            ),
            patch(
                "backend.app.ai.embeddings.get_settings",
                return_value=settings,
            ),
        ):
            self.assertEqual(
                embedding_provider_credentials(),
                (
                    "provider-key",
                    "https://embeddings.example/v1",
                    "test-embedding",
                ),
            )


if __name__ == "__main__":
    unittest.main()
