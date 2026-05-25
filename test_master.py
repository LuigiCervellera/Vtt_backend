import asyncio
from tortoise import Tortoise
from models import Campaign

async def main():
    await Tortoise.init(
        db_url="sqlite://db.sqlite3",
        modules={"models": ["models"]}
    )
    c = await Campaign.first()
    if c:
        print(f"Type of master_id: {type(c.master_id)}, value: {c.master_id}")
    else:
        print("No campaigns")
        
asyncio.run(main())
