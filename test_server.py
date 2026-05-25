import asyncio
from quart import Quart
from main import app
import sys
import traceback

@app.errorhandler(Exception)
async def handle_exception(e):
    traceback.print_exc()
    return str(e), 500

if __name__ == "__main__":
    app.run(port=5001)
