# 操作手册：日常怎么跑

> 第一次落地见 `SOP.md` 的快速开始；这里写「装好之后每天怎么用」。
> 遇到报错先查 `TROUBLESHOOTING.md`，字段含义查 `CONFIG.md`。

## 0. 每次开工前

```sh
python scripts/check-docs.py
python scripts/check-structure.py
```

前者查文档引用与复述，后者查结构契约。两项都绿再开始改代码；红了先修，别带着已知的破契约往下做。

## 1. 一轮改动的完整循环

```
改代码
  → python scripts/gate.py              ① 项目命令 + 文档 + 结构 + 模型
  → ② 独立审核（见下一节）             换模型 subagent 审 diff
  → python scripts/pre-ci-review.py check
  → python scripts/gate.py              全绿
  → git commit                          钩子再跑一遍门禁
  → git push                            CI 四段
  → ④ findings 处置（见第 4 节）
```

关键点：**gate 与 ② 是两次不同性质的事**。gate 是确定性的（命令、契约），② 是判断性的（换模型找盲区）。两者都过才提交。

## 2. ② 独立审核（每批改动都要做）

### 2.1 取 diff

```sh
# 未提交的改动
git diff --no-color

# 相对主干的改动（多提交时用这个）
git diff --no-color origin/main...HEAD
```

**注意未跟踪文件**：`git diff` 不显示还没被 Git 认识的**新文件**。新增文件要让审核看到，两种做法：

```sh
git add -N <新文件>     # 只登记意图，不改内容；之后 git diff 就能看到
# 或者
git add -A && git diff HEAD   # 全部暂存后与 HEAD 比较
```

漏掉这一步，审核者会把「引用了新文件」误判成「引用了不存在的文件」。

diff 控制在 60KB 以内；太大就按模块拆成多次审核，别指望一次看完。

### 2.2 派审核 subagent

- 模型：取 `sop.json` 的 `review.preCiModel`，**必须与主会话模型不同**；
- prompt：用 `REVIEW-PROMPT.md` 的「通用骨架（可直接复制）」，把 diff 附在最后；
- 要求它输出十字段 YAML，最多 10 条。

在 DSH 里就是一次 `subagent` 调用，参数里显式写 `provider` 与 `model`。

**如果 subagent 只有与主会话相同的模型**：② 会退化成同源审核，等于没做。这时改用外部 API 审核（`scripts/ai_review.py` 就是现成例子：它调 DeepSeek 的 `deepseek-chat`，与主会话的模型不同），把产出作为 ② 的 findings 落盘。只要 `record --model` 记录的是实际参与审核的模型即可；汇报时说明用的是哪种方式。

### 2.3 落盘与记录

```sh
# 1) 把子代理返回的 YAML 原样写入（不许改写、删条目、降级）
#    .review/pre-ci-findings.yaml

# 2) 记录证据（--model 填实际使用的模型 ID）
python scripts/pre-ci-review.py record --model <具体模型ID>

# 3) 立刻校验
python scripts/pre-ci-review.py check

# 4) 再跑完整门禁
python scripts/gate.py
```

`check` 返回 0 之前不要提交、不要汇报完成。

### 2.4 常见分支

| 情况 | 处理 |
|---|---|
| findings 里有 `important` | 先修，或明确升级给人；`important` 只能修或交人，不许自行关闭 |
| 修完代码后再 `check` 变红 | 正常：证据要覆盖当前内容，重新审核并 record |
| 只有 `nit` / `pre-existing` | 写清理由后可关闭，记录在回复里 |
| 产物格式不合法 | 按 `REVIEW-PROMPT.md` 的十字段重写，不要手改 parser |

### 2.5 轮次与收敛

② 不是无限重审：第 1 轮全量，之后只看增量；上限与「仍有 important 就升级给人」的规则见 `SOP.md` 的「② 的收敛规则」。

每轮开始前，把上一轮没解决的 findings 写进 `.review/open-findings.yaml`，审核时告诉它不要重复报；已知问题由 `pre-ci-review.py check` 数着，不会被忘掉。

到顶后由**人**裁决是否接受某条 important，写进 `.review/waivers.json`：

```json
{"waivers": [
  {"id": "compat-1", "reason": "影响面只限诊断输出，不改门禁行为", "approved_by": "用户"}
]}
```

`check` 会扣除这些条目并打印「已豁免 N 条」；agent 不得自行写 waiver。

## 3. 日常循环的边界

- **一次只做 `plan.md` 里的一个增量**，做完立即过门禁，不攒到最后。
- **修一类问题先搜全量**：同一模式改一处必然漏，先 `grep` 出全部位置再批量改。
- **规则只改一处**：改 `REVIEW.md`/`AGENTS.md`/`SOP.md` 后跑 `check-docs.py`，它会抓复述与断链。

## 4. ④ 三视角审核的结果怎么处置

CI 的 `ai-review` job 全绿不等于没问题——**④ 不阻断 CI**，它只产出 findings 并记账。处置在本地：

```sh
# 看本轮合并结果
cat .review/merged.yaml        # 合并去重后的十字段
cat .review/summary.json       # 统计、维度冲突
cat .review/important-count    # important 条数

# 看轮次状态
python scripts/review-state.py check
```

- 有 `important`：逐条修，或明确升级给人；不许「装没看见」。
- 修完并重新推送后进入下一轮；轮次怎么推进、到顶怎么停见 `SOP.md` 的「轮次与刹车」。
- 到顶后要复位：**本地 reset 改不到 CI 的 cache**，用仓库的 `workflow_dispatch` 复位 job（Actions 页 → 该 workflow → Run workflow，填原因）。

## 5. API Key 轮换

1. 新 key 先在本地验证一次（直接调 API，返回 200 再继续）；
2. 仓库 Settings → Secrets and variables → Actions → 更新 `CI_AGENT_TOKEN`；
3. Actions 页找到失败/旧 run → **重新运行所有作业**（不是重试旧 job）；
4. 确认三个视角都产出 findings 再收工。

旧 key 立即作废；不要把 key 写进任何仓库文件或文档。

## 6. 迁移与备份

换机器或换仓库时，需要重建的东西只有四样：

| 内容 | 做法 |
|---|---|
| 仓库内容 | 直接 clone；模板由 `template/` 复制而来 |
| Secrets | 新仓库重新配 `CI_AGENT_TOKEN` |
| 分支保护 | 新仓库重新加 required checks |
| 轮次状态 | 不需要搬：CI cache 按分支隔离，新仓库从第 1 轮开始 |

本地只需要：`git config core.hooksPath .githooks` + 配置代理（如果直连不稳定）。

## 7. 收尾汇报模板

```
改动：<对应增量与 spec 的实际变化>
门禁：全绿（N 项）/ X 失败
② 审核：<模型> 审过，important=N（已修/升级）
未验证项：<逐项说明原因，没有就写「无」>
```

不写「应该没问题」「待会再修」——没跑就是没跑。
