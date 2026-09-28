import os

import uvicorn

from app import app


if __name__ == "__main__":
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8010"))
    reload_enabled = os.getenv("RELOAD", "0") == "1"
    uvicorn.run(app, host=host, port=port, reload=reload_enabled)