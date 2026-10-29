import base64
import unittest
from unittest.mock import Mock, patch

from backend.app.ai.speech import RealtimeSpeechService
from backend.app.api.deps import get_session
from backend.app.core.config import Settings
from backend.app.db.base import Base
from backend.app.main import app
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


class RealtimeSpeechTests(unittest.TestCase):
    def test_connects_asr_with_configured_model_and_transcription(self) -> None:
        settings = Settings(
            active_chat_provider="deepseek",
            ai_bailian_api_key="test-bailian-key",
            qwen_asr_model="test-asr-model",
        )
        conversation = Mock()
        with (
            patch("backend.app.ai.speech.get_settings", return_value=settings),
            patch(
                "backend.app.ai.speech.OmniRealtimeConversation",
                return_value=conversation,
            ) as create_conversation,
        ):
            service = RealtimeSpeechService()
            callback = Mock()
            service.connect_asr(callback)
            service.append_audio(b"pcm")

        create_conversation.assert_called_once()
        self.assertEqual(
            create_conversation.call_args.kwargs["model"],
            "test-asr-model",
        )
        conversation.connect.assert_called_once()
        self.assertEqual(
            conversation.append_audio.call_args.args[0],
            base64.b64encode(b"pcm").decode("ascii"),
        )
        conversation.close.assert_not_called()
        service.close_asr()
        conversation.close.assert_called_once()

    def test_synthesizes_pcm_and_streams_audio_delta(self) -> None:
        settings = Settings(
            active_chat_provider="deepseek",
            ai_bailian_api_key="test-bailian-key",
            qwen_tts_model="test-tts-model",
            qwen_tts_voice="TestVoice",
        )
        client = Mock()

        def commit() -> None:
            callback = create_client.call_args.kwargs["callback"]
            callback.on_event(
                {"type": "response.audio.delta", "delta": "encoded-audio"}
            )
            callback.on_event({"type": "response.done"})

        client.commit.side_effect = commit
        with (
            patch("backend.app.ai.speech.get_settings", return_value=settings),
            patch(
                "backend.app.ai.speech.QwenTtsRealtime",
                return_value=client,
            ) as create_client,
        ):
            audio_chunks: list[str] = []
            RealtimeSpeechService().synthesize("Hello candidate.", audio_chunks.append)

        self.assertEqual(audio_chunks, ["encoded-audio"])
        self.assertEqual(create_client.call_args.kwargs["model"], "test-tts-model")
        client.update_session.assert_called_once()
        self.assertEqual(
            client.update_session.call_args.kwargs["voice"],
            "TestVoice",
        )
        client.close.assert_called_once()


class SpeechSettingsApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

        async def override_get_session():
            async with self.session_factory() as session:
                yield session

        app.dependency_overrides[get_session] = override_get_session
        from httpx import ASGITransport, AsyncClient

        self.client = AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        )
        await self.client.post(
            "/api/v1/auth/register",
            json={
                "email": "speech-user@example.com",
                "password": "a-long-test-password",
            },
        )
        token = await self.client.post(
            "/api/v1/auth/token",
            data={
                "username": "speech-user@example.com",
                "password": "a-long-test-password",
            },
        )
        self.headers = {"Authorization": f"Bearer {token.json()['access_token']}"}

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        app.dependency_overrides.pop(get_session, None)
        await self.engine.dispose()

    async def test_settings_expose_service_status_without_secrets(self) -> None:
        response = await self.client.get(
            "/api/v1/settings/models",
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("active_provider", response.json())
        self.assertNotIn("test-secret", response.text)


if __name__ == "__main__":
    unittest.main()
