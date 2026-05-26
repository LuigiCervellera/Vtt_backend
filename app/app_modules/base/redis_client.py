import os
import redis.asyncio as redis_async
from typing import Optional

class RedisManager:
    def __init__(self) -> None:
        self.client: Optional[redis_async.Redis] = None

    async def init(self) -> None:
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self.client = await redis_async.from_url(redis_url)

    async def close(self) -> None:
        if self.client:
            await self.client.aclose()
            self.client = None

redis_manager = RedisManager()
