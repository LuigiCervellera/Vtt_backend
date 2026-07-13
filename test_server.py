import asyncio
import os
from quart import Quart
from main import app
import sys
import traceback

@app.errorhandler(Exception)
async def handle_exception(e):
    traceback.print_exc()
    if os.getenv("QUART_DEBUG", "False").lower() in ("true", "1", "yes"):
        return str(e), 500
    return "Errore interno del server", 500

if __name__ == "__main__":
    app.run(port=5001)

