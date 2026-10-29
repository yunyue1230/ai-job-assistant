from collections.abc import AsyncIterator
from typing import Protocol

import redis.asyncio as redis
from redis.exceptions import ResponseError

from backend.app.core.config import get_settings

ANALYSIS_STREAM = "interview:analysis"
ANALYSIS_GROUP = "analysis-workers"
KNOWLEDGE_STREAM = "interview:knowledge-ingestion"
KNOWLEDGE_GROUP = "knowledge-workers"


class TaskQueue(Protocol):
    async def enqueue_analysis(self, job_id: str) -> None: ...

    async def close(self) -> None: ...


class RedisStreamQueue:
    def __init__(self, client: redis.Redis) -> None:
        self.client = client

    @classmethod
    def from_settings(cls) -> "RedisStreamQueue":
        settings = get_settings()
        return cls(redis.from_url(settings.redis_url, decode_responses=True))

    async def enqueue_analysis(self, job_id: str) -> None:
        await self.client.xadd(ANALYSIS_STREAM, {"job_id": job_id})

    async def enqueue_document(self, document_id: str) -> None:
        await self.client.xadd(KNOWLEDGE_STREAM, {"document_id": document_id})

    async def close(self) -> None:
        await self.client.aclose()

    async def ensure_consumer_group(self) -> None:
        await self._ensure_group(ANALYSIS_STREAM, ANALYSIS_GROUP)

    async def ensure_knowledge_consumer_group(self) -> None:
        await self._ensure_group(KNOWLEDGE_STREAM, KNOWLEDGE_GROUP)

    async def _ensure_group(self, stream: str, group: str) -> None:
        try:
            await self.client.xgroup_create(stream, group, id="0", mkstream=True)
        except ResponseError as error:
            if "BUSYGROUP" not in str(error):
                raise

    async def read_new(
        self, consumer_name: str, block_ms: int = 5000
    ) -> list[tuple[str, dict[str, str]]]:
        return await self._read_new(
            ANALYSIS_STREAM, ANALYSIS_GROUP, consumer_name, block_ms
        )

    async def read_new_documents(
        self, consumer_name: str, block_ms: int = 5000
    ) -> list[tuple[str, dict[str, str]]]:
        return await self._read_new(
            KNOWLEDGE_STREAM, KNOWLEDGE_GROUP, consumer_name, block_ms
        )

    async def _read_new(
        self, stream: str, group: str, consumer_name: str, block_ms: int
    ) -> list[tuple[str, dict[str, str]]]:
        messages = await self.client.xreadgroup(
            group,
            consumer_name,
            {stream: ">"},
            count=1,
            block=block_ms,
        )
        return [
            (message_id, fields)
            for _, entries in messages
            for message_id, fields in entries
        ]

    async def reclaim_pending(
        self, consumer_name: str, min_idle_ms: int = 60_000
    ) -> list[tuple[str, dict[str, str]]]:
        return await self._reclaim_pending(
            ANALYSIS_STREAM, ANALYSIS_GROUP, consumer_name, min_idle_ms
        )

    async def reclaim_pending_documents(
        self, consumer_name: str, min_idle_ms: int = 60_000
    ) -> list[tuple[str, dict[str, str]]]:
        return await self._reclaim_pending(
            KNOWLEDGE_STREAM, KNOWLEDGE_GROUP, consumer_name, min_idle_ms
        )

    async def _reclaim_pending(
        self, stream: str, group: str, consumer_name: str, min_idle_ms: int
    ) -> list[tuple[str, dict[str, str]]]:
        response = await self.client.xautoclaim(
            stream,
            group,
            consumer_name,
            min_idle_time=min_idle_ms,
            start_id="0-0",
            count=1,
        )
        _, entries, *_ = response
        return [(message_id, fields) for message_id, fields in entries]

    async def acknowledge(self, message_id: str) -> None:
        await self.client.xack(ANALYSIS_STREAM, ANALYSIS_GROUP, message_id)

    async def acknowledge_document(self, message_id: str) -> None:
        await self.client.xack(KNOWLEDGE_STREAM, KNOWLEDGE_GROUP, message_id)


async def get_task_queue() -> AsyncIterator[RedisStreamQueue]:
    queue = RedisStreamQueue.from_settings()
    try:
        yield queue
    finally:
        await queue.close()
