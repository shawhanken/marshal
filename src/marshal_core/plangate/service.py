"""Plan-gate 确定性核心 —— CLI 与 MCP 共享的单一入口(避免 F1 那种多份隔离实现漂移)。
路径校验 + 隔离内存 DB derive + concept_budget。只读查询, 绝不 mutate 共享 marshal.db。"""
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from ..knowledge.models import ensure_schema
from ..knowledge.store import Store
from ..concept.sync import derive_db
from .budget import concept_budget


@contextmanager
def isolated_store(concepts_dir: str, domain_pack: str, repo_roots: dict[str, str] | None):
    """派生进隔离内存 DB, yield store, 退出时 close session + dispose engine。
    CLI 与 MCP 的只读 derive 共用这一份(F1 教训: 别让隔离-derive 出现多份漂移)。"""
    eng = create_engine("sqlite:///:memory:")
    ensure_schema(eng)
    s = sessionmaker(bind=eng)()
    try:
        store = Store(s)
        derive_db(concepts_dir, domain_pack, store, repo_roots or {})
        yield store
    finally:
        s.close()
        eng.dispose()


def plan_review(concepts_dir: str, repo_roots: dict[str, str],
                domain_pack: str, touches: list[dict]) -> dict:
    """给定概念页目录 + 触及集, 返回中性概念预算(cost-only)。
    repo_roots 可选(budget 用 anchor 的 repo, 不看 verified/doc_only)。路径 typo → ValueError。"""
    if not Path(concepts_dir).is_dir():
        raise ValueError(f"--concepts-dir not a directory: {concepts_dir}")
    for repo, path in (repo_roots or {}).items():
        if not Path(path).is_dir():
            raise ValueError(f"repo-root path not a directory: {repo}={path}")
    for t in touches:
        if "concept_id" not in t or "op" not in t:
            raise ValueError(f"each touch needs 'concept_id' and 'op'; got: {t}")

    with isolated_store(concepts_dir, domain_pack, repo_roots) as store:
        return concept_budget(store, domain_pack, touches)
