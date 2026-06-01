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


def cmd_invariants(a) -> int:
    scope = {"repo": a.repo, "diff_paths": a.paths}
    invs = _PACK.list_invariants(scope)
    return _emit([
        {"id": i.id, "severity": i.severity, "executor_kind": i.executor_kind,
         "location_repo": i.location_repo, "location_path": i.location_path,
         "location_test": i.location_test, "run_command": i.run_command}
        for i in invs
    ])


def cmd_ratchet_open(a) -> int:
    s = _session()
    try:
        esc = Store(s).open_escape(
            id=a.escape_id, description=a.desc, root_cause_class=a.root_cause,
            change_ref=a.change_ref)
        return _emit({"escape_id": esc.id})
    finally:
        s.close()


def cmd_ratchet_close(a) -> int:
    if not a.spawned_check:
        return _fail("spawned_check is required to close an escape (棘轮纪律)")
    inv = json.loads(a.inv_json)
    inv.setdefault("domain_pack", "cowboy")  # InvariantRegistry.domain_pack 非空
    # 知识核只存引用(repo+path+test-name),不存可执行命令(spec §3.4);
    # 丢弃 InvariantDef 上有、但 InvariantRegistry 表没有的字段(如 run_command)。
    _REGISTRY_FIELDS = {"id", "domain_pack", "domain", "spec_ref", "executor_kind",
                        "location_repo", "location_path", "location_test", "severity",
                        "status"}
    inv = {k: v for k, v in inv.items() if k in _REGISTRY_FIELDS}
    s = _session()
    try:
        store = Store(s)
        store.register_invariant(**inv, origin="ratchet", escape_id=a.escape_id)
        store.close_escape(a.escape_id, spawned_check=a.spawned_check)
        return _emit({"ok": True, "escape_id": a.escape_id,
                      "spawned_check": a.spawned_check})
    finally:
        s.close()


def cmd_gate_record(a) -> int:
    gates = json.loads(a.evidence_json)
    s = _session()
    try:
        store = Store(s)
        run = store.record_gate_run(change_ref=a.change_ref, job_id=a.change_ref,
                                    verdict=a.verdict, evidence={"gates": gates})
        store.audit(event="gate_decision", actor="marshal-skill",
                    decision=a.verdict, refs={"change_ref": a.change_ref})
        return _emit({"run_id": run.id})
    finally:
        s.close()


def cmd_setup(a) -> int:
    home = _marshal_home()
    skill_src = home / ".claude" / "skills" / "marshal"
    link_dir = Path(os.path.expanduser("~")) / ".claude" / "skills"
    link_dir.mkdir(parents=True, exist_ok=True)
    link = link_dir / "marshal"
    if link.is_symlink() or link.exists():
        if link.is_symlink():
            link.unlink()
        else:
            return _fail(f"{link} exists and is not a symlink; remove it manually")
    link.symlink_to(skill_src, target_is_directory=True)

    try:
        import marshal_pack_cowboy.pack  # noqa: F401
        import_ok = True
    except Exception:
        import_ok = False

    return _emit({"ok": True, "symlink": str(link), "target": str(skill_src),
                  "import_ok": import_ok,
                  "hint": None if import_ok else "run: pip install -e . in marshal venv"})


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="marshal")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("classify")
    c.add_argument("--repo", required=True)
    c.add_argument("--paths", nargs="*", default=[])
    c.add_argument("--diff-text", dest="diff_text", default="")
    c.add_argument("--labels", nargs="*", default=[])
    c.set_defaults(func=cmd_classify)

    iv = sub.add_parser("invariants")
    iv.add_argument("--repo", required=True)
    iv.add_argument("--paths", nargs="*", default=[])
    iv.set_defaults(func=cmd_invariants)

    ro = sub.add_parser("ratchet-open")
    ro.add_argument("--escape-id", dest="escape_id", required=True)
    ro.add_argument("--desc", required=True)
    ro.add_argument("--root-cause", dest="root_cause", default="")
    ro.add_argument("--change-ref", dest="change_ref", default=None)
    ro.set_defaults(func=cmd_ratchet_open)

    rc = sub.add_parser("ratchet-close")
    rc.add_argument("--escape-id", dest="escape_id", required=True)
    rc.add_argument("--spawned-check", dest="spawned_check", default="")
    rc.add_argument("--inv-json", dest="inv_json", required=True)
    rc.set_defaults(func=cmd_ratchet_close)

    gr = sub.add_parser("gate-record")
    gr.add_argument("--change-ref", dest="change_ref", required=True)
    gr.add_argument("--verdict", required=True,
                    choices=["pass", "block", "needs_human"])
    gr.add_argument("--evidence-json", dest="evidence_json", default="[]")
    gr.set_defaults(func=cmd_gate_record)

    st = sub.add_parser("setup")
    st.set_defaults(func=cmd_setup)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except Exception as e:  # 边界:把任何确定性失败转成 degraded 信号给 skill
        return _fail(f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    sys.exit(main())
