import os
from tortoise import Tortoise

async def init_tortoise() -> None:
    db_url = os.getenv("DATABASE_URL", "postgres://postgres:admin@localhost:5432/vtt_db")
    await Tortoise.init(
        db_url=db_url,
        modules={"models": ["models"]},
        _enable_global_fallback=True
    )
    await Tortoise.generate_schemas()

async def close_tortoise() -> None:
    await Tortoise.close_connections()
