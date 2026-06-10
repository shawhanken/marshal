# 流 A — 门禁评估细节

## 取 diff
- 无参:`base=$(git merge-base HEAD origin/main 2>/dev/null || git merge-base HEAD origin/devnet)`;`git diff --name-only $base...HEAD` 取改动路径;`git diff $base...HEAD` 取 diff_text。
- `<PR#>`:**先定 repo**。`/marshal <PR#>` 默认 `R=cowboyinc/node`;`/marshal <repo> <PR#>` / `<repo>#<PR#>` / PR-URL 则 `R=cowboyinc/<repo>`(URL 自带 owner/repo)。
  - `gh pr diff <PR#> -R $R --name-only` 取路径;`gh pr diff <PR#> -R $R` 取 diff_text;`gh pr view <PR#> -R $R --json headRefOid -q .headRefOid` 取 change_ref。
  - `cli classify/invariants --repo <repo>` 的 `<repo>` 必须与 `$R` 一致(用裸名 runner/cbss/cbfs/node…,不带 owner)。
  - **不能靠 cwd**:`gh` 无 `-R` 时按当前目录 remote 解析,会误落到 node。务必显式 `-R`。
- 多 repo:本地分支 diff 可能跨多个 git 顶层目录;按 repo 分组,各自走分级+不变量(PR 模式则单 repo,由 `$R` 决定)。

## 调 CLI
- `"$PY" -m marshal_core.cli classify --repo <r> --paths <p1> <p2> --diff-text "<截断的diff>" --labels <l1>`
- `"$PY" -m marshal_core.cli invariants --repo <r> --paths <p1> <p2>`

## CI/CD 安全(diff 命中 `.github/workflows/**` 时**必做**)

CI 改动是供应链攻击面。分级器只看 diff 窗口会漏掉危险**组合**(不可信触发 × {自定义/self-hosted runner | secret | 跨仓特权 dispatch}),因为关键构造常在 diff 3 行窗口外(node #649 即栽于此:Almanax 抓到 coverage.yml 的 self-hosted-runner-on-`pull_request` HIGH,Marshal 漏报)。所以:

1. **传整文件给威胁模型(P2)**:对每个改动的 workflow,在 PR head 取整文件喂 classify:
   - `gh api repos/cowboyinc/<repo>/contents/<wf-path>?ref=<headSHA> -q .content | base64 -d > /tmp/<wf>`
   - `classify … --workflow-file "<wf-path>=/tmp/<wf>"`(可重复多次,每个改动 workflow 一条)。
   - classify 会按 **job 粒度**做可达性推理(push/delete/main-devnet 钉死的 job 不算 PR 可达;`github.actor` 白名单在该 job `if:` 内则清除 open-dispatch),命中即在 `security_hazards` 返回 `ci.*` 危险点并升 tier=high。
2. **确定性后盾 zizmor(P0)**:`cli ci-scan --paths /tmp/<wf> …`。
   - 装了 zizmor → 把其 findings 折进 GateDecision(与 `ci.*` hazard 互证)。
   - 没装(返回 `degraded:true`)→ 该 CI 门禁记 degraded,verdict 至少 needs_human,并提示 `pipx install zizmor`。**绝不**因 zizmor 缺失就当 CI 安全审过。
3. **把 `ci.*` hazard 的 `prompt` 注入步骤 4 的对抗 review**(security lens):这些是否定性属性,不变量门禁抓不到,只能 review 裁定。
4. CI 安全发现是 review-lens 结论:确认的 HIGH(如 self-hosted-runner-on-PR)→ needs_human(高危终审归人)。

## 跑不变量(默认在被审 checkout 的干净 worktree 跑)

> **绝不在主工作树 `cd <workspace>/<repo>` 直接跑不变量。** 主树的 checkout 常落后于 PR head / `origin/devnet`(可能缺整个 `*_invariants.rs` 模块),`cargo test … -- --exact` 会命中不到测试而 `running 0 tests`,naive 读成 degraded —— **假阳性**(2026-06-05 PR #604 即栽在此)。必须在**被审代码本身的 checkout** 上跑。

### 1) 为每个 repo 建一次干净 worktree(复用给该 repo 的所有不变量)
按不变量的 `location_repo` 选 ref:
- **`location_repo` == 本次被审 repo**(普通不变量):
  - PR 模式:ref = 上面取到的 `headRefOid`。
    `cd <workspace>/<location_repo> && git fetch -q origin <headSHA> 2>/dev/null; git worktree add --detach /tmp/marshal-<location_repo>-<pr> <headSHA>`
  - 本地分支模式(`/marshal` 无参):ref = `HEAD`(当前分支)。
    `cd <workspace>/<repo> && git worktree add --detach /tmp/marshal-<repo>-head HEAD`
    (worktree 隔离未提交改动;若刻意要审工作区脏改动,才回退到主树并在摘要里注明。)
- **`location_repo` != 被审 repo**(契约/跨 repo 不变量):PR head 不存在于该 repo,用该 repo 的 tip:
  `cd <workspace>/<location_repo> && git worktree add --detach /tmp/marshal-<location_repo>-tip $(git rev-parse origin/devnet 2>/dev/null || git rev-parse origin/main)`

### 2) 在 worktree 根跑 `inv.run_command`
worktree 根即该 repo 根,`run_command` 里的 `-p <crate>` / 路径都相对 repo 根,直接在 worktree 根执行即可。
- 可选加速:`CARGO_TARGET_DIR=<workspace>/<location_repo>/target inv.run_command` 复用主树编译缓存,省掉 worktree 全量重编。

### 3) 判读(标准不变)
- **退出 0 ≠ pass**。先看实际跑了几个测试:cargo 输出含 `running 0 tests` 或 `0 passed; …; N filtered out`(N>0)→ **该不变量记 degraded,绝不算 pass**。这是 `--exact` + 错误测试名/模块路径的静默假报陷阱:名字不匹配时 `cargo test … -- --exact` 退出 0 且"running 0 tests",naive 读 exit code 会把"没跑"当"审过"。必须确认 `test result: ok. ≥1 passed` 才算真 pass。
- **`running 0 tests` 的归因顺序**:① 先怀疑 checkout —— 确认你确实在上面的 worktree(而非主树)跑;若在主树跑出 0 tests,**先换 worktree 重跑**,别急着记 degraded。② 在正确 worktree 上仍 `running 0 tests` / 包名或模块路径不符(契约不变量本体可能未实现)→ 才记 degraded,提示"检查缺失或引用过时,建议用 /marshal ratchet 补或修正 pack 引用",**不当作 pass**。
- 真正跑了且失败(`test result: FAILED`)→ 该门禁 outcome=fail。
- 教训:pack 注册的 run_command 必须指向**真实存在、已验证能命中**的测试(正确包名 + 全模块路径);注册前先在目标 repo 实跑确认 `≥1 passed`。

### 4) 清理
跑完该 repo 的全部不变量后:`git worktree remove --force /tmp/marshal-<...>`。

## 核对 Almanax 已贴 findings(PR 模式出判决前**必做**)

Almanax 是独立的第三方扫描器,常在 Marshal 跑之前就把 finding 贴上了 PR。**绝不凭印象写「Almanax: 0 findings」** —— 必须实拉再断言(node #660 即栽于此:Almanax 的 HIGH 早 4 分钟已 live,Marshal 却报「skipping / 0 findings」并判干净 PASS,违反「降级不谎报」)。Marshal 自己的对抗 review 发现了同一 bug ≠ 可以无视 Almanax 的判决态;两者是互相印证,不是替代。

1. **实拉**(两个端点都要,finding 可能在 review-comment 或 review body 里):
   - `gh api repos/cowboyinc/<repo>/pulls/<PR>/comments --jq '.[] | select(.user.login=="almanax-ai[bot]")'`
   - `gh api repos/cowboyinc/<repo>/pulls/<PR>/reviews  --jq '.[] | select(.user.login=="almanax-ai[bot]")'`
   - severity 从 body 解析(`alt="High Severity"` / `Critical` / `Medium` / `Low`);看是否已被 `/almanax dismiss|resolve`(查后续回复或 `almanax_finding_id` 状态)。
2. **核对**:对每条**未 dismiss/resolve** 的 Almanax finding,Marshal 必须在评论里**逐条 confirm 或 refute**(回代码一手核;refute 要给证据,别空口反驳)。
3. **判决约束(确定性,不靠模型裁量)**:存在任一未 dismiss 的 **HIGH/CRITICAL** Almanax finding → 判决**不得是干净 PASS**,至少 needs_human(高危终审归人)。只有 Marshal 拿出一手证据 refute 掉(证明是假阳性),才能降到 pass,且评论里写明 refute 依据。
4. 评论里「Almanax: N findings」**必须等于实拉计数**,并逐条列出 severity + 你的处置(confirmed / refuted-with-evidence / dismissed-upstream)。

## 汇总 GateDecision(verdict 优先级 block > needs_human > pass)
- 任一 active 不变量 fail → block
- 高危 tier + 确认的高 severity review 发现 → needs_human
- **存在未 dismiss/refute 的 HIGH/CRITICAL Almanax finding → 至少 needs_human**(见上节;refute 须附一手证据才可降级)
- **change 自评/升为 consensus-relevant tier,且 review 确认其声称的安全不变量仍可被绕过 → needs_human**,不得用「defense-in-depth / correctly scoped」措辞发干净 PASS(PR 可作增量合入,但判决要标注「<不变量> 仍开放」)
- 任一步骤 degraded(CLI 错/测试缺/review 超预算)→ 至少 needs_human + 标 degraded
- 否则 → pass

## 落库与回写
- `"$PY" -m marshal_core.cli gate-record --change-ref <ref> --verdict <v> --evidence-json '<gates JSON>'`
- 有 PR# 且用户要:把发现贴成 PR 评论(可借 `/code-review ultra` 的 --comment)。
  **所有 GitHub 评论一律用英文**(PR comment / review / 描述);终端给用户的摘要仍按对话语言(中文)。
  **每条 GitHub 评论结尾必须逐字加这一行声明**(建议用 `<sub>...</sub>` 小字):
  `Generated by Marshal (risk-tiering + invariant gate + adversarial review). Advisory only.`
