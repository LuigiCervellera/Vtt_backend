import asyncio
import os
from dotenv import load_dotenv
from tortoise import Tortoise

load_dotenv()


async def init_db():
    db_url = os.getenv("DATABASE_URL", "postgres://postgres:admin@localhost:5432/vtt_db")
    print(f"Connecting to {db_url}...")
    try:
        await Tortoise.init(
            db_url=db_url,
            modules={"models": ["models"]}
        )
        print("Generating schemas...")
        await Tortoise.generate_schemas()
        print("Schemas generated successfully!")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        await Tortoise.close_connections()

if __name__ == "__main__":
    asyncio.run(init_db())
