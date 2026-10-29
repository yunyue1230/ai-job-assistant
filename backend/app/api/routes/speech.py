import asyncio
import json
import logging
from typing import Any

import jwt
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from openai import OpenAIError
from sqlalchemy import select

from backend.app.ai.model_providers import create_chat_client
from backend.app.ai.speech import RealtimeSpeechService
from backend.app.core.config import get_settings
from backend.app.core.security import decode_access_token
from backend.app.db.session import SessionFactory
from backend.app.models.user import User

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/speech", tags=["speech"])
MAX_AUDIO_FRAME_BYTES = 32 * 1024


async def _active_user(token: str) -> bool:
    try:
        user_id = decode_access_token(token)
    except (jwt.InvalidTokenError, ValueError):
        return False
    async with SessionFactory() as session:
        user = await session.scalar(select(User).where(User.id == user_id))
        return user is not None and user.is_active


@router.websocket("/realtime")
async def realtime_speech(websocket: WebSocket) -> None:
    offered_protocols = websocket.scope.get("subprotocols", [])
    token_protocol = next(
        (protocol for protocol in offered_protocols if protocol.startswith("auth.")),
        None,
    )
    token = token_protocol.removeprefix("auth.") if token_protocol else ""
    if not token or not await _active_user(token):
        await websocket.close(code=1008, reason="Authentication required.")
        return

    settings = get_settings()
    if not settings.ai_bailian_api_key:
        await websocket.close(code=1011, reason="Realtime speech is not configured.")
        return

    await websocket.accept(subprotocol="interview-guide")
    try:
        start_message = await asyncio.wait_for(websocket.receive_json(), timeout=10)
    except (asyncio.TimeoutError, WebSocketDisconnect):
        await websocket.close(code=1008, reason="Speech session setup timed out.")
        return
    if not isinstance(start_message, dict) or start_message.get("type") != "start":
        await websocket.close(code=1003, reason="Expected speech session settings.")
        return

    role = str(start_message.get("role", "software engineer")).strip()[:120]
    context = str(start_message.get("context", "")).strip()[:3000]
    if not role:
        role = "software engineer"

    loop = asyncio.get_running_loop()
    send_lock = asyncio.Lock()
    response_lock = asyncio.Lock()
    response_tasks: set[asyncio.Task[None]] = set()
    history: list[dict[str, str]] = [
        {
            "role": "system",
            "content": (
                "You are a concise, supportive interviewer conducting a spoken "
                f"mock interview for the role of {role}. Ask one focused follow-up "
                "question at a time, react to the candidate's latest answer, and "
                "keep spoken replies under 100 words. Do not claim to be human."
                + (f"\nInterview context:\n{context}" if context else "")
            ),
        }
    ]

    async def send_message(payload: dict[str, Any]) -> bool:
        async with send_lock:
            try:
                await websocket.send_json(payload)
            except (RuntimeError, WebSocketDisconnect):
                return False
        return True

    def schedule_message(payload: dict[str, Any]) -> None:
        def create_task() -> None:
            task = asyncio.create_task(send_message(payload))
            response_tasks.add(task)
            task.add_done_callback(response_tasks.discard)

        try:
            loop.call_soon_threadsafe(create_task)
        except RuntimeError:
            logger.debug("Speech connection event arrived after its event loop closed.")

    async def respond_to_transcript(text: str) -> None:
        async with response_lock:
            try:
                if not await send_message({"type": "transcript.final", "text": text}):
                    return
                history.append({"role": "user", "content": text[:2000]})
                client, model = create_chat_client()
                response = await run_in_threadpool(
                    client.chat.completions.create,
                    model=model,
                    temperature=0.5,
                    max_tokens=300,
                    messages=[history[0], *history[1:][-10:]],
                )
                answer = response.choices[0].message.content
                if not answer or not answer.strip():
                    raise RuntimeError("The chat provider returned an empty response.")
                answer = answer.strip()
                history.append({"role": "assistant", "content": answer})
                if not await send_message(
                    {"type": "assistant.text", "text": answer}
                ):
                    return
                speech = RealtimeSpeechService()
                await run_in_threadpool(
                    speech.synthesize,
                    answer,
                    lambda audio: schedule_message(
                        {
                            "type": "assistant.audio",
                            "audio": audio,
                            "sample_rate": 24000,
                        }
                    ),
                )
                await send_message({"type": "assistant.done"})
            except WebSocketDisconnect:
                return
            except (OpenAIError, RuntimeError, TimeoutError, OSError):
                logger.warning("Realtime interview response failed.")
                await send_message(
                    {
                        "type": "error",
                        "message": (
                            "The AI response could not be completed. Check the "
                            "configured chat and Bailian services, then try again."
                        ),
                    }
                )

    def on_asr_event(event: dict[str, Any]) -> None:
        event_type = event.get("type")
        if event_type in {
            "conversation.item.input_audio_transcription.text",
            "conversation.item.input_audio_transcription.delta",
        }:
            text = event.get("text") or event.get("delta") or event.get("transcript")
            if isinstance(text, str) and text:
                schedule_message({"type": "transcript.partial", "text": text})
        elif (
            event_type
            == "conversation.item.input_audio_transcription.completed"
        ):
            text = event.get("transcript")
            if isinstance(text, str) and text.strip():
                def launch_response() -> None:
                    task = asyncio.create_task(respond_to_transcript(text.strip()))
                    response_tasks.add(task)
                    task.add_done_callback(response_tasks.discard)

                try:
                    loop.call_soon_threadsafe(launch_response)
                except RuntimeError:
                    logger.debug("Speech event arrived after its event loop closed.")
        elif event_type == "error":
            schedule_message(
                {
                    "type": "error",
                    "message": "DashScope speech recognition returned an error.",
                }
            )

    speech = RealtimeSpeechService()
    try:
        await run_in_threadpool(speech.connect_asr, on_asr_event)
        await send_message(
            {
                "type": "ready",
                "input_sample_rate": 16000,
                "output_sample_rate": 24000,
            }
        )
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break
            audio = message.get("bytes")
            if audio is not None:
                if len(audio) > MAX_AUDIO_FRAME_BYTES:
                    await send_message(
                        {"type": "error", "message": "Audio frame is too large."}
                    )
                    continue
                await run_in_threadpool(speech.append_audio, audio)
            elif message.get("text"):
                try:
                    control = json.loads(message["text"])
                except (json.JSONDecodeError, KeyError, TypeError):
                    continue
                if isinstance(control, dict) and control.get("type") == "stop":
                    break
    except WebSocketDisconnect:
        pass
    except (RuntimeError, TimeoutError, OSError):
        logger.warning("Realtime speech connection failed.")
        try:
            await send_message(
                {
                    "type": "error",
                    "message": (
                        "Could not connect to realtime speech. Verify "
                        "AI_BAILIAN_API_KEY and the configured Qwen models."
                    ),
                }
            )
        except WebSocketDisconnect:
            pass
    finally:
        for task in tuple(response_tasks):
            task.cancel()
        if response_tasks:
            await asyncio.gather(*response_tasks, return_exceptions=True)
        try:
            await run_in_threadpool(speech.close_asr)
        except (RuntimeError, OSError):
            logger.warning("Could not cleanly close the realtime speech session.")
