# 故障排查手册

> 只收**真实踩过的**报错与误判。每条按「现象 → 原因 → 修法」写，命令可直接复制。
> 轮次与到顶处置见 `SOP.md` 的「轮次与刹车」，这里不重复。

## 一、网络与代理

| 现象 | 原因 | 修法 |
|---|---|---|
| `Could not connect to server` / 22 秒后超时 | git 没走代理 | 见下方「配代理」 |
| `curl -x http://127.0.0.1:<port> https://github.com` 返回 000 | 代理端口不对或代理没开 | 先找端口：Clash Verge 一般在 `设置 → 端口`；常见 7890 / 7897 |
| 浏览器能开 GitHub，`git push` 却超时 | 浏览器走了系统代理，git 没走 | 给 git 单独配代理 |

配代理（只对 github.com 生效，避免影响内网）：

```sh
git config --global http.https://github.com.proxy http://127.0.0.1:7897
git config --global https.https://github.com.proxy http://127.0.0.1:7897
git config --global --get-regexp proxy   # 复查
```

删除代理：

```sh
git config --global --unset http.https://github.com.proxy
git config --global --unset https.https://github.com.proxy
```

## 二、Git 认证与推送

| 现象 | 原因 | 修法 |
|---|---|---|
| `could not read Username for 'https://github.com'` | 没有可用凭据，且终端无法弹窗 | 换到有界面的终端手动 `git push` 一次，走完 Git Credential Manager 登录 |
| `fatal: User cancelled dialog` | GCM 登录窗口被取消 | 重新 `git push`，在弹窗里点「Sign in with your browser」 |
| 弹窗没出现 | 被其他窗口挡住 | 看任务栏的 Git Credential Manager 窗口 |
| 推送后要求邮箱验证码 | GitHub 敏感操作触发了 sudo 二次验证 | 去邮箱取验证码；没有收到就检查垃圾邮件 |
| `Repository not found` | 仓库地址拼错，或没授权该账号 | 核对 `git remote -v`；确认浏览器登录的是同一个账号 |

凭据存好之后，后续 push 不再弹窗。

## 三、本地门禁（`scripts/gate.py`）

| 现象 | 原因 | 修法 |
|---|---|---|
| `GATE FAIL: 没检测到 package.json / pyproject.toml` | 项目没有可识别的构建文件，`sop.json` 也没填 `commands` | 在 `sop.json` 里填 `commands`，见 `CONFIG.md` |
| `GATE FAIL: 没有任何一步真正执行` | 所有步骤都被跳过或未配置 | 检查命令是否装了；`sop.json` 是否只填了 `build` |
| `文档一致性` 红，提示「引用了不存在的 X」 | md 里写了不存在的路径 | 改成真实路径，或删掉引用 |
| `结构契约` 红，提示「未填写、仍是占位符」 | `ARCHITECTURE.md` 还是模板 | 按 `REVIEW.md` 同目录的模板逐节填，见 `CONFIG.md` |
| `结构契约` 红，提示「命令仍是占位符」 | 没登记结构检查命令 | 在 `ARCHITECTURE.md` 的「结构检查」填 JSON 数组 |
| `结构契约` 红，提示「看起来是空操作」 | 嵌入了 `-c pass` 这类内联代码 | 写一个真实的检查脚本，见 `CONFIG.md` |
| `模型分离` 红，提示「不是具体模型 ID」 | `sop.json` 的 review 仍是占位符 | 填两个不同的具体模型 ID |
| `CI 前独立审核` 红 | 还没做 ② | 见「五、② 独立审核」 |

## 四、提交钩子（`.githooks/pre-commit`）

| 现象 | 原因 | 修法 |
|---|---|---|
| 提交时没跑门禁 | `core.hooksPath` 没设 | `git config core.hooksPath .githooks` |
| 钩子报 `No such file or directory` | 钩子没有执行权限（macOS / Linux） | `chmod +x .githooks/pre-commit` |
| 提交被拦，只红在「CI 前独立审核」 | 设计如此 | 先走 ②，或对纯普通文档改动由钩子自动跳过 |
| 只想临时跳过 | 明确知道风险时 | `git commit --no-verify`，但这会绕过 ② 与门禁，CI 仍可能绿——不要当常规操作 |

## 五、② 独立审核

| 现象 | 原因 | 修法 |
|---|---|---|
| `没有审核产物 .review/pre-ci-findings.yaml` | 还没落盘 findings | 先跑审核 subagent，把 YAML 原样写进去 |
| `审核产物格式不合法` | 字段缺失、缩进错误、多了字段 | 按 `REVIEW-PROMPT.md` 的十字段格式重写 |
| `审核之后代码又变了，证据不再覆盖当前改动` | record 之后又改了文件 | 重新审核并 record；如果只是工具缓存，检查是否命中了已知忽略清单 |
| `还有 N 条 important 未处置` | findings 里有 important | 先修或升级给人，再 record |
| `证据里的 model='inherit' 不是具体模型 ID` | record 时用了占位词 | `--model` 填具体模型 ID |
| `record` 拒绝：`model='x' 不是具体模型 ID` | 同上 | 同上 |
| `豁免文件不可读` | `.review/waivers.json` 格式错或缺 `reason` / `approved_by` | 按 `OPERATIONS.md` 2.5 的格式补全 |
| `提示：N 条豁免未匹配当前 findings` | 豁免的 id 在本轮 findings 里不存在（可能已修或重新生成） | 正常提示，不阻断；确认是否该删掉过期豁免 |
| 子代理返回的 YAML 被吞或格式乱 | 模型直接写 YAML 缩进不稳 | 让它输出 JSON，再按模板转 YAML（`scripts/ai_review.py` 就是这么做的） |

一次完整的 ② 命令序列见 `OPERATIONS.md`。

## 六、GitHub Actions

| 现象 | 原因 | 修法 |
|---|---|---|
| push 后 Actions 没有任何 run | 仓库禁用了 Actions，或改动没碰触发分支 | Settings → Actions → General → 允许；确认改的是 `main` 或开了 PR |
| workflow 立刻失败，日志只有一行 | YAML 语法错或文件不在 `.github/workflows/` | 看 Actions 页的「Workflow file」链接 |
| `Cache save failed: Path(s) ... do not exist` | 该 job 没产出 `.review/state.json` | 正常提示；④ 成功后会生成 |
| 同一提交出现两条 run | 同时挂了 push 与 pull_request | 只保留一种触发，见 `SOP.md` 的流水线段 |
| 三平台里只有 windows 红 | 路径分隔符 / 换行符 / 编码差异 | 本地用 Windows 复跑同一命令 |
| Actions 分钟数用得快 | 私有仓库 + 三平台矩阵 | 公开仓库免费；或减少矩阵 |

## 七、④ 三视角审核

| 现象 | 原因 | 修法 |
|---|---|---|
| `HTTP Error 402: Payment Required` | API key 余额不足 | 充值或换 key，然后更新 Secret |
| `HTTP Error 401` | key 错误或已失效 | 更新 Secret 里的 `CI_AGENT_TOKEN` |
| `缺少 CI_AGENT_TOKEN / DEEPSEEK_API_KEY` | Secret 没配，或名字不一致 | Settings → Secrets and variables → Actions 核对 |
| `取不到 diff（CI 里需要 fetch-depth: 0`） | checkout 用了浅克隆 | 在 `ai-review` job 的 checkout 加 `fetch-depth: 0` |
| `命令仍是占位符或未填` | `ci.json` 的 ai 段没换成真实命令 | 见 `CONFIG.md` |
| 三视角都返回空 findings | diff 太小，或 prompt 没给上下文 | 确认 diff 非空；检查 prompt 是否带了 `REVIEW.md` 与契约 |
| 更新 Secret 后重跑仍失败 | 旧 run 用的是旧 Secret | 重跑要用「重新运行所有作业」，不是原样重试旧 job 缓存 |

## 八、分支保护与 Secret

| 现象 | 原因 | 修法 |
|---|---|---|
| 想开分支保护但只有「规则集」 | 新仓库默认推荐 rulesets | 经典分支保护仍可用；两者选其一 |
| required checks 搜不到 | 还没有一次运行产生过该 check | 先让流水线跑一次，名字才会出现在下拉里 |
| 私有仓库开不了分支保护 | 免费计划仅公开仓库支持 | 换公开仓库，或升级计划 |
| 改了 Secret 但 CI 没生效 | Secret 只在新建的 run 里注入 | 重新运行工作流 |

## 九、这些不是问题（常见误判）

- `gate.py` 首次运行是红的：**设计如此**，红才说明它在检查。
- hook 拦提交：**设计如此**，说明 ② 没做。
- CI 里 `--ci` 跳过「CI 前独立审核」：证据只在本地，CI 无法验证（已知限制）。
- `④` 有 important 但 CI 绿：④ 不阻断 CI，重要问题要靠本地 `review-state.py check` 处置。
- `Cache save failed`：不影响结论，最多让下一轮从头算起。
