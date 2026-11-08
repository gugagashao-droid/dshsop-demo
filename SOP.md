# SOP — 流程与边界

> 被复制到项目根之后，这份就是**流程的唯一出处**。`AGENTS.md` 管怎么写，`REVIEW.md` 管查什么，这里管怎么跑。

## 四段流水线（唯一出处）

四段串联，顺序不可换。**前一段没有证据，后一段不许开始。**

| 段 | 位置 | 做什么 | 落点 |
|---|---|---|---|
| ① | 本地 / 提交前 | 格式化、Lint、静态类型、单测、基础集成测试 | `scripts/gate.py` 的项目命令段 + 结构契约 + 文档一致性 |
| ② | 本地 / 提交前 / CI 之前 | 全新上下文独立审核：换模型，消除编码 agent 的思维惯性盲区 | 审核 subagent → `.review/pre-ci-findings.yaml` → `scripts/pre-ci-review.py` 落盘证据 → `scripts/gate.py` 校验 |
| ③ | CI | 静态检查、全量单元/集成测试、多平台构建、兼容性、依赖安全扫描、基础性能基准 | `.github/workflows/sop.yml` 读 `ci.json` → `scripts/ci-run.py` |
| ④ | CI 之后 | 多 Agent 标准化审核：spec / compat / impact 三视角 | `ci.json` 的 `ai` 阶段 → `scripts/merge-findings.py` + `scripts/review-state.py` |

两条边界，只在这里写一次：

- **② 与 ④ 不是同一道关**：② 是单 agent、CI 前、靠换模型破盲区；④ 是三个视角、CI 后、靠交叉。两者不共用模型、不共用触发入口。
- **自动判断不构成调用**：agentic 子代理由主 agent 按 `description` 判断是否调用，这是判断而非保证。所以 ② 有三层：skill 纪律（`.agents/skills/sop-gate/SKILL.md`）、`scripts/gate.py` 的证据校验、`.githooks/pre-commit` 最终拦截。④ 由 CI 的 `ai` 阶段调用。

审核 subagent 的 prompt 模板见 `REVIEW-PROMPT.md`；② 的完整命令序列与日常循环见 `OPERATIONS.md`。

**DSH 与 CodeBuddy 的触发差异（必读）**：DSH 没有 CodeBuddy 的 `Stop` hook，无法在「回合即将结束」时阻塞并要求继续。因此 ② 的**确定性保证来自门禁与提交钩子，而不是回合拦截**。这是本移植版最弱的一环，靠 CI 抓漏。

## 增量闭环

按「渐进增强」推进，**一次只做 `plan.md` 中的一个增量**：

1. 核对该项对应的 spec、唯一用户可观察行为，以及 `ARCHITECTURE.md` 中相关的模块所有权、依赖与边界；模块或接口变化时先更新架构契约。不并行开始其他增量。
2. 先写该项测试并运行，记录预期失败；确认先红后再做最小实现，直到测试变绿。
3. 跑门禁，保留实际命令与结果；失败就修复当前增量，不扩大范围。
4. 更新 `plan.md` 的增量状态与「进度与交接」，写清最近验证证据、下一步、阻塞与未验证项，再选择下一个增量。

### 新会话接手

先读 `intent.md`、`spec.md`、`ARCHITECTURE.md`、`plan.md`，再运行 `python scripts/pre-ci-review.py check`；以文件和命令结果为准，不依赖上一会话的记忆。

### 最终汇报

最终汇报必须包含：

- **改动**：对应增量与 spec 的实际变化。
- **验证证据**：实际运行的命令及结果，不声称未运行的检查已通过。
- **未验证项**：逐项说明原因与后续动作；没有则明确写「无」。

## 授权边界（谁决定继续）

**默认连续执行。** 一次审核收回 findings 后，按严重度修完 → 跑门禁 → 直接派下一轮，中间不停下来征求同意。

**只有这四种情况停下问人**：修复会改变对外行为；要删除功能或文件；规格有缺口必须先改 `spec.md`；到顶（仍有 `important` 且额度用完）。

判断标准只有一句：**这个决定如果做错了，能不能一次 revert 干净地回去？** 能→自己决定；不能→停下来问。

## 结构契约

`ARCHITECTURE.md` 是项目结构的唯一规范来源；本阶段是进入实现前的硬出口：

1. 填完项目环境、模块、依赖、边界、扩展点和架构不变量；模块的根路径必须真实存在。
2. 在「结构检查」登记一个项目自己的结构检查命令；它必须实际检查至少一个结构不变量，不能是空操作。
3. 运行 `python scripts/check-structure.py`，必须返回 0。
4. 新增目录、模块、跨模块依赖或公开接口时，先建立可识别的最小入口骨架并更新 `ARCHITECTURE.md`，重跑检查通过后再写业务实现。

## ② 的收敛规则

② 是判断型审核，不是穷举型检查。没有收敛规则，就会陷入「修一处、报一处」的循环；但收敛不等于放弃把关，而是把「漏」变成明确的「交给人」。

- **第 1 轮全量**：审全部当前改动。
- **第 2 轮起只看增量**：只审「上一轮审核之后」的 diff，历史部分不再重扫。
- **最多 2 轮**：第 2 轮仍有 `important`，停手升级给人，由人决定继续修、放宽口径还是接受。
- **三种情况强制回到全量**：改动涉及规则/契约文件（`SOP.md`、`REVIEW.md`、`AGENTS.md`、`ARCHITECTURE.md`、`spec.md`、`intent.md`）；上一轮证据覆盖的文件又被修改；审核者换了模型或 prompt。
- **维护已知问题清单**：每轮把未解决的 findings 记进 `.review/open-findings.yaml`，下一轮审核前附上「不要重复报」，既减噪音也不遗忘。
- **升级给人的落点**：人在 `.review/waivers.json` 写一条豁免（`id` + `reason` + `approved_by`），`check` 扣除该条并在输出里列出；agent 不得自行写 waiver，否则等于自己给自己放行。
- **判级硬化**：`important` 必须写出「什么操作 → 什么错误」；写不出具体场景的，按 `REVIEW.md` 的判据降为 `nit`。

规则只在这里写一次：`OPERATIONS.md` 只给执行步骤，`REVIEW-PROMPT.md` 只给 prompt 模板。

## 轮次与刹车

本节只管 **CI 之后的 ④ 段**；CI 前的 ② 段由独立审核与证据校验负责，不走轮次。

- **每次只看新增。** 第 2 轮起只看上一轮之后的新增 diff，不重扫全量——否则每轮都翻出新的存量问题，流程永不终止。
- **CI 后交叉审共 3 轮额度：** 第 1 轮 full → 第 2 轮 narrow → 第 3 轮 incremental。
- **第 3 轮仍有 `important` 就停手**，回看 `spec.md` / `plan.md`：多数时候是思路有方向性偏差，不是漏了几个 bug。
- **第 3 轮即使已通过，之后代码再变也必须 `reset` 开新分支**；第 4 轮记录会被拒绝。
- **只允许自动重置一次。** 自动重置用完后再次到顶，`check` 返回 4；先复盘并写台账，再由人运行 `reset --force`，agent 不得自行执行。

## 到顶之后

第 3 轮仍有 `important` 时，按顺序做：

1. **先写台账行**（见「事后复盘与规则迭代」）。这一步不能省——`reset` 之后 `check` 就不再返回 3，触发条件会消失。
2. **停手，只做删除与合并**：抽公共实现、删无人调用的导出、删死分支。不新增功能、不改表现层、不改对外文案。
3. `python scripts/review-state.py reset` —— 当前轮次归档进 `history`，清空当前轮次。
4. 新分支从第 1 轮 full 重新审。

**CI 里的到顶恢复**：`.review/state.json` 存在 Actions cache 里，本地 `reset` 改不到它。所以 `.github/workflows/sop.yml` 留了一个 `reset-state` 的 `workflow_dispatch` 出口：复盘并写好 `LESSONS.md` 后，在 Actions 页面手动跑一次，它恢复 cache → `reset --force` → 用新 key 保存。没有这个出口，流水线会永久卡红。

## 事后复盘与规则迭代

### 什么时候必须复盘

| 触发 | 说明 |
|---|---|
| `check` 返回 3 / 4 | 说明前几轮修的是症状，不是根因 |
| 一次事故 | 不管是不是代码引起的 |
| 同一类 finding 出现 ≥ 2 次 | 高频漏报 / 高频无效意见 |
| 人工批准 `reset --force` 之前 | 没复盘不给重开 |

### 产物三条，缺一条不算做完

1. **台账一行**（`LESSONS.md`）：现象、根因、改了哪个文件、**怎么验证不再犯**
2. **至少一处规则更新**：`REVIEW.md`（审什么）/ `AGENTS.md`（怎么写）/ 本文件（怎么跑）
3. **一条可执行的验证**：能失败的 grep / 测试 / gate 一步

### 三个月没被触发的规则，删掉

在 `LESSONS.md` 标注「已过期」。规则文件只会变长不会变短，是这类文档最常见的死法。

## 命令速查

| 目的 | 命令 |
|---|---|
| 本地门禁（完整） | `python scripts/gate.py` |
| 本地门禁（只跑一致性检查） | `python scripts/gate.py --checks-only` |
| CI 门禁（不校验本地证据） | `python scripts/gate.py --ci` |
| 结构检查 | `python scripts/check-structure.py` |
| 文档一致性 | `python scripts/check-docs.py` |
| 模型分离 | `python scripts/check-model-separation.py` |
| CI 前审核证据校验 | `python scripts/pre-ci-review.py check` |
| 记录 CI 前审核证据 | `python scripts/pre-ci-review.py record --model <模型 ID>` |
| 该跑第几轮（CI 后） | `python scripts/review-state.py check` |
| 记录本轮结果 | `python scripts/review-state.py record --round N --important M --axes spec,standards` |
| 合并三份 findings | `python scripts/merge-findings.py merge --inputs .review/spec.yaml .review/compat.yaml .review/impact.yaml --outdir .review` |
| 算出下一轮 | `python scripts/review-state.py next-round --out .review/next-round` |
| CI 阶段编排 | `python scripts/ci-run.py --stage ai` |
| 脚本自检 | `python scripts/gate.py self-test` 等五个脚本 |

判定规则与退出码分别见各脚本文件头，这里不复述。

## 已知限制（不要吹成已解决）

- **② 的自动触发是弱保证**：DSH 无回合拦截钩子；只靠 skill 纪律 + gate + 提交钩子。愿意投入的话，可以写一个 Cordis 插件挂 `agent/turn-stopping` 或 `tools/pre-execute`（拦 `git commit`），但那是开发项。
- **CI 不校验本地证据**：`--ci` 模式跳过 `.review/pre-ci.json`（证据只在本地）。绕过 `--no-verify` 后 CI 仍可能绿；要堵这个口，需要把证据变成可提交/可验证的产物。
- **模型分离只验证配置与证据一致性**：模型被可用列表拦截时会静默回退到主会话模型，脚本证明不了运行时真的换了模型。
- **GitHub Actions 未经真实仓库验证**：`ci.json` 的 `ai` 段仍是占位符，`actions/*` 暂用 tag，上线前应 pin 到 commit SHA。
