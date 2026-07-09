import os
import sys

# Ensure root path is in sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.bootstrap import create_app

app = create_app()

if __name__ == "__main__":
    debug_mode = os.getenv("QUART_DEBUG", "False").lower() in ("true", "1", "yes")
    app.run(host="127.0.0.1", port=5000, debug=debug_mode)