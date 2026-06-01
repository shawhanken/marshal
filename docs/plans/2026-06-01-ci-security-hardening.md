# CI 安全加固:Action 版本固定 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 消除 harness 安全告警——把 node CI workflow 中所有 GitHub Action 从可变引用(`@main`/`@stable`)固定到不可变 commit SHA,加最小权限,收敛历史/secret 暴露,并给 Marshal action 打版本 tag。

**Architecture:** 纯配置/供应链加固。三类硬化:(1) **pin-by-SHA**——所有 `uses:` 固定到 40 位 commit SHA(GitHub 官方供应链硬化建议);(2) **least-privilege**——workflow 显式 `permissions: contents: read`;(3) **minimize-exposure**——不再 `fetch-depth: 0` 拉全史,改为按需取 base commit + 两点 diff。Marshal action 打 `v0.1.0` tag,node pin 到其 SHA。

**Tech Stack:** GitHub Actions YAML;git tag;`gh`/`git` 解析 SHA。

**关键决策(D-S1):** 固定到**完整 commit SHA**(非 tag)。tag 可被移动,SHA 不可变——这是 GitHub「security hardening for GitHub Actions」对第三方 action 的明确建议。第一方 action(我们自己的 marshal)亦同等处理,保持一致。

**前置状态:** 重构已完成。node `feat/marshal-econ-invariants` 的 `.github/workflows/marshal-econ.yml` 当前用 `shawhanken/marshal@main`(未固定)、`actions/checkout@v4`、`dtolnay/rust-toolchain@stable`,且 `fetch-depth: 0`。marshal `feat/walking-skeleton-econ` 含 `action.yml` + reporter。

**验证边界(诚实声明):** 本 plan 改动为 CI 配置,**无法在本地真跑 GitHub Actions**。可验证项限于:YAML 语法有效、无浮动引用残留、被 pin 的 SHA 真实可解析、权限块存在。真实 CI 行为须待推送后在 GitHub 上验证。

**安全参考:** GitHub Docs — Security hardening for GitHub Actions(pin actions to full-length commit SHA;set minimal `GITHUB_TOKEN` permissions)。

---

## File Structure

**marshal/(本仓库)**
```
.github/actions/... (无新增;action.yml 在仓库根)
action.yml                              # 修改: diff 改两点, 减少对全史依赖
docs/SECURITY.md                        # 新建: 接入方安全须知 (pin/权限/fork-PR secret)
(git tag v0.1.0 — 不改文件, 记录 SHA)
```

**node/(独立 repo, 分支 feat/marshal-econ-invariants)**
```
.github/workflows/marshal-econ.yml      # 修改: 全 pin SHA + permissions + 按需 fetch base
```

---

## Task 1: Marshal action 自硬化(两点 diff)+ 版本 tag + 安全文档

**Files:**
- Modify: `marshal/action.yml`
- Create: `marshal/docs/SECURITY.md`

> 当前 action 的 diff 步骤用三点 `base...HEAD`(需 merge-base,依赖较深历史)。改为两点 `base HEAD`(只需两个 commit 对象在场),配合接入方按需取 base,即可避免 `fetch-depth: 0` 拉全史。

- [ ] **Step 1: 改 action.yml 的 diff 步骤为两点 + 不假设全史**

编辑 `marshal/action.yml` 的 `Compute diff paths` 步骤(整步替换):
```yaml
    - name: Compute diff paths
      id: diff
      shell: bash
      run: |
        BASE="${{ inputs.base-ref }}"
        if [ -z "$BASE" ]; then BASE="$(git rev-parse HEAD~1 2>/dev/null || echo HEAD)"; fi
        # 两点 diff: 只需 BASE 与 HEAD 两个 commit 对象, 不需要完整历史/merge-base
        PATHS=$(git diff --name-only "$BASE" HEAD 2>/dev/null | paste -sd, -)
        echo "paths=$PATHS" >> "$GITHUB_OUTPUT"
```
(其余步骤不变。)

- [ ] **Step 2: 校验 action.yml YAML 有效**

Run: `cd marshal && python3 -c "import yaml,sys; yaml.safe_load(open('action.yml')); print('action.yml OK')"`
Expected: `action.yml OK`(若环境无 pyyaml:`./.venv/bin/python -c ...`,venv 里随 fastapi 装了 pyyaml 依赖链;若仍无则 `./.venv/bin/pip install pyyaml` 后再跑)。

- [ ] **Step 3: 写接入方安全文档** `marshal/docs/SECURITY.md`:
```markdown
# Marshal 接入安全须知

接入 Marshal(在你的 repo CI 里 `uses: shawhanken/marshal@...`)时,遵循以下硬化:

## 1. 固定到 commit SHA(必须)
不要用 `@main`/`@v1` 等可变引用。固定到完整 40 位 commit SHA:
```yaml
- uses: shawhanken/marshal@<40-char-sha>   # v0.1.0
```
查 SHA:`git ls-remote https://github.com/shawhanken/marshal v0.1.0`,或本地 `git rev-list -1 v0.1.0`。
理由:tag 可被移动,SHA 不可变——杜绝「引用被悄悄替换为恶意代码」的供应链风险。

## 2. 最小权限(必须)
workflow 顶层声明:
```yaml
permissions:
  contents: read
```
Marshal reporter 只需读代码 + 出网调大脑,不需要写权限或其他 scope。

## 3. fork PR 行为(知悉)
GitHub 对来自 fork 的 `pull_request` **不下发 secrets**(含 `MARSHAL_BRAIN_URL`)。
此时 reporter 无法到达大脑,会在影子模式下静默降级(不阻断 PR)。这是预期且安全的——
**切勿**为给 fork 下发 secret 而改用 `pull_request_target`(那会让 fork 代码拿到 secret,是已知高危反模式)。

## 4. 最小历史暴露
Marshal action 用两点 diff,只需 base 与 head 两个 commit;无需 `fetch-depth: 0` 拉全史。
按需取 base:`git fetch --depth=1 origin <base-sha>`。

## 5. 大脑 URL
`MARSHAL_BRAIN_URL` 经 secret 注入,勿硬编码进 workflow。生产建议 https 且网络可达性受控。
```

- [ ] **Step 4: 提交 action + 文档**

```bash
cd marshal && git add action.yml docs/SECURITY.md
git commit -m "harden: action two-dot diff (no full-history dep) +接入安全文档"
```

- [ ] **Step 5: 打版本 tag 并记录 SHA(供 node pin)**

```bash
cd marshal
git tag -a v0.1.0 -m "Marshal v0.1.0: walking skeleton + generic reporter action"
MARSHAL_SHA=$(git rev-list -1 v0.1.0)
echo "PIN THIS SHA IN NODE: $MARSHAL_SHA"
```
把打印出的 `MARSHAL_SHA`(40 位)记进报告——Task 2 要用它。
注:tag 是本地的;真实 CI 解析该 SHA 前,marshal 分支/ tag 需推送到 origin(本 plan 不推送,记为上线前动作)。

---

## Task 2: node workflow 全面 pin SHA + 最小权限 + 收敛暴露

**Files:**
- Modify: `node/.github/workflows/marshal-econ.yml`

> node 在 `feat/marshal-econ-invariants` 分支。需把 3 个 action 全 pin 到 SHA。**外部 action(checkout / rust-toolchain)的 SHA 必须实时解析,不可臆造。**

- [ ] **Step 1: 解析三个 action 的 commit SHA**

```bash
# Marshal (第一方): 用 Task 1 记录的 MARSHAL_SHA
echo "marshal: $MARSHAL_SHA"

# actions/checkout 的某个稳定 tag → SHA (优先 gh; 无 gh 用 git ls-remote)
git ls-remote https://github.com/actions/checkout refs/tags/v4.2.2 | awk '{print "checkout v4.2.2:", $1}'

# dtolnay/rust-toolchain: 该 action 用滚动 tag, 取其当前 stable 分支或某 commit
git ls-remote https://github.com/dtolnay/rust-toolchain refs/heads/master | awk '{print "rust-toolchain master:", $1}'
```
把三行输出的 40 位 SHA 记进报告。下一步把它们填进 workflow(注释标明对应版本)。
(若 `git ls-remote` 因网络不可达失败:改用 `gh api repos/actions/checkout/git/ref/tags/v4.2.2 --jq .object.sha`;仍不可达则报 NEEDS_CONTEXT,由主控提供 SHA。)

- [ ] **Step 2: 重写 workflow(用上一步解析出的真实 SHA 替换 `<...SHA>`)**

`node/.github/workflows/marshal-econ.yml`(整文件替换;把 4 处 `<...SHA>` 换成 Step 1 实际值):
```yaml
name: marshal-econ (shadow)
on:
  pull_request:
    paths:
      - "execution/**"
      - "runner/**"

# 最小权限: reporter 只需读代码 + 出网
permissions:
  contents: read

jobs:
  econ-invariants:
    runs-on: ubuntu-latest
    continue-on-error: true                 # 影子模式: 不阻断 PR
    steps:
      - uses: actions/checkout@<CHECKOUT_SHA>          # v4.2.2
        with:
          fetch-depth: 1                               # 仅取 head, base 按需补
      - name: Fetch base commit for diff
        run: git fetch --depth=1 origin ${{ github.event.pull_request.base.sha }}
      - uses: dtolnay/rust-toolchain@<RUST_TOOLCHAIN_SHA>   # stable @ <date>
        with:
          toolchain: stable
      - uses: shawhanken/marshal@<MARSHAL_SHA>         # v0.1.0
        with:
          brain-url: ${{ secrets.MARSHAL_BRAIN_URL }}
          repo: node
          base-ref: ${{ github.event.pull_request.base.sha }}
```
注:`dtolnay/rust-toolchain` pin 到 SHA 后需用 `with: toolchain: stable` 指定工具链(该 action SHA 固定后不再从 tag 推断版本)。

- [ ] **Step 3: 校验 YAML + 无浮动引用残留**

Run: `cd /home/ubuntu/workspace/node && python3 -c "import yaml; yaml.safe_load(open('.github/workflows/marshal-econ.yml')); print('yaml OK')"`
Expected: `yaml OK`。
Run: `grep -nE '@(main|master|stable|v[0-9]+)$' .github/workflows/marshal-econ.yml || echo "no floating refs OK"`
Expected: `no floating refs OK`(所有 `uses:` 都以 40 位 SHA 结尾)。
Run: `grep -nE 'uses: .+@[0-9a-f]{40}' .github/workflows/marshal-econ.yml | wc -l`
Expected: `3`(三个 action 全 pin)。

- [ ] **Step 4: 确认 permissions 存在**

Run: `grep -nA1 '^permissions:' .github/workflows/marshal-econ.yml`
Expected: 显示 `permissions:` + `contents: read`。

- [ ] **Step 5: 提交(node repo)**

```bash
cd /home/ubuntu/workspace/node
git add .github/workflows/marshal-econ.yml
git commit -m "harden(ci): pin all actions to SHA, least-privilege perms, minimal fetch"
```

---

## Task 3: 验证 + 文档登记

**Files:**
- Modify: `marshal/docs/README.md`

- [ ] **Step 1: marshal 全量回归(确保加固未碰坏逻辑)**

Run: `cd marshal && ./.venv/bin/pytest -q`
Expected: 23 passed(本 plan 不改 Python,应不变)。

- [ ] **Step 2: 复核 node A 类不变量未受影响**

Run: `cd /home/ubuntu/workspace/node && cargo test -p cowboy-execution econ_invariants 2>&1 | tail -3`
Expected: `3 passed`。

- [ ] **Step 3: docs 索引登记本 plan**

在 `marshal/docs/README.md` plans 段落后追加一句,指向 `plans/2026-06-01-ci-security-hardening.md`,注明「CI 供应链加固:全 action pin SHA + 最小权限 + 暴露收敛(已实现)」,并链接 `SECURITY.md`。

- [ ] **Step 4: 提交**

```bash
cd marshal && git add docs/README.md
git commit -m "docs: register ci-security-hardening plan + link SECURITY.md"
```

---

## 验收标准

- [ ] node workflow 中 **3 个 action 全部 pin 到 40 位 commit SHA**,`grep` 无 `@main/@master/@stable/@vN` 残留。
- [ ] workflow 含 `permissions: contents: read`。
- [ ] `fetch-depth: 1` + 按需 `git fetch` base;action 用两点 diff(不依赖全史)。
- [ ] `SECURITY.md` 写明 pin/权限/fork-PR secret/历史暴露四条须知。
- [ ] marshal 23 passed;node econ proptest 3 passed(加固不碰功能)。
- [ ] marshal 打了 `v0.1.0` tag,SHA 已记录。

## 范围外 / 上线前仍需

- **推送** marshal 分支/tag 到 origin(本 plan 不推送;node CI 解析 SHA 前必须可达)。
- 真实 GitHub Actions 行为验证(须推送后在 GitHub 上跑一次)。
- 大脑 `/webhook`、`/results` 的鉴权/签名(独立安全 plan)。
- `MARSHAL_BRAIN_URL` 之外若将来引入更敏感 secret 的轮转策略。
