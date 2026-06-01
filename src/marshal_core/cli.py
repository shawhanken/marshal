"""Marshal 薄 CLI — skill 的确定性执行器。JSON 出入,错误非零退出。

db 路径解析为绝对 $MARSHAL_HOME/marshal.db,与 cwd 无关。
"""
import argparse
import json
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from marshal_core.knowledge.models import Base
from marshal_core.knowledge.store import Store
from marshal_pack_cowboy.pack import CowboyPack

_PACK = CowboyPack()


def _marshal_home() -> Path:
    env = os.environ.get("MARSHAL_HOME")
    if env:
        return Path(env)
    # cli.py 在 <home>/src/marshal_core/cli.py
    return Path(__file__).resolve().parents[2]


def _db_url() -> str:
    if os.environ.get("MARSHAL_DB"):
        return os.environ["MARSHAL_DB"]
    return f"sqlite:///{_marshal_home() / 'marshal.db'}"


def _session():
    engine = create_engine(_db_url())
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _emit(obj) -> int:
    print(json.dumps(obj, ensure_ascii=False))
    return 0


def _fail(msg: str) -> int:
    print(json.dumps({"error": msg}, ensure_ascii=False))
    return 1


def cmd_classify(a) -> int:
    scope = {"repo": a.repo, "diff_paths": a.paths, "diff_text": a.diff_text or "",
             "labels": a.labels or []}
    return _emit(_PACK.classify_detailed(scope))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="marshal")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("classify")
    c.add_argument("--repo", required=True)
    c.add_argument("--paths", nargs="*", default=[])
    c.add_argument("--diff-text", dest="diff_text", default="")
    c.add_argument("--labels", nargs="*", default=[])
    c.set_defaults(func=cmd_classify)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except Exception as e:  # 边界:把任何确定性失败转成 degraded 信号给 skill
        return _fail(f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    sys.exit(main())
