import base64
import json
import threading
from collections.abc import Callable
from typing import Any

import dashscope
from dashscope.audio.qwen_omni import (
    AudioFormat,
    MultiModality,
    OmniRealtimeCallback,
    OmniRealtimeConversation,
)
from dashscope.audio.qwen_omni.omni_realtime import TranscriptionParams
from dashscope.audio.qwen_tts_realtime import (
    AudioFormat as TtsAudioFormat,
    QwenTtsRealtime,
    QwenTtsRealtimeCallback,
)

from backend.app.core.config import get_settings

DASHSCOPE_REALTIME_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"


class _AsrCallback(OmniRealtimeCallback):
    def __init__(self, on_event: Callable[[dict[str, Any]], None]) -> None:
        self._on_event = on_event

    def on_event(self, message: str) -> None:
        try:
            event = json.loads(message)
        except (TypeError, json.JSONDecodeError):
            return
        if isinstance(event, dict):
            self._on_event(event)


class _TtsCallback(QwenTtsRealtimeCallback):
    def __init__(
        self,
        on_event: Callable[[dict[str, Any]], None],
        completed: threading.Event,
    ) -> None:
        self._on_event = on_event
        self._completed = completed
        self.error: RuntimeError | None = None

    def on_event(self, message: dict[str, Any] | str) -> None:
        try:
            event = json.loads(message) if isinstance(message, str) else message
        except (TypeError, json.JSONDecodeError):
            self.error = RuntimeError("DashScope returned an invalid speech event.")
            self._completed.set()
            return
        if not isinstance(event, dict):
            return
        if event.get("type") == "error":
            self.error = RuntimeError("DashScope speech synthesis failed.")
            self._completed.set()
        elif event.get("type") == "response.done":
            self._completed.set()
        self._on_event(event)


class RealtimeSpeechService:
    def __init__(self) -> None:
        settings = get_settings()
        if not settings.ai_bailian_api_key:
            raise RuntimeError("AI_BAILIAN_API_KEY must be set for realtime speech.")
        self._settings = settings
        self._asr: OmniRealtimeConversation | None = None

    def connect_asr(self, on_event: Callable[[dict[str, Any]], None]) -> None:
        callback = _AsrCallback(on_event)
        conversation = OmniRealtimeConversation(
            model=self._settings.qwen_asr_model,
            callback=callback,
            url=DASHSCOPE_REALTIME_URL,
            api_key=self._settings.ai_bailian_api_key,
        )
        self._asr = conversation
        conversation.connect()
        conversation.update_session(
            output_modalities=[MultiModality.TEXT],
            input_audio_format=AudioFormat.PCM_16000HZ_MONO_16BIT,
            enable_input_audio_transcription=True,
            input_audio_transcription_model=self._settings.qwen_asr_model,
            enable_turn_detection=True,
            turn_detection_type="server_vad",
            transcription_params=TranscriptionParams(
                sample_rate=16000,
                input_audio_format="pcm",
            ),
        )

    def append_audio(self, audio: bytes) -> None:
        if self._asr is None:
            raise RuntimeError("Speech recognition session is not connected.")
        self._asr.append_audio(base64.b64encode(audio).decode("ascii"))

    def close_asr(self) -> None:
        if self._asr is not None:
            self._asr.close()
            self._asr = None

    def synthesize(
        self,
        text: str,
        on_audio: Callable[[str], None],
    ) -> None:
        settings = self._settings
        # QwenTtsRealtime reads the process-wide DashScope key when constructed.
        dashscope.api_key = settings.ai_bailian_api_key
        completed = threading.Event()
        callback = _TtsCallback(
            lambda event: self._emit_tts_audio(event, on_audio),
            completed,
        )
        client = QwenTtsRealtime(
            model=settings.qwen_tts_model,
            callback=callback,
        )
        try:
            client.connect()
            client.update_session(
                voice=settings.qwen_tts_voice,
                response_format=TtsAudioFormat.PCM_24000HZ_MONO_16BIT,
                mode="commit",
            )
            client.append_text(text)
            client.commit()
            if not completed.wait(timeout=45):
                raise TimeoutError("DashScope speech synthesis timed out.")
            if callback.error is not None:
                raise callback.error
        finally:
            client.close()

    @staticmethod
    def _emit_tts_audio(
        event: dict[str, Any], on_audio: Callable[[str], None]
    ) -> None:
        if event.get("type") == "response.audio.delta":
            audio = event.get("delta")
            if isinstance(audio, str) and audio:
                on_audio(audio)
