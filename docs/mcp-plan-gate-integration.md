# Plan-Gate MCP 接入指南(marshal_plan_review)

把 Marshal 的**概念预算 plan gate** 作为一个 MCP tool 接进任意 agent(Codex / Claude Code / Opencode),
让"每次 plan 完让 Marshal 过一下"成为工具调用。tool 只报**中性成本**,绝不建议做/不做。

## 接入配置

前置:`pip install -e ".[mcp]"`(装 mcp SDK)。server 走 **stdio** —— 不是常驻服务,client
在需要时 fork 一个子进程、用完即退,**无需单独部署**。client 与 server 必须同机(tool 传的
`concepts_dir` / `repo_roots` 路径要在 server 进程能读到的文件系统里)。

### Claude Code —— 推荐用 `claude mcp add`(user scope,任何目录都生效)

```bash
claude mcp add -s user marshal-plan-gate \
  /path/to/marshal/.venv/bin/python -- -m marshal_core.mcp_server
```

把 `/path/to/marshal` 换成你本机 clone 的路径。写进 `~/.claude.json`,从任何目录开
Claude Code 都能用。验证:`claude mcp list` 应显示 `marshal-plan-gate … ✔ Connected`。

> ⚠️ **别用项目根 `.mcp.json`**,除非你总是从 marshal 目录启动 Claude Code —— 它只读
> **启动目录**的 `.mcp.json`,不会递归进子目录;且项目级 server 首次要手动 approve。
> `claude mcp add -s user` 两个坑都没有。

### Codex / Opencode

配置文件名不同(Codex 是 `~/.codex/config.toml`,Opencode 是 `opencode.json` 的 `mcp` 段),
但 command/args 一样:`<你的 marshal venv>/bin/python -m marshal_core.mcp_server`。

## 工具

`marshal_plan_review(concepts_dir, domain_pack, touches, repo_roots?)`

- **调用方 agent 的职责**:把你的 plan 映射成 `touches` —— `[{concept_id, op:"add"|"redefine", importance?, est_scope?}]`。
  这一步是 agent(LLM)的判断,tool 不做(marshal 核无 LLM)。
- **返回(中性成本画像)**:`weighted_concept_cost`(=grounded+hinted,**无单位相对权重,非工期**)、
  `grounded_cost`(redefine,树算,不可 gaming)、`hinted_cost`(add,你的 est_scope 估,需核对)、
  `blast_radius`(传递受影响概念)、`impacted_repos`、`highest_tier_touched`、`unknown_redefines`/`unknown_ops`。
  `verdict` 恒 `cost-only`——**从不建议做/不做**。

## 已验证(真 stdio 协议端到端)

用真 MCP client 生出 `python -m marshal_core.mcp_server` 进程、走完整协议:

- `initialize` + `tools/list` → `['marshal_plan_review']`,描述中性(无 recommend/should)。
- `tools/call marshal_plan_review`(对真实 cowboy 概念树:redefine gas+dual-gas-model + add cell-rent large)
  → `isError=False`,`verdict=cost-only`,`cost=68`(grounded 32 + hinted 36),`tier=constitutional`,`blast=13 概念`。
- 畸形 touch(缺 `op`)经协议 → `isError=True` + 清晰错误("each touch needs 'concept_id' and 'op'"),非 server crash。

> 只读隔离:tool 每次派生进隔离内存 DB,**绝不 mutate 共享 marshal.db**(概念页 markdown 是真相源)。

## 剩余(需真人)

真实接入 Codex/Opencode 并由团队("想法多"的人)试用、收 ≥3 条反馈——这是 §8 S3 验收门的人参与部分,配置与协议已就绪。
