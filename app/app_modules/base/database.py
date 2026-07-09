import os
from tortoise import Tortoise

async def init_tortoise() -> None:
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL environment variable is not set!")
    await Tortoise.init(
        db_url=db_url,
        modules={"models": ["models"]},
        _enable_global_fallback=True
    )
    # Schema generation should not occur on every production boot due to startup races.
    if os.getenv("GENERATE_SCHEMAS", "False").lower() in ("true", "1", "yes"):
        await Tortoise.generate_schemas()

async def close_tortoise() -> None:
    await Tortoise.close_connections()
