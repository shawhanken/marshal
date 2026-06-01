# Marshal 文档

Marshal —— 通用质量工程平台。核心**领域无关**,把任意项目的质量工程内容收拢为可插拔的**领域包(Domain Pack)**;Cowboy 是第一个领域包,而非平台的绑定对象。

> 项目名释义:**Marshal**(西部法官——为质量执法、守住多 repo 边疆;technical 双关:*marshalling* = 跨语言序列化契约,正是平台内核之一)。

## 文档地图

| 文档 | 内容 | 读者 |
|---|---|---|
| [方法论 · AI 高速研发下的质量工程](methodology/ai-velocity-quality-methodology.zh.md) | **为什么**:三个结构性不对称 + 三支柱(可执行不变量 / 风险分级 / 逃逸棘轮)+ 两包裹(AI 对抗式 review / 运行时纵深防御)+ 度量。Marshal 的理论根基。 | 全体 |
| [方法论 · 宣讲页(HTML)](methodology/ai-velocity-quality-methodology.html) | 同上的可投屏单页演示版(深色 + 数据可视化;浏览器直接打开)。 | 对内宣讲 |
| [架构设计 · 平台总体蓝图](architecture/platform-architecture-design.zh.md) | **怎么建**:领域无关核心 + 领域包契约;模块化单体大脑 + 无状态执行器 + 知识核脊椎;7 子系统映射、数据流、分层规格体系、领域知识获取、演进顺序。 | 工程/架构 |

## 阅读路径

1. 先读**方法论**(或看 HTML 宣讲页)建立心智模型——理解「为什么人工 review 必败、三支柱如何破局」。
2. 再读**架构设计蓝图**——理解 Marshal 如何把方法论落成可建造的系统。
3. 各子系统的可开工详细 spec(implementation plans)放 [`plans/`](plans/)。首个切入点见 [`plans/2026-06-01-walking-skeleton-econ-slice.md`](plans/2026-06-01-walking-skeleton-econ-slice.md) —— 平台 walking skeleton + 经济守恒不变量竖切(已实现,见分支 `feat/walking-skeleton-econ`)。

## 文档约定

- `*.zh.md` 中文;后续如出英文版用 `*.en.md`。
- 架构蓝图为 **living spec**,版本记录见文档末尾「修订记录」。
- 本目录文档由 Cowboy 工作区(`refs/`、`docs/superpowers/specs/`)整理迁移而来,marshal 仓库为权威副本。
