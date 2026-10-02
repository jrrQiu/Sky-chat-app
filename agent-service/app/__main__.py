"""Service entrypoint: `python -m app`.

Exists because uvicorn's own loop choice is unusable for the durable PostgreSQL
backend on Windows. See `app/runtime.py`.
"""

from app.runtime import run_server

if __name__ == "__main__":
    run_server()
