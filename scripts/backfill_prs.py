#!/usr/bin/env python
"""Backfill repo/PR identity onto inbox gate_runs via GitHub (needs $GITHUB_TOKEN).

Resolves the PR for gate_runs whose evidence has no repo/PR by asking GitHub which PR a
commit SHA belongs to, and caches the result into evidence['_backfill'] so the inbox
shows `repo #pr` without repeating the API call.

Usage:
  MARSHAL_DB="sqlite:////home/ubuntu/workspace/marshal/marshal.db" \\
  GITHUB_TOKEN=ghp_xxx venv/bin/python scripts/backfill_prs.py [limit]
"""
import sys

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from marshal_core.config import db_url
from marshal_core.github_backfill import backfill
from marshal_core.knowledge.models import ensure_schema


def main() -> None:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    engine = create_engine(db_url())
    ensure_schema(engine)
    with sessionmaker(bind=engine)() as s:
        n = backfill(s, limit=limit)
    print(f"backfilled repo/pr onto {n} gate_run(s)")


if __name__ == "__main__":
    main()
