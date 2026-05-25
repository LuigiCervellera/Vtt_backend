import asyncio
from main import app

async def test_options():
    client = app.test_client()
    response = await client.options(
        "/api/auth/register",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type"
        }
    )
    print("Status:", response.status_code)
    print("Headers:", response.headers)

asyncio.run(test_options())
