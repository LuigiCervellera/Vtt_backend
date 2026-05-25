import asyncio
from main import app

async def get_schema():
    client = app.test_client()
    response = await client.get("/openapi.json")
    print("Status:", response.status_code)
    print("Body:", await response.get_data())

asyncio.run(get_schema())
