"""Central DB-path resolution, shared by the CLI, the FastAPI dashboard, and the worker.

Precedence:
  1. $MARSHAL_DB — explicit override, always wins.
  2. $MARSHAL_HOME/marshal.db — MARSHAL_HOME defaults to this checkout's root, so the
     path is absolute and cwd-independent.

Point the dashboard/worker/CLI at a specific database via $MARSHAL_DB (or $MARSHAL_HOME);
no path is hardcoded.
"""
import os
from pathlib import Path


def marshal_home() -> Path:
    env = os.environ.get("MARSHAL_HOME")
    if env:
        return Path(env)
    # this file is <home>/src/marshal_core/config.py
    return Path(__file__).resolve().parents[2]


def db_url() -> str:
    if os.environ.get("MARSHAL_DB"):
        return os.environ["MARSHAL_DB"]
    return f"sqlite:///{marshal_home() / 'marshal.db'}"

# review-queue smoke: db_url/marshal_home read env fresh each call (test PR)
