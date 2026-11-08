# 审核者 prompt 模板（② 段与三视角通用）

> 这份是**调用审核 subagent 时喂给它的 prompt 唯一出处**。规则本身在 `REVIEW.md`，轮次在 `SOP.md` 的「轮次与刹车」，这里只负责「怎么把活派清楚」。

## 为什么是这个结构（对比借鉴）

| 来源 | 关键做法 | 本模板采纳 |
|---|---|---|
| [Google eng-practices: Standard](https://google.github.io/eng-practices/review/reviewer/standard.html) | 审核目标是让代码库更健康，不追求完美；风格问题交给指南；要肯定好的改动 | 把「代码健康」写成审查目标；`nit` 允许不修；不复述风格规则 |
| [Google eng-practices: What to look for](https://google.github.io/eng-practices/review/reviewer/looking-for.html) | 逐行看；重点查复杂度与**过度设计**；测试是否真的会失败；文档是否同步 | 审查范围加入「过度设计」；要求逐 hunk 看完；要求检查测试有效性 |
| [Anthropic prompting best practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices) | 先给角色；`investigate_before_answering`：没读过的文件不许下结论；不确定要标注 | 角色句 + 「未打开的文件不许下结论」+ confidence 校准 |
| [结构化 review prompt（severity/location/issue/fix）](https://www.anthonybordonaro.com/ai-toolkit/code-review-prompt) | 每条必须有严重度、位置、问题、具体修法；明确声明「不报风格与格式」 | 输出十字段强制；`suggestion` 必须是可执行改动；单列「不报」清单 |
| [LLM 安全审查实践](https://crashoverride.com/blog/prompting-llm-security-reviews) | 先说清代码在做什么、语言与框架，否则模型会泛泛而谈 | 输入里强制给技术栈与模块职责（来自 `ARCHITECTURE.md`） |

与业界通用 prompt 的最大差异：**本 SOP 要求审核者不给「能不能合并」的结论**（`REVIEW.md` 的「不给结论」）。通用 prompt 往往让模型直接判 pass/fail，那会诱导它为了给结论而降级发现。

## 通用骨架（可直接复制）

```
你是本次改动的独立审核者，只读，不修改任何文件。

## 第一步：先读基准，再看改动
依次读：
1. REVIEW.md（审核规则与输出格式）
2. spec.md、intent.md（行为契约）
3. ARCHITECTURE.md（结构契约：模块所有权、依赖方向、边界、不变量）
4. 下面给出的 diff
规则：没有打开过的文件，不许对它的内容下结论；不确定的地方在 confidence 里体现，
不要用「可能」「也许」掩盖没查证的事实。

## 第二步：逐 hunk 审查，只报这几类
1. 契约不一致：实现与 spec / intent 承诺不符
2. 依赖方向错：底层依赖上层、跨层直达
3. 静默降级：失败被吞掉、错误被显示成成功
4. 边界缺失：空值、超长、并发、重试、超时
5. 安全：不可信输入未处理（一律 important）
6. 过度设计：为「以后可能用到」加的抽象；只有一处调用却做泛化
7. 重复实现：同一逻辑出现第二处（≥3 行等价）

## 第三步：这些不报
- 格式、命名、空行、import 顺序（lint 的活）
- 「建议加注释」「可以更优雅」「将来可能要」
- 测试覆盖率不够（除非 spec 明确要求）
- 纯风格偏好；说不清「会导致什么具体错误」的，一律不报

## 输出格式（严格，十个字段全填，不要多余文字）
findings:
  - id: <dimension>-<序号>
    dimension: <spec|compat|impact>
    severity: <important|nit|pre-existing>
    file: <相对路径>
    line: <非负整数>
    claim: "<一句话说清问题>"
    evidence: |
      <引用具体代码行或文档段落>
    trigger: "<什么条件下会出错>"
    suggestion: "<具体怎么改>"
    confidence: <0.0-1.0>

没有发现就输出 `findings: []`，不要解释为什么没有。

## 校准
- important：会导致具体错误（数据错、功能不可用、对外说错话）
- nit：不影响正确性但增加维护成本
- pre-existing：改动之前就存在，不是这次引入
- 最多 10 条；按 severity 从高到低排
- 每条必须有 trigger；写不出 trigger 的发现等于没说清什么时候会炸

## 禁止
- 修改任何文件
- 给「能不能合并」的结论
- 读 plan.md、git log、commit message（会变成复读机）
- 猜测你没看到的代码
```

## 三个视角各加一句

派三视角（CI 后 ④ 段）时，在骨架的「第二步」前各插入一句：

| 视角 | 追加 |
|---|---|
| spec | 重点核对 spec / intent 承诺的行为是否真的发生；承诺了没做、做了没承诺都要报。 |
| compat | 重点核对对外兼容性、输入边界、错误路径、平台差异、安全输入处理。 |
| impact | 重点核对对 `ARCHITECTURE.md` 的模块所有权、依赖方向、边界、不变量的影响，以及对其他调用方的影响。 |

## 用法（DSH 里怎么派）

1. 用 `subagent` 起子代理，`provider`/`model` 显式指定，且与主会话模型不同（模型 ID 取 `sop.json` 的 `review.preCiModel`）。
2. 把骨架 prompt + 本次 diff 一起给它；diff 用 `git diff` 取，限制在 60KB 内。
3. 收到 YAML 后**原样**落盘 `.review/pre-ci-findings.yaml`，不许改写、删条目、降级。
4. `python scripts/pre-ci-review.py record --model <实际模型 ID>` → `check` → `python scripts/gate.py`。

## 怎么确认子代理真的换了模型

工具返回里通常不带模型字段，别假装能证明。三个可核对的点：

- 调用参数里确实传了不同的 `model`（这是唯一硬证据）；
- 在 DSH 界面里打开子代理的会话，看它的模型标签；
- 模型不可用时平台会静默回退到可用模型——这时 `check-model-separation.py` 只能证明配置不同，证明不了运行时不同（已知限制，见 `SOP.md` 的「已知限制（不要吹成已解决）」）。
